# ================= BOT V296 (BRAIN UNLOCKED: MERCADOS VARIADOS + VISUAL PRO) =================
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

# ================= 🛡️ CONFIGURAÇÃO VIP =================
VIP_TEAMS = [
    "Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Santos", "Grêmio", 
    "Internacional", "Atlético-MG", "Cruzeiro", "Botafogo", "Fluminense", "Vasco",
    "Bahia", "Fortaleza", "Athletico-PR", "Sport", "Ceará", "Vitória", "Remo", "Paysandu"
]

def get_api_dates():
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz)

def get_display_date_str():
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    try: data_fake = agora.replace(year=2026)
    except: data_fake = agora + timedelta(days=365)
    return data_fake.strftime("%d/%m/%Y")

# ================= MEMÓRIA =================
TODAYS_GAMES = []
TODAYS_NBA = []

# ================= 1. ANÁLISE DE MERCADO AVANÇADA =================
def calculate_dynamic_odd(probability):
    if probability <= 0: return 2.00
    fair_odd = 100 / probability
    return round(fair_odd + random.uniform(0.05, 0.15), 2)

def get_market_analysis(home, away, league_name):
    # 1. Definição de Pesos (Favoritismo)
    h_weight = 50; a_weight = 40 # Casa tem vantagem natural
    
    # Peso de Camisa
    if any(t in home for t in VIP_TEAMS): h_weight += 25
    if any(t in away for t in VIP_TEAMS): a_weight += 20
    
    # Cálculo Probabilidade
    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    # Ajustes finos aleatórios (Simula momento do time)
    random.seed(len(home) + len(away)) # Seed fixa pelo nome pra não mudar toda hora
    ph += random.randint(-5, 5)
    pa += random.randint(-5, 5)

    confidence = max(ph, pa)
    confidence = min(confidence, 92) # Teto de 92%
    
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    # 2. SELEÇÃO DE MERCADO (INTELIGÊNCIA DO BOT)
    pick = ""
    extra = ""
    narrativa = ""
    icon = "🎯"
    
    # Cenário A: Favorito Claro (Vitória Seca)
    if ph >= 62:
        pick = f"Vitória do {home}"
        odd = calculate_dynamic_odd(ph)
        narrativa = f"O {home} é muito forte em seus domínios."
        icon = "🏠"
        extra = f"Over 1.5 Gols do {home}"
    elif pa >= 58:
        pick = f"Vitória do {away}"
        odd = calculate_dynamic_odd(pa)
        narrativa = f"O {away} tem elenco superior e deve vencer."
        icon = "🔥"
        extra = "Empate Anula: Visitante"
        
    # Cenário B: Jogo Equilibrado (Mercados Alternativos)
    else:
        diff = abs(ph - pa)
        odd = 1.90
        
        # Ligas de Gols/Cantos (Inglaterra, Alemanha, Holanda)
        if any(x in league_name for x in ['Premier', 'Bundesliga', 'Eredivisie', 'Champions']):
            if random.choice([True, False]):
                pick = "Over 2.5 Gols"
                narrativa = "Dois ataques potentes, jogo para gols."
                icon = "⚽"
                extra = "Ambas Marcam: Sim"
            else:
                pick = "Over 9.5 Escanteios"
                narrativa = "Jogo lá e cá, tendência de muitos cantos."
                icon = "🚩"
                extra = "Over 4.5 Cantos HT"
                
        # Ligas de Cartões/Pegadas (Brasil, Argentina, Libertadores, Italia)
        elif any(x in league_name for x in ['Brasil', 'Série A', 'Libertadores', 'Sul-Americana', 'Serie A', 'La Liga']):
            if diff < 5: # Muito parelho
                pick = "Empate"
                narrativa = "Jogo truncado, cheiro de empate."
                icon = "⚖️"
                extra = "Menos de 2.5 Gols"
            else:
                pick = "Over 5.5 Cartões"
                narrativa = "Clássico/Jogo pegado. Arbitragem rigorosa."
                icon = "🟨"
                extra = "Expulsão: Sim (Risco)"
        
        # Genérico (Resto)
        else:
            pick = "Ambas Marcam: Sim"
            narrativa = "Defesas instáveis."
            icon = "🥅"
            extra = "Over 1.5 Gols"

    return pick, extra, narrativa, f"{conf_bar} {int(confidence)}%", odd, icon

# ================= 2. MOTORES (GE + ESPN) =================
async def fetch_ge_data():
    data_real = get_api_dates()
    url = f"https://api.globoesporte.globo.com/tabela/d1/api/tabela/jogos?data={data_real.strftime('%Y-%m-%d')}"
    jogos = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code == 200:
                lista = r.json()
                for item in lista:
                    if item.get('realizado', False): continue
                    
                    home = item['equipes']['mandante']['nome_popular']
                    away = item['equipes']['visitante']['nome_popular']
                    camp = item.get('campeonato', {}).get('nome', '')
                    
                    is_serie_a = "Série A" in camp
                    has_big = any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)
                    
                    if not (is_serie_a or has_big): continue
                    if "Sub-" in camp or "Feminino" in camp: continue

                    jogos.append({
                        "match": f"{home} x {away}", "home": home, "away": away,
                        "time": item.get('hora', '00:00'), "league": f"🇧🇷 {camp}",
                        "source": "GE"
                    })
    except: pass
    return jogos

async def fetch_espn_europe():
    data_real = get_api_dates()
    leagues = {'uefa.champions': '🇪🇺 Champions League', 'eng.1': '🇬🇧 Premier League', 'esp.1': '🇪🇸 La Liga', 
               'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'}
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={data_real.strftime('%Y%m%d')}"
                r = await client.get(url)
                data = r.json()
                for event in data.get('events', []):
                    if event['status']['type']['state'] in ['post', 'ccc']: continue
                    
                    comp = event['competitions'][0]['competitors']
                    home = comp[0]['team']['name']
                    away = comp[1]['team']['name']
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    jogos.append({
                        "match": f"{home} x {away}", "home": home, "away": away,
                        "time": dt.strftime("%H:%M"), "league": name,
                        "source": "ESPN"
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

# ================= 3. NBA =================
async def fetch_nba():
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={get_api_dates().strftime('%Y%m%d')}"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(url)
            data = r.json()
            for e in data.get('events', []):
                comp = e['competitions'][0]
                h = comp['competitors'][0]; a = comp['competitors'][1]
                t_home = h if h['homeAway']=='home' else a
                t_away = a if a['homeAway']=='away' else h
                dt = datetime.strptime(e['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                jogos.append(f"🏀 <b>NBA</b> | ⏰ {dt.strftime('%H:%M')}\n⚔️ <b>{t_away['team']['name']} @ {t_home['team']['name']}</b>\n")
        except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

# ================= 4. MENU E LAYOUT (RESTAURADO) =================
def format_card(game):
    pick, extra, narrativa, conf, odd, icon = get_market_analysis(game['home'], game['away'], game['league'])
    return (
        f"{game['league']} | ⏰ {game['time']}\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"📝 <i>{narrativa}</i>\n"
        f"{icon} <b>Palpite: {pick}</b>\n"
        f"🛡️ Extra: {extra}\n"
        f"📊 Confiança: {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

async def generate_ticket(app):
    if not TODAYS_GAMES: return
    cands = []
    for g in TODAYS_GAMES:
        p, _, _, _, o, _ = get_market_analysis(g['home'], g['away'], g['league'])
        if 1.50 <= o <= 2.10: cands.append({'m': g['match'], 'p': p, 'o': o})
    random.shuffle(cands)
    sel = cands[:3] # Tripla Forte
    if sel:
        msg = "🎫 <b>TRIPLA DE RESPEITO (V296)</b> 🎫\n\n"
        odd_t = 1.0
        for c in sel:
            odd_t *= c['o']
            msg += f"✅ <b>{c['m']}</b>\n🎯 {c['p']} @ {c['o']}\n\n"
        msg += f"🔥 <b>ODD FINAL: {odd_t:.2f}</b>"
        await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete Pronto", callback_data="ticket")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V296</b>\nInteligência de Mercado Restaurada!", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    data_visual = get_display_date_str()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 <b>Analisando Mercados ({data_visual})...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_all_soccer()
        if not jogos: await msg.edit_text("❌ Grade vazia."); return
        
        txt = f"🦁 <b>DVD TIPS | GRADE VIP</b> 🦁\n📅 <b>{data_visual}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        for g in jogos:
            card = format_card(g)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()
        
    elif q.data == "ticket":
        await fetch_all_soccer()
        await generate_ticket(c)
        await q.message.reply_text("✅ <b>Bilhete Gerado!</b>")
        
    elif q.data == "nba":
        msg = await q.message.reply_text("🔎 <b>Buscando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba()
        if not jogos: await msg.edit_text("❌ Sem jogos."); return
        await c.bot.send_message(CHANNEL_ID, "".join(jogos), parse_mode=ParseMode.HTML)
        await msg.delete()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V296 FULL BRAIN")
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
