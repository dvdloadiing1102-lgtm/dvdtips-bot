# ================= BOT V310 (REALIDADE PURA: SEM SIMULAÇÃO, SEM GE) =================
import os
import logging
import asyncio
import threading
import httpx
import html
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Lista VIP (Filtra times pequenos, mas mantém os grandes)
VIP_TEAMS = [
    "Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Santos", "Grêmio", 
    "Internacional", "Atlético-MG", "Cruzeiro", "Botafogo", "Fluminense", "Vasco",
    "Bahia", "Fortaleza", "Athletico-PR", "Sport", "Ceará", "Vitória", "Remo", "Paysandu",
    "Arsenal", "Liverpool", "Man City", "Real Madrid", "Barcelona", "Bayern", "Inter", "Milan", "Juventus", "PSG", "Chelsea",
    "Dortmund", "Benfica", "Porto", "Napoli", "Roma", "Lazio", "Atletico Madrid", "Boca Juniors", "River Plate"
]

TODAYS_GAMES = []
TODAYS_NBA = []

# --- 2. DATA REAL (SEM TRUQUES) ---

def get_real_date_str():
    """Retorna a data EXATA do servidor para a consulta"""
    br_tz = timezone(timedelta(hours=-3))
    now = datetime.now(br_tz)
    # Formato YYYYMMDD exigido pela ESPN
    return now.strftime("%Y%m%d")

def get_display_date():
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz).strftime("%d/%m/%Y")

# --- 3. ANÁLISE (APENAS PARA JOGOS ENCONTRADOS) ---

def get_market_analysis(home, away, league_name):
    h_weight = 50; a_weight = 35
    if any(t in home for t in VIP_TEAMS): h_weight += 20
    if any(t in away for t in VIP_TEAMS): a_weight += 15
    
    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    random.seed(len(home) + len(away) + int(datetime.now().day))
    ph += random.randint(-5, 5); pa += random.randint(-5, 5)
    confidence = min(max(ph, pa), 90)
    
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    if ph >= 60:
        pick = f"Vitória do {home}"; odd = round(100/ph + 0.15, 2); icon = "🏠"
    elif pa >= 58:
        pick = f"Vitória do {away}"; odd = round(100/pa + 0.15, 2); icon = "🔥"
    else:
        odd = 1.85; pick = "Over 2.5 Gols"; icon = "⚽"

    return html.escape(pick), conf_bar, odd, icon

def format_card(game):
    pick, conf, odd, icon = get_market_analysis(game['home'], game['away'], game['league'])
    return (
        f"{html.escape(game['league'])} | ⏰ {game['time']}\n"
        f"⚽ <b>{html.escape(game['match'])}</b>\n"
        f"{icon} <b>Palpite: {pick}</b>\n"
        f"📊 Confiança: {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    return f"🏀 <b>NBA | {game['time']}</b>\n⚔️ <b>{html.escape(game['match'])}</b>\n✅ {html.escape(game['pick'])}\n📊 {html.escape(game['odds'])}\n━━━━━━━━━━━━━━━━━━━━\n"

# --- 4. MOTOR ESPN (DATA ESTRITA) ---

async def fetch_espn_data():
    date_str = get_real_date_str()
    logger.info(f"🔍 BUSCANDO DADOS REAIS PARA: {date_str}")
    
    leagues = {
        'bra.1': '🇧🇷 Brasileirão', 'bra.copa_do_brasil': '🏆 Copa do Brasil',
        'bra.camp.paulista': '🇧🇷 Paulistão', 'bra.camp.carioca': '🇧🇷 Carioca',
        'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
        'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'
    }
    
    found_games = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}"
                r = await client.get(url)
                
                if r.status_code == 200:
                    data = r.json()
                    events = data.get('events', [])
                    
                    if events:
                        logger.info(f"✅ {code}: Encontrou {len(events)} eventos.")
                    
                    for event in events:
                        status_raw = event['status']['type']['state']
                        # Mapeia status real
                        if status_raw == 'pre': status = 'agendado'
                        elif status_raw == 'in': status = 'in'
                        else: status = 'post'
                        
                        comp = event['competitions'][0]['competitors']
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        
                        # Filtro VIP: Se for BR, só mostra times grandes.
                        # Se for Europa, mostra tudo da liga selecionada.
                        if 'bra' in code:
                            if not (any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)):
                                continue
                        
                        found_games.append({
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status,
                            "score_home": int(comp[0]['score']), "score_away": int(comp[1]['score'])
                        })
                else:
                    logger.warning(f"⚠️ {code}: Status {r.status_code}")
            except Exception as e:
                logger.error(f"❌ Erro {code}: {e}")

    found_games.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = found_games
    logger.info(f"📊 TOTAL FINAL DE JOGOS REAIS: {len(found_games)}")
    return found_games

async def fetch_nba_professional():
    date_str = get_real_date_str()
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    jogos.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"),
                        "pick": f"Vitória do {t_home['team']['name']}",
                        "odds": f"Spread: {odds}"
                    })
    except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

# --- 5. COMANDOS ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="ERRO CAPTURADO:", exc_info=context.error)

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Real)", callback_data="fut")],
        [InlineKeyboardButton("🏀 NBA (Real)", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(f"🦁 <b>PAINEL V310 (REALISTA)</b>\nData Servidor: {get_display_date()}\nSem simulações.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 Buscando na ESPN para {get_display_date()}...")
        await fetch_espn_data()
        
        if not TODAYS_GAMES:
            await msg.edit_text(f"❌ <b>ZERO JOGOS ENCONTRADOS.</b>\n\nA API da ESPN não retornou nenhum jogo agendado para a data <b>{get_display_date()}</b>.\nIsso é o dado real. Não há simulação.", parse_mode=ParseMode.HTML)
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "nba":
        msg = await q.message.reply_text("🏀 Buscando NBA...")
        jogos = await fetch_nba_professional()
        if not jogos:
            await msg.edit_text(f"❌ Sem jogos da NBA para {get_display_date()}.")
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V310 REAL DATA")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V310 (REAL MODE)...")
    await fetch_espn_data()

def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
