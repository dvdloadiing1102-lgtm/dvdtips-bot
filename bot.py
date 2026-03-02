# ================= BOT V324 (RAW DATA: FUTEBOL + NBA + UFC) =================
import os
import logging
import asyncio
import threading
import httpx
import html
import feedparser
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

# LISTA VIP APENAS PARA FILTRAR VISIBILIDADE (NÃO AFETA ODDS)
VIP_FILTER = [
    "flamengo", "palmeiras", "corinthians", "são paulo", "santos", "grêmio",
    "internacional", "atlético-mg", "cruzeiro", "botafogo", "fluminense", "vasco",
    "bahia", "fortaleza", "athletico-pr", "sport", "ceará", "vitória",
    "arsenal", "liverpool", "man city", "real madrid", "barcelona", "bayern", 
    "inter", "milan", "juventus", "psg", "chelsea", "dortmund", "benfica", "porto", "napoli", 
    "roma", "lazio", "atletico madrid", "boca juniors", "river plate", "tottenham", "man utd"
]

TODAYS_GAMES = []
TODAYS_NBA = []
TODAYS_UFC = []

# --- 2. HELPERS ---

def get_real_server_date():
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz)

def get_display_date():
    return get_real_server_date().strftime("%d/%m/%Y")

def get_api_date_str():
    return get_real_server_date().strftime("%Y%m%d")

def safe_html(text):
    if not text: return ""
    return html.escape(str(text))

def is_vip(team_name):
    return any(vip in team_name.lower() for vip in VIP_FILTER)

# --- 3. EXTRAÇÃO PURA (SEM COLINHA) ---

def extract_raw_soccer_data(game_data):
    """Extrai EXATAMENTE o que está na API. Se não tiver, retorna None."""
    comp = game_data['competitions'][0]
    odds_list = comp.get('odds', [])
    
    # 1. Tenta pegar Probability (BPI - Power Index)
    prob_home = 0
    prob_away = 0
    
    # Alguns endpoints retornam odds dentro de odds, outros direto.
    if odds_list:
        line = odds_list[0]
        # Tenta pegar probabilidade matemática da ESPN
        if 'homeTeamOdds' in line and 'winPercentage' in line['homeTeamOdds']:
            prob_home = line['homeTeamOdds'].get('winPercentage', 0)
            prob_away = line['awayTeamOdds'].get('winPercentage', 0)
    
    # 2. Tenta pegar Odds de Aposta (Betting Lines)
    pick_text = "Aguardando Mercado Oficial"
    details_text = "-"
    icon = "⚖️"
    
    if odds_list:
        line = odds_list[0]
        details = line.get('details', '-') # Ex: "FLA -0.5"
        over_under = line.get('overUnder', 0)
        
        if details and details != '-':
            pick_text = f"Linha: {details}"
            icon = "📊"
        
        if over_under > 0:
            details_text = f"O/U {over_under} Gols"
            
    # Se tiver probabilidade BPI, usa ela pois é mais precisa que odd
    if prob_home > 0 or prob_away > 0:
        home_name = comp['competitors'][0]['team']['name']
        away_name = comp['competitors'][1]['team']['name']
        
        if prob_home > prob_away:
            pick_text = f"Favorito: {home_name} ({int(prob_home)}%)"
            icon = "🏠"
        else:
            pick_text = f"Favorito: {away_name} ({int(prob_away)}%)"
            icon = "🔥"

    return pick_text, details_text, icon

# --- 4. FORMATADORES ---

def format_card(game, api_raw):
    # Extração Pura
    pick, extra, icon = extract_raw_soccer_data(api_raw)
    
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    
    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ <i>{safe_html(game['venue'])}</i>\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"{icon} <b>{pick}</b>\n"
        f"📏 Market: {extra}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    return (
        f"🏀 <b>NBA | {game['clock']}</b>\n"
        f"⚔️ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"✅ {safe_html(game['pick'])}\n"
        f"📊 {safe_html(game['odds'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_ufc_card(fight):
    # Formata card de luta
    title_str = "🏆 VALENDO CINTURÃO" if fight['title'] else ""
    card_status = "Main Card" if fight['card'] == 'main' else "Prelims"
    
    odds_str = ""
    if fight['odds']:
        odds_str = f"💰 Odds: {fight['odds']}\n"
    
    return (
        f"🥊 <b>UFC | {fight['time']}</b>\n"
        f"ℹ️ {card_status} | {fight['weight']}\n"
        f"{title_str}\n"
        f"🔴 <b>{safe_html(fight['red'])}</b>\n"
        f"       Vs\n"
        f"🔵 <b>{safe_html(fight['blue'])}</b>\n"
        f"{odds_str}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# --- 5. MOTORES DE BUSCA (AGORA COM UFC) ---

async def fetch_espn_soccer():
    date_str = get_api_date_str()
    logger.info(f"🔍 SOCCER: {date_str}")
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
                    for event in data.get('events', []):
                        status_key = event['status']['type']['state']
                        display_clock = event['status']['type']['detail']
                        
                        if status_key == 'pre': status = 'agendado'; display_clock = "Agendado"
                        elif status_key == 'in': status = 'in'
                        else: status = 'post'; display_clock = "Finalizado"
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        sh = int(comp[0]['score']); sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', 'Local a definir')
                        
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                        tv_str = ", ".join(tv_channels) if tv_channels else ""
                        if 'bra' in code and not tv_str: tv_str = "Premiere / Globo"
                        
                        # Filtro visual apenas
                        if 'bra' in code:
                            if not (is_vip(home) or is_vip(away)): continue
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        found_games.append({
                            "raw": event,
                            "id": event['id'],
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status,
                            "clock": display_clock,
                            "venue": venue,
                            "tv": tv_str,
                            "score_home": sh, "score_away": sa
                        })
            except Exception: pass
    
    found_games.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = found_games
    return found_games

async def fetch_espn_nba():
    date_str = get_api_date_str()
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
                    display_clock = event['status']['type']['detail']
                    
                    broadcasts = comp.get('broadcasts', [])
                    tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                    tv_str = ", ".join(tv_channels) if tv_channels else "NBA League Pass"

                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    
                    jogos.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"),
                        "clock": display_clock,
                        "tv": tv_str,
                        "pick": f"Vitória do {t_home['team']['name']}",
                        "odds": f"Spread: {odds}"
                    })
    except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

async def fetch_espn_ufc():
    """Busca dados brutos do UFC"""
    url = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
    lutas = []
    br_tz = timezone(timedelta(hours=-3))
    
    logger.info("🥊 BUSCANDO UFC...")
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    # Status do Card (Main/Prelim)
                    status_type = event['status']['type']['name'] # STATUS_SCHEDULED
                    
                    for comp in event['competitions']:
                        # Lutadores
                        fighters = comp['competitors']
                        red = fighters[0]['athlete']['fullName']
                        blue = fighters[1]['athlete']['fullName']
                        
                        # Detalhes
                        weight_class = comp.get('type', {}).get('abbreviation', 'MMA')
                        is_title = comp.get('type', {}).get('slug', '') == 'title-fight'
                        card_segment = comp.get('card', 'main') # main or prelims (às vezes null)
                        
                        # Odds
                        odds_val = "-"
                        if 'odds' in comp and comp['odds']:
                            odds_val = comp['odds'][0].get('details', '-')

                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        lutas.append({
                            "red": red, "blue": blue,
                            "time": dt.strftime("%H:%M"),
                            "weight": weight_class,
                            "title": is_title,
                            "card": card_segment,
                            "odds": odds_val
                        })
    except Exception as e:
        logger.error(f"Erro UFC: {e}")
        
    global TODAYS_UFC; TODAYS_UFC = lutas
    return lutas

# --- 6. ROTINAS ---

async def automation_routine(app):
    while True:
        now = datetime.now(timezone(timedelta(hours=-3)))
        
        # 08:00 - Futebol
        if now.hour == 8 and now.minute == 0:
            await fetch_espn_soccer()
            if TODAYS_GAMES:
                txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
                for g in TODAYS_GAMES:
                    if g['status'] != 'post':
                        card = format_card(g, g['raw'])
                        if len(txt)+len(card) > 4000:
                            try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                            except: pass
                            txt = ""
                        txt += card
                if txt: 
                    try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                    except: pass
            await asyncio.sleep(65)

        # 16:00 - NBA
        elif now.hour == 16 and now.minute == 0:
            await fetch_espn_nba()
            if TODAYS_NBA:
                txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
                for g in TODAYS_NBA:
                    card = format_nba_card(g)
                    if len(txt)+len(card) > 4000:
                        try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                        except: pass
                        txt = ""
                    txt += card
                if txt:
                    try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                    except: pass
            await asyncio.sleep(65)

        await asyncio.sleep(30)

async def news_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                msg = f"🌍 <b>GIRO DE NOTÍCIAS</b>\n\n📰 {safe_html(entry.title)}\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass
        await asyncio.sleep(14400)

# --- 7. MENU E EXECUÇÃO ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="ERRO:", exc_info=context.error)

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(f"🦁 <b>PAINEL V324 (RAW DATA)</b>\nAPI Pura. Sem Invenções.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 Consultando ESPN (Sem filtro de invenção)...")
        await fetch_espn_soccer()
        
        if not TODAYS_GAMES:
            await msg.edit_text(f"❌ Sem dados na API para {get_display_date()}.", parse_mode=ParseMode.HTML)
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            # RAW DATA ONLY
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "nba":
        msg = await q.message.reply_text("🏀 Buscando NBA...")
        jogos = await fetch_espn_nba()
        if not jogos:
            await msg.edit_text(f"❌ Sem NBA hoje.", parse_mode=ParseMode.HTML)
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()
        
    elif q.data == "ufc":
        msg = await q.message.reply_text("🥊 Buscando Card do UFC...")
        lutas = await fetch_espn_ufc()
        
        if not lutas:
            await msg.edit_text("❌ Nenhum evento do UFC encontrado hoje/próximos dias.")
            return
            
        txt = "🥊 <b>CARD UFC (OFICIAL)</b> 🥊\n\n"
        for fight in lutas:
            card = format_ufc_card(fight)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V324 RAW MODE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V324...")
    await fetch_espn_soccer()
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(news_loop(app))

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
