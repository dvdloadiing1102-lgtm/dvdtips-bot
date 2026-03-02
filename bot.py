# ================= BOT V327 (MATEMÁTICA DE ODDS CORRIGIDA + DETECTOR DE FAVORITO) =================
import os
import logging
import asyncio
import threading
import httpx
import html
import feedparser
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

TODAYS_GAMES = []
TODAYS_NBA = []
TODAYS_UFC = []

# --- 2. MATEMÁTICA DE APOSTAS (O CORAÇÃO DO FIX) ---

def american_to_decimal(american_val):
    """
    Converte Odd Americana para Decimal.
    Ex: -475 -> 1.21
    Ex: +150 -> 2.50
    """
    try:
        val = float(american_val)
        if val == 0: return 1.0
        
        if val < 0:
            # Favorito (Ex: -475) -> 1 + (100 / 475)
            return round(1 + (100 / abs(val)), 2)
        else:
            # Zebra (Ex: +150) -> 1 + (150 / 100)
            return round(1 + (val / 100), 2)
    except:
        return 0.0

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

# --- 3. PARSER INTELIGENTE DE ODDS ---

def parse_odds_string(details_str, home_name, away_name):
    """
    Analisa string tipo 'FLA -475' ou 'MAD +900' e define quem é o favorito.
    """
    pick = "Aguardando Odds"
    odd_decimal = 0.0
    icon = "⏳"
    is_favorite = False # Se true, a pick é no favorito

    if not details_str or details_str == '-':
        return pick, odd_decimal, icon, is_favorite

    try:
        # Separa texto (FLA) do número (-475)
        # As vezes vem "EVEN"
        if "EV" in details_str.upper():
            return "Jogo Equilibrado", 1.90, "⚖️", False

        parts = details_str.split(' ')
        abbr = parts[0] # FLA
        number_str = parts[1] if len(parts) > 1 else "0" # -475
        
        # Converte a odd
        odd_decimal = american_to_decimal(number_str)
        
        # Identifica de quem estamos falando
        # A API da ESPN costuma colocar a odd do time mencionado
        team_focused = None
        if abbr.lower() in home_name.lower()[:4]:
            team_focused = home_name
            type_team = "HOME"
        elif abbr.lower() in away_name.lower()[:4]:
            team_focused = away_name
            type_team = "AWAY"
        else:
            # Fallback: Se não achar por sigla, assume que é o favorito da linha se tiver '-'
            if "-" in number_str:
                # O time com '-' é favorito, mas não sabemos qual é só pela sigla.
                # Vamos tentar adivinhar ou retornar genérico
                pick = f"Favorito: {abbr}"
                is_favorite = True
                return pick, odd_decimal, "🔥", is_favorite

        # Lógica do Sinal
        if team_focused:
            if "-" in number_str:
                # FLA -475 -> Flamengo é Favorito
                pick = f"Vitória do {team_focused}"
                icon = "🏠" if type_team == "HOME" else "🔥"
                is_favorite = True
            else:
                # MAD +900 -> Madureira é Zebra
                # A API mostrou a odd da zebra. 
                pick = f"Zebra: {team_focused}"
                icon = "🦓"
                is_favorite = False
                
    except Exception as e:
        logger.error(f"Erro parse odds: {e}")

    return pick, odd_decimal, icon, is_favorite

# --- 4. FORMATADORES ---

def format_card(game, api_raw):
    # Extrai detalhes brutos
    odds_list = api_raw['competitions'][0].get('odds', [])
    details = odds_list[0].get('details', '-') if odds_list else '-'
    
    home = game['home']
    away = game['away']
    
    # Processa a matemática
    pick, odd, icon, is_fav = parse_odds_string(details, home, away)
    
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    
    odd_display = f"@{odd:.2f}" if odd > 0 else "(S/ Odd)"
    
    # Se for zebra, adiciona aviso
    risk_msg = ""
    if not is_fav and odd > 2.5:
        risk_msg = " ⚠️ (Alto Risco)"

    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ <i>{safe_html(game['venue'])}</i>\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"{icon} <b>{pick}</b>\n"
        f"💰 Odd: <b>{odd_display}</b>{risk_msg}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_ufc_card(fight):
    title_str = "🏆 <b>VALENDO CINTURÃO</b>\n" if fight['title'] else ""
    card_status = "🔥 Main Card" if fight['card'] == 'main' else "📺 Prelims"
    
    # Converte odds
    red_odd = american_to_decimal(fight['red_odds'])
    blue_odd = american_to_decimal(fight['blue_odds'])
    
    # Identifica favorito
    red_icon = ""
    blue_icon = ""
    if red_odd > 0 and blue_odd > 0:
        if red_odd < blue_odd: red_icon = "👑 (Fav)"
        else: blue_icon = "👑 (Fav)"
    
    odds_str = "⚠️ Aguardando Odds"
    if red_odd > 0:
        odds_str = f"💰 {fight['red']}: @{red_odd}\n💰 {fight['blue']}: @{blue_odd}"

    return (
        f"🥊 <b>UFC | {fight['time']}</b>\n"
        f"📍 {safe_html(fight['venue'])}\n"
        f"ℹ️ {card_status} | {fight['weight']}\n"
        f"{title_str}"
        f"🔴 {safe_html(fight['red'])} {red_icon}\n"
        f"          Vs\n"
        f"🔵 {safe_html(fight['blue'])} {blue_icon}\n"
        f"{odds_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    # Processa odd NBA (Spread)
    # Ex: HOU -15.5
    pick = game['pick'] # Já vem processado na extração
    spread = game['odds']
    
    return (
        f"🏀 <b>NBA | {game['clock']}</b>\n"
        f"⚔️ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"✅ {safe_html(pick)}\n"
        f"📊 Spread: {safe_html(spread)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# --- 5. MOTORES DE BUSCA ---

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
                        
                        if status_key == 'pre': status = 'agendado'
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
                        
                        # Filtro visual para não poluir com série C/D
                        # Mas aceita tudo se tiver odd
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        found_games.append({
                            "raw": event,
                            "id": event['id'],
                            "home": home, "away": away, # Guardar nomes limpos
                            "match": f"{home} x {away}",
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
                    
                    # Lógica de Pick NBA (Spread)
                    pick = f"Vitória do {t_home['team']['name']}" # Padrão casa
                    if odds != '-' and len(odds.split(' ')) > 1:
                        fav_abbr = odds.split(' ')[0]
                        # Se o spread é pro visitante (Ex: BOS -7.5)
                        if fav_abbr in t_away['team']['abbreviation']:
                            pick = f"Vitória do {t_away['team']['name']}"
                    
                    jogos.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"),
                        "clock": display_clock,
                        "tv": tv_str,
                        "pick": pick,
                        "odds": odds
                    })
    except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

async def fetch_espn_ufc():
    url = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
    lutas = []
    br_tz = timezone(timedelta(hours=-3))
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    for comp_inner in event['competitions']:
                        venue = comp_inner.get('venue', {}).get('fullName', 'Arena UFC')
                        fighters = comp_inner['competitors']
                        red = fighters[0]['athlete']['fullName']
                        blue = fighters[1]['athlete']['fullName']
                        
                        # Tenta pegar odds (estrutura chata da ESPN)
                        r_odd = 0
                        b_odd = 0
                        # Navega na estrutura (as vezes muda)
                        if 'odds' in comp_inner and comp_inner['odds']:
                             # Lógica de extração de odd de luta seria aqui
                             # Como é complexa, deixamos 0 se não achar fácil
                             pass

                        weight_class = comp_inner.get('type', {}).get('abbreviation', 'MMA')
                        is_title = comp_inner.get('type', {}).get('slug', '') == 'title-fight'
                        card_segment = comp_inner.get('card', 'main') 

                        lutas.append({
                            "red": red, "blue": blue,
                            "time": dt.strftime("%d/%m - %H:%M"),
                            "venue": venue,
                            "weight": weight_class,
                            "title": is_title,
                            "card": card_segment,
                            "red_odds": r_odd, 
                            "blue_odds": b_odd
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
        [InlineKeyboardButton("🎫 Bilhete de Ouro", callback_data="ticket")],
        [InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(f"🦁 <b>PAINEL V327 (DECIMAL EXACT)</b>\n-475 = 1.21\nFavoritos Reais: ON", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 Analisando odds reais...")
        await fetch_espn_soccer()
        
        if not TODAYS_GAMES:
            await msg.edit_text(f"❌ Sem jogos.", parse_mode=ParseMode.HTML)
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "ticket":
        await fetch_espn_soccer()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        valid_cands = []
        
        # Filtra favoritos claros (1.15 a 1.90) - Aposta Segura
        for g in cands:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            details = odds_list[0].get('details', '-') if odds_list else '-'
            p, odd, _, is_fav = parse_odds_string(details, g['home'], g['away'])
            
            if is_fav and odd >= 1.15 and odd <= 2.00:
                valid_cands.append({'match': g['match'], 'pick': p, 'odd': odd})
        
        if len(valid_cands) < 3:
            await q.message.reply_text("❌ Não há favoritos claros suficientes para um Bilhete de Ouro hoje.")
            return

        random.shuffle(valid_cands)
        msg = "🎫 <b>BILHETE DE OURO (ODDS REAIS)</b> 🎫\n\n"
        total_odd = 1.0
        for vc in valid_cands[:3]:
            total_odd *= vc['odd']
            msg += f"✅ <b>{vc['match']}</b>\n🎯 {vc['pick']} (@{vc['odd']:.2f})\n\n"
        
        msg += f"🔥 <b>ODD FINAL: {total_odd:.2f}</b>"
        await c.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)

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
        msg = await q.message.reply_text("🥊 Buscando UFC...")
        lutas = await fetch_espn_ufc()
        
        if not lutas:
            await msg.edit_text("❌ Nenhum evento na API.")
            return
            
        txt = "🥊 <b>CARD UFC</b> 🥊\n\n"
        for fight in lutas:
            card = format_ufc_card(fight)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V327 MATH FIX")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V327...")
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
