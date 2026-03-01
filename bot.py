# ================= BOT V292 (COMPLETO: MENU RESTAURADO + GE/ESPN + NBA + BILHETE) =================
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

# ================= 🛡️ CACHE DE TABELA =================
LIVE_STANDINGS = {}

# ================= CONFIGURAÇÃO DATA =================
def get_current_date_data():
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    # Se for antes das 05:00, considera grade do dia anterior (jogos da noite)
    if agora.hour < 5: 
        data_real = agora - timedelta(days=1)
    else: 
        data_real = agora
    
    # Data Visual (2026 para sua simulação)
    try: data_display = data_real.replace(year=2026)
    except: data_display = data_real + timedelta(days=365)
    
    return data_real, data_display

# ================= MEMÓRIA =================
TODAYS_GAMES = []
TODAYS_NBA = []
PROCESSED_GAMES = set()
ALERTED_SNIPER = set()
ALERTED_LIVE = set()
DAILY_STATS = {"green": 0, "red": 0}

# ================= 1. TRATAMENTO DE ERROS =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# ================= 2. NEWS =================
async def fetch_news():
    feeds = ["https://ge.globo.com/rss/ge/futebol/", "https://rss.uol.com.br/feed/esporte.xml"]
    noticias = []
    try:
        for url in feeds:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:1]:
                noticias.append(f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler matéria</a>")
    except: pass
    return noticias

async def news_loop(app: Application):
    await asyncio.sleep(10)
    while True:
        noticias = await fetch_news()
        if noticias:
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text="🌍 <b>GIRO DE NOTÍCIAS</b> 🌍\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass
        await asyncio.sleep(14400) 

# ================= 3. ANÁLISE DE MERCADO (FUTEBOL) =================
def calculate_dynamic_odd(probability):
    if probability <= 0: return 2.00
    fair_odd = 100 / probability
    return round(fair_odd + random.uniform(0.05, 0.15), 2)

def get_market_analysis(home, away, league_name):
    # Lógica baseada em peso de camisa e mando
    GIGANTES_BRASIL = ["Flamengo", "Palmeiras", "Atlético-MG", "São Paulo", "Internacional", "Grêmio", "Fluminense", "Botafogo", "Fortaleza", "Cruzeiro", "Corinthians", "Vasco", "Bahia", "Athletico-PR", "Santos"]
    GIGANTES_EUROPA = ["Real Madrid", "Man City", "Bayern", "Liverpool", "Inter", "Arsenal", "Barcelona", "PSG", "Juventus", "Milan"]
    
    h_weight = 50
    a_weight = 30
    
    # Bônus para gigantes
    if any(g in home for g in GIGANTES_BRASIL + GIGANTES_EUROPA): h_weight += 25
    if any(g in away for g in GIGANTES_BRASIL + GIGANTES_EUROPA): a_weight += 20
    
    # Ajuste de Liga (Zebras)
    if "Copa" in league_name and not any(g in home for g in GIGANTES_BRASIL):
        h_weight -= 10 # Time pequeno na Copa costuma sofrer

    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    # Travas de segurança
    ph = max(15, min(ph, 85))
    pa = max(15, min(pa, 85))

    confidence = max(ph, pa)
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    strategy_icon = "🎯"; strategy_name = "Análise Tática"
    
    if ph >= 65:
        pick = f"Vitória do {home}"
        odd = calculate_dynamic_odd(ph)
        narrativa = f"O {home} joga em casa e deve impor seu ritmo."
        strategy_icon = "🛡️"; strategy_name = "Muralha em Casa"
    elif pa >= 60:
        pick = f"Vitória do {away}"
        odd = calculate_dynamic_odd(pa)
        narrativa = f"O {away} é superior tecnicamente."
        strategy_icon = "🔥"; strategy_name = "Visitante Favorito"
    else:
        pick = "Over 1.5 Gols"
        odd = 1.45
        narrativa = "Jogo muito equilibrado, tendência a gols."
        
    return pick, "Over 0.5 HT" if confidence > 70 else "Menos de 3.5 Gols", narrativa, f"{conf_bar} {int(confidence)}%", odd, strategy_icon, strategy_name

# ================= 4. NBA (RESTAURADA) =================
def generate_nba_narrative(home, away, spread, total):
    try: spread_val = float(spread.split(' ')[1]) if spread != '-' and ' ' in spread else 0
    except: spread_val = 0
    analise = ""
    if abs(spread_val) >= 9: analise += f"O {home if spread_val < 0 else away} é amplamente favorito. "
    else: analise += "Confronto equilibrado na quadra. "
    return analise

async def fetch_nba_professional():
    api_date, _ = get_current_date_data()
    date_str = api_date.strftime("%Y%m%d")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200 or not r.json().get('events'):
                # Tenta fallback sem data
                r = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
            
            data = r.json()
            for event in data.get('events', []):
                state = event['status']['type']['state']
                comp = event['competitions'][0]
                t1 = comp['competitors'][0]; t2 = comp['competitors'][1]
                team_home = t1 if t1['homeAway'] == 'home' else t2
                team_away = t2 if t2['homeAway'] == 'away' else t1
                
                dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                
                odds_str = "Aguardando..."; spread_val = "-"; ou_val = "-"
                if 'odds' in comp and len(comp['odds']) > 0:
                    odd = comp['odds'][0]
                    spread_val = odd.get('details', '-'); ou_val = odd.get('overUnder', '-')
                    odds_str = f"Spread: {spread_val} | O/U: {ou_val}"

                try: spread_num = float(spread_val.split(' ')[1]) if ' ' in spread_val else 0
                except: spread_num = 0
                
                if spread_num <= -5: pick = f"Vitória do {team_home['team']['name']}"
                elif spread_num >= 5: pick = f"Vitória do {team_away['team']['name']}"
                else: pick = f"Vitória do {team_home['team']['name']}"

                analise = generate_nba_narrative(team_home['team']['name'], team_away['team']['name'], spread_val, ou_val)
                jogos.append({
                    "match": f"{team_away['team']['name']} @ {team_home['team']['name']}",
                    "home": team_home['team']['name'], "away": team_away['team']['name'],
                    "time": dt_br.strftime("%H:%M"), "odds": odds_str, "analise": analise,
                    "pick": pick, "status": state
                })
        except: pass
    
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

def format_nba_card(game):
    return (
        f"🏀 <b>NBA | {game['time']}</b>\n⚔️ <b>{game['match']}</b>\n"
        f"📝 <b>Resumo:</b> <i>{game['analise']}</i>\n✅ <b>Palpite:</b> {game['pick']}\n"
        f"📊 <b>Linhas:</b> {game['odds']}\n━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 5. FUTEBOL HÍBRIDO (GE + ESPN) =================
async def fetch_ge_data():
    """Busca jogos do Brasil no Globo Esporte"""
    data_real, _ = get_current_date_data()
    data_str = data_real.strftime("%Y-%m-%d")
    url = f"https://api.globoesporte.globo.com/tabela/d1/api/tabela/jogos?data={data_str}"
    jogos = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code == 200:
                lista = r.json()
                for item in lista:
                    camp = item.get('campeonato', {}).get('nome', 'Futebol BR')
                    home = item['equipes']['mandante']['nome_popular']
                    away = item['equipes']['visitante']['nome_popular']
                    hora = item.get('hora', '00:00')
                    local = item.get('sede', {}).get('nome_popular', '')
                    
                    if "Sub-" in camp or "Feminino" in camp: continue 

                    jogos.append({
                        "id": f"ge_{home}_{away}",
                        "match": f"{home} x {away}",
                        "home": home, "away": away,
                        "time": hora,
                        "league": f"🏆 {camp}",
                        "stadium": f"🏟️ {local}" if local else "",
                        "score_home": 0, "score_away": 0 # GE não traz live score fácil aqui
                    })
    except: pass
    return jogos

async def fetch_espn_europe():
    """Busca Elite da Europa na ESPN"""
    data_real, _ = get_current_date_data()
    data_str = data_real.strftime("%Y%m%d")
    leagues = ['uefa.champions', 'eng.1', 'esp.1', 'ita.1', 'ger.1', 'ksa.1']
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={data_str}"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    l_name = data.get('leagues', [{}])[0].get('name', 'Futebol Int.')
                    for event in data.get('events', []):
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']
                        away = comp[1]['team']['name']
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        jogos.append({
                            "id": event['id'],
                            "match": f"{home} x {away}",
                            "home": home, "away": away,
                            "time": dt.strftime("%H:%M"),
                            "league": f"🏆 {l_name}",
                            "stadium": "",
                            "score_home": int(comp[0]['score']),
                            "score_away": int(comp[1]['score'])
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

# ================= 6. BILHETE OURO (RESTAURADO) =================
async def generate_daily_ticket(app):
    if not TODAYS_GAMES: return
    candidates = []
    for g in TODAYS_GAMES:
        pick, _, _, _, odd, _, _ = get_market_analysis(g['home'], g['away'], g['league'])
        if 1.30 <= odd <= 1.95: 
            candidates.append({'match': g['match'], 'pick': pick, 'odd': odd})
    
    random.shuffle(candidates)
    ticket = []
    total_odd = 1.0
    for c in candidates:
        if total_odd < 12.0: 
            ticket.append(c)
            total_odd *= c['odd']
        else: break
    
    if len(ticket) >= 3:
        msg = "🎫 <b>BILHETE DE OURO (ODD 10+)</b> 🎫\n<i>Jogos Selecionados 🚀</i>\n➖➖➖➖➖➖➖➖➖➖\n"
        for i, c in enumerate(ticket, 1):
            msg += f"{i}️⃣ <b>{c['match']}</b>\n🎯 {c['pick']} (Odd: {c['odd']:.2f})\n\n"
        msg += f"🔥 <b>ODD TOTAL: {total_odd:.2f}</b>\n💰 <i>Gestão: 0.5% da Banca</i>"
        try: await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
        except: pass

# ================= 7. LAYOUT E MENU =================
def format_card(game):
    pick, extra, narrativa, conf, odd, icon, sname = get_market_analysis(game['home'], game['away'], game['league'])
    stadium_txt = f"{game['stadium']}\n" if game['stadium'] else ""
    
    return (
        f"{game['league']}\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ {game['time']}\n{stadium_txt}"
        f"🧠 <b>Estratégia:</b> {icon} {sname}\n"
        f"📝 <b>Análise:</b> <i>{narrativa}</i>\n"
        f"✅ <b>Palpite:</b> {pick}\n"
        f"🛡️ <b>Extra:</b> {extra}\n"
        f"📊 <b>Confiança:</b> {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🎫 Bilhete Ouro (Odd 10+)", callback_data="daily_ticket")],
        [InlineKeyboardButton("🏀 Grade NBA VIP", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V292</b>\nSistema Completo Restaurado.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    _, data_visual = get_current_date_data()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Varrendo GE + ESPN ({data_visual})...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_all_soccer()
        if not jogos: await msg.edit_text("❌ Grade vazia."); return
        
        header = f"🦁 <b>DVD TIPS | FUTEBOL</b> 🦁\n📅 <b>{data_visual}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
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
        await fetch_all_soccer() # Garante dados frescos
        await generate_daily_ticket(c)
        await q.message.reply_text("✅ <b>Bilhete Gerado!</b>", parse_mode=ParseMode.HTML)

    elif q.data == "nba_deep":
        msg = await q.message.reply_text("🔎 <b>Analisando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_professional()
        if not jogos: await msg.edit_text("❌ Grade NBA vazia."); return
        
        header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{data_visual}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>NBA Postada!</b>")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V292 FULL RESTORE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_all_soccer() 
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
