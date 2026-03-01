# ================= BOT V294 (FILTRO DE ELITE + TRAVA ANTI-JOGO VELHO) =================
import os
import logging
import asyncio
import threading
import httpx
import feedparser
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Conflict, NetworkError

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= 🛡️ CACHE E DADOS =================
LIVE_STANDINGS = {}

def get_api_dates():
    """Pega a data REAL (Hoje)"""
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz)

def get_display_date_str():
    """Data Visual (2026)"""
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    try: data_fake = agora.replace(year=2026)
    except: data_fake = agora + timedelta(days=365)
    return data_fake.strftime("%d/%m/%Y")

# ================= MEMÓRIA =================
TODAYS_GAMES = []
TODAYS_NBA = []

# ================= 1. LISTA VIP DE TIMES (O FILTRO DE OURO) =================
# Só mostra jogos do Brasil se um desses times estiver em campo OU se for explicitamente Série A
VIP_TEAMS = [
    "Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Santos", "Grêmio", 
    "Internacional", "Atlético-MG", "Cruzeiro", "Botafogo", "Fluminense", "Vasco",
    "Bahia", "Fortaleza", "Athletico-PR", "Sport", "Ceará", "Vitória", "Remo", "Paysandu"
]

# ================= 2. ANÁLISE DE MERCADO =================
def calculate_dynamic_odd(probability):
    if probability <= 0: return 2.00
    fair_odd = 100 / probability
    return round(fair_odd + random.uniform(0.05, 0.15), 2)

def get_market_analysis(home, away, league_name):
    # Pesos
    h_weight = 50; a_weight = 30
    
    # Bônus para gigantes
    if any(t in home for t in VIP_TEAMS): h_weight += 30
    if any(t in away for t in VIP_TEAMS): a_weight += 25
    
    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    # Travas
    ph = max(20, min(ph, 85)); pa = max(15, min(pa, 80))
    confidence = max(ph, pa)
    
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    strategy_icon = "🎯"; strategy_name = "Análise Tática"
    
    if ph >= 70:
        pick = f"Vitória do {home}"; odd = calculate_dynamic_odd(ph)
        narrativa = f"O {home} é muito forte em casa."
        strategy_icon = "🛡️"; strategy_name = "Muralha em Casa"
    elif pa >= 65:
        pick = f"Vitória do {away}"; odd = calculate_dynamic_odd(pa)
        narrativa = f"O {away} é superior tecnicamente."
        strategy_icon = "🔥"; strategy_name = "Visitante Favorito"
    else:
        pick = "Over 1.5 Gols"; odd = 1.45
        narrativa = "Jogo equilibrado, tendência a gols."
        
    return pick, "Over 0.5 HT" if confidence > 70 else "Menos de 3.5 Gols", narrativa, f"{conf_bar} {int(confidence)}%", odd, strategy_icon, strategy_name

# ================= 3. MOTORES DE BUSCA (COM FILTRO ANTI-LIXO) =================

async def fetch_ge_data():
    """Motor Brasil (Globo Esporte) com Filtro VIP"""
    data_real = get_api_dates()
    data_str = data_real.strftime("%Y-%m-%d")
    url = f"https://api.globoesporte.globo.com/tabela/d1/api/tabela/jogos?data={data_str}"
    
    jogos = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code == 200:
                lista = r.json()
                for item in lista:
                    # 1. Checa se o jogo já acabou (Evita jogos de ontem que a API ainda mostra)
                    if item.get('realizado', False) is True: continue
                    if item.get('placar_oficial_mandante') is not None: continue 

                    home = item['equipes']['mandante']['nome_popular']
                    away = item['equipes']['visitante']['nome_popular']
                    camp = item.get('campeonato', {}).get('nome', '')
                    
                    # 2. FILTRO VIP: É Série A? OU Tem time grande?
                    is_serie_a = "Série A" in camp
                    has_big_team = any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)
                    
                    if not (is_serie_a or has_big_team): continue # Pula time bosta
                    if "Sub-" in camp or "Feminino" in camp: continue

                    jogos.append({
                        "match": f"{home} x {away}",
                        "home": home, "away": away,
                        "time": item.get('hora', '00:00'),
                        "league": f"🏆 {camp}",
                        "stadium": f"🏟️ {item.get('sede', {}).get('nome_popular', '')}"
                    })
    except: pass
    return jogos

async def fetch_espn_europe():
    """Motor Europa (ESPN) com Filtro de Status"""
    data_real = get_api_dates()
    data_str = data_real.strftime("%Y%m%d")
    leagues = ['uefa.champions', 'eng.1', 'esp.1', 'ita.1', 'ger.1', 'ksa.1']
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={data_str}"
                r = await client.get(url)
                data = r.json()
                l_name = data.get('leagues', [{}])[0].get('name', 'Futebol Int.')
                
                for event in data.get('events', []):
                    # 1. FILTRO DE STATUS: Só pega o que não acabou
                    status = event['status']['type']['state']
                    if status in ['post', 'ccc']: continue # Ignora Finalizado/Cancelado

                    comp = event['competitions'][0]['competitors']
                    home = comp[0]['team']['name']
                    away = comp[1]['team']['name']
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    jogos.append({
                        "match": f"{home} x {away}",
                        "home": home, "away": away,
                        "time": dt.strftime("%H:%M"),
                        "league": f"🏆 {l_name}",
                        "stadium": ""
                    })
            except: pass
    return jogos

async def fetch_all_soccer():
    br = await fetch_ge_data()
    eu = await fetch_espn_europe()
    todos = br + eu
    todos.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = todos
    return todos

# ================= 4. LAYOUT E ROTINAS =================
def format_card(game):
    pick, extra, narrativa, conf, odd, icon, sname = get_market_analysis(game['home'], game['away'], game['league'])
    return (
        f"{game['league']}\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ {game['time']}\n{game['stadium']}\n"
        f"🧠 <b>Estratégia:</b> {icon} {sname}\n"
        f"📝 <b>Análise:</b> <i>{narrativa}</i>\n"
        f"✅ <b>Palpite:</b> {pick}\n"
        f"🛡️ <b>Extra:</b> {extra}\n"
        f"📊 <b>Confiança:</b> {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

async def generate_daily_ticket(app):
    if not TODAYS_GAMES: return
    candidates = []
    for g in TODAYS_GAMES:
        pick, _, _, _, odd, _, _ = get_market_analysis(g['home'], g['away'], g['league'])
        if 1.35 <= odd <= 1.85: candidates.append({'match': g['match'], 'pick': pick, 'odd': odd})
    
    random.shuffle(candidates)
    ticket = candidates[:4] # Top 4 jogos
    
    if ticket:
        total_odd = 1.0
        msg = "🎫 <b>BILHETE DE ELITE (V294)</b> 🎫\n<i>Filtro: Apenas Times Relevantes</i>\n➖➖➖➖➖➖➖➖➖➖\n"
        for i, c in enumerate(ticket, 1):
            total_odd *= c['odd']
            msg += f"{i}️⃣ <b>{c['match']}</b>\n🎯 {c['pick']} (Odd: {c['odd']:.2f})\n\n"
        msg += f"🔥 <b>ODD TOTAL: {total_odd:.2f}</b>"
        try: await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
        except: pass

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    data_visual = get_display_date_str()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Filtrando Elite e Jogos Ao Vivo ({data_visual})...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_all_soccer()
        if not jogos: 
            await msg.edit_text("❌ Nenhum jogo de Elite encontrado para hoje (ou todos já acabaram).")
            return
        
        header = f"🦁 <b>DVD TIPS | GRADE VIP</b> 🦁\n📅 <b>{data_visual}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            card = format_card(g)
            if len(txt) + len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>Postado!</b>")

    elif q.data == "daily_ticket":
        await fetch_all_soccer()
        await generate_daily_ticket(c)
        await q.message.reply_text("✅ <b>Bilhete Gerado!</b>")

def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Só Elite)", callback_data="fut_market")],
        [InlineKeyboardButton("🎫 Bilhete Pronto", callback_data="daily_ticket")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V294</b>\nFiltro VIP Ativado: Sem jogos velhos, sem times pequenos.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

# ================= SERVER =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V294 ELITE FILTER")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_all_soccer()

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
