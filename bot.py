# ================= BOT V329 (ESTABILIDADE: GRADE GARANTIDA + ALERTAS FIXOS) =================
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

# LISTA VIP (Para destaque, não mais para exclusão total)
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
GAME_MEMORY = {} 

# --- 2. HELPERS MATEMÁTICOS ---

def american_to_decimal(american_str):
    try:
        val = float(american_str)
        if val == 0: return 1.0
        if val < 0: return round((100 / abs(val)) + 1, 2)
        else: return round((val / 100) + 1, 2)
    except: return 0.0

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

# --- 3. EXTRAÇÃO DE DADOS ---

def parse_odds_string(details_str, home_name, away_name):
    """
    Retorna: Pick, Odd Decimal, Ícone, Is_Favorite
    """
    pick = "Aguardando Odds"
    odd_decimal = 0.0
    icon = "⏳"
    is_favorite = False

    if not details_str or details_str == '-': return pick, odd_decimal, icon, is_favorite

    try:
        if "EV" in str(details_str).upper(): return "Jogo Equilibrado", 1.90, "⚖️", False

        parts = details_str.split(' ')
        abbr = parts[0]
        number_str = parts[1] if len(parts) > 1 else "0"
        
        odd_decimal = american_to_decimal(number_str)
        
        # Tenta identificar por sigla
        team_focused = None
        type_team = ""
        
        if abbr.lower() in home_name.lower()[:4]:
            team_focused = home_name; type_team = "HOME"
        elif abbr.lower() in away_name.lower()[:4]:
            team_focused = away_name; type_team = "AWAY"
        else:
            # Se não bate a sigla, mas tem '-', assume que é o favorito da linha
            if "-" in number_str:
                return f"Favorito: {abbr}", odd_decimal, "🔥", True
            return pick, odd_decimal, icon, False

        # Lógica do Sinal (-)
        if "-" in number_str:
            pick = f"Vitória do {team_focused}"
            icon = "🏠" if type_team == "HOME" else "🔥"
            is_favorite = True
        else:
            pick = f"Zebra: {team_focused}"
            icon = "🦓"
            is_favorite = False
                
    except: pass
    return pick, odd_decimal, icon, is_favorite

# --- 4. FORMATADORES ---

def format_card(game, api_raw):
    odds_list = api_raw['competitions'][0].get('odds', [])
    details = odds_list[0].get('details', '-') if odds_list else '-'
    
    pick, odd, icon, is_fav = parse_odds_string(details, game['home'], game['away'])
    
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    odd_display = f"@{odd:.2f}" if odd > 0 else "(S/ Odd)"
    
    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ <i>{safe_html(game['venue'])}</i>\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"{icon} <b>{pick}</b>\n"
        f"💰 Odd: <b>{odd_display}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    return (
        f"🏀 <b>NBA | {game['clock']}</b>\n"
        f"⚔️ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"✅ {safe_html(game['pick'])}\n"
        f"📊 Spread: {safe_html(game['odds'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_ufc_card(fight):
    title_str = "🏆 <b>VALENDO CINTURÃO</b>\n" if fight['title'] else ""
    card_status = "🔥 Main Card" if fight['card'] == 'main' else "📺 Prelims"
    red_odd = american_to_decimal(fight['red_odds'])
    blue_odd = american_to_decimal(fight['blue_odds'])
    
    odds_str = "⚠️ Aguardando Odds"
    if red_odd > 0: odds_str = f"💰 {fight['red']}: @{red_odd}\n💰 {fight['blue']}: @{blue_odd}"

    return (
        f"🥊 <b>UFC | {fight['time']}</b>\n"
        f"📍 {safe_html(fight['venue'])}\n"
        f"ℹ️ {card_status} | {fight['weight']}\n"
        f"{title_str}"
        f"🔴 {safe_html(fight['red'])}\n"
        f"          Vs\n"
        f"🔵 {safe_html(fight['blue'])}\n"
        f"{odds_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# --- 5. MOTORES DE BUSCA (COM PROTEÇÃO DE LISTA VAZIA) ---

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
    games_all = [] # Backup sem filtro VIP
    
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
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        game_obj = {
                            "raw": event,
                            "id": event['id'],
                            "home": home, "away": away,
                            "match": f"{home} x {away}",
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status,
                            "clock": display_clock,
                            "venue": venue,
                            "tv": tv_str,
                            "score_home": sh, "score_away": sa
                        }
                        
                        # Salva na lista geral
                        games_all.append(game_obj)
                        
                        # Salva na lista VIP se passar no filtro
                        if 'bra' in code:
                            if is_vip(home) or is_vip(away): found_games.append(game_obj)
                        else:
                            found_games.append(game_obj) # Estrangeiros aceita tudo
                            
            except: pass
    
    # LÓGICA DE SALVAMENTO:
    # Se achou VIPs, mostra VIPs. Se não achou NENHUM VIP, mostra TUDO.
    if found_games:
        TODAYS_GAMES_FINAL = found_games
    else:
        TODAYS_GAMES_FINAL = games_all
        
    TODAYS_GAMES_FINAL.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = TODAYS_GAMES_FINAL
    
    logger.info(f"📊 Jogos Carregados: {len(TODAYS_GAMES)}")
    return TODAYS_GAMES

async def fetch_espn_nba():
    # Lógica NBA (Padrão)
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
                    
                    pick = f"Vitória do {t_home['team']['name']}"
                    if odds != '-' and len(odds.split(' ')) > 1:
                        fav_abbr = odds.split(' ')[0]
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
    # Lógica UFC
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
                            "red_odds": "-200", "blue_odds": "+150" # Placeholder
                        })
    except: pass
    global TODAYS_UFC; TODAYS_UFC = lutas
    return lutas

# --- 6. NARRADOR AO VIVO ---

async def live_narrator_routine(app):
    global GAME_MEMORY
    logger.info("🎙️ Narrador Ativo")
    while True:
        await asyncio.sleep(60)
        try:
            # Busca silenciosa para atualizar dados
            current_games = await fetch_espn_soccer()
            
            for game in current_games:
                gid = game['id']; status = game['status']
                sh = game['score_home']; sa = game['score_away']
                
                if gid not in GAME_MEMORY:
                    GAME_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}
                    continue
                old = GAME_MEMORY[gid]
                
                # Gol
                if status == 'in' and (sh > old['h'] or sa > old['a']):
                    scorer = game['home'] if sh > old['h'] else game['away']
                    msg = f"⚽ <b>GOOOOOOL DO {scorer.upper()}!</b>\n\n🏟️ {game['match']}\n⏱️ {game['clock']}\n🔢 Placar: {sh} - {sa}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                # Green/Red
                if status == 'post' and old['status'] == 'in':
                    odds_list = game['raw']['competitions'][0].get('odds', [])
                    details = odds_list[0].get('details', '-') if odds_list else '-'
                    pick, _, _, is_fav = parse_odds_string(details, game['home'], game['away'])
                    
                    is_green = False
                    if "Vitória do" in pick:
                        if game['home'] in pick and sh > sa: is_green = True
                        elif game['away'] in pick and sa > sh: is_green = True
                    
                    res_icon = "✅ GREEN" if is_green else "❌ RED"
                    if is_fav:
                        msg = f"{res_icon} <b>FINALIZADO</b>\n\n⚽ {game['match']}\n🔢 Placar Final: {sh} - {sa}\n🎯 Pick: {pick}"
                        try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                        except: pass

                GAME_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}
        except: pass

async def automation_routine(app):
    while True:
        now = datetime.now(timezone(timedelta(hours=-3)))
        
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
    await u.message.reply_text(f"🦁 <b>PAINEL V329 (ESTÁVEL)</b>\nLista Garantida: ON\nAlertas: ON", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 Buscando grade...")
        await fetch_espn_soccer()
        
        if not TODAYS_GAMES:
            await msg.edit_text(f"❌ <b>ZERO JOGOS ENCONTRADOS.</b>\nVerifique se há jogos hoje.", parse_mode=ParseMode.HTML)
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        
        # MANDA NO CHAT DO USUÁRIO (q.message.chat_id)
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "ticket":
        await fetch_espn_soccer()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        valid_cands = []
        for g in cands:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            details = odds_list[0].get('details', '-') if odds_list else '-'
            p, odd, _, is_fav = parse_odds_string(details, g['home'], g['away'])
            if is_fav and odd >= 1.15 and odd <= 2.20:
                valid_cands.append({'match': g['match'], 'pick': p, 'odd': odd})
        
        if len(valid_cands) < 3:
            await q.message.reply_text("❌ Sem jogos seguros o suficiente para bilhete.")
            return
        random.shuffle(valid_cands)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        t_odd = 1.0
        for vc in valid_cands[:3]:
            t_odd *= vc['odd']
            msg += f"✅ <b>{vc['match']}</b>\n🎯 {vc['pick']} (@{vc['odd']:.2f})\n\n"
        msg += f"🔥 <b>ODD FINAL: {t_odd:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        msg = await q.message.reply_text("🏀 Buscando NBA...")
        jogos = await fetch_espn_nba()
        if not jogos:
            await msg.edit_text("❌ Sem NBA hoje.")
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await msg.delete()
        
    elif q.data == "ufc":
        msg = await q.message.reply_text("🥊 Buscando UFC...")
        lutas = await fetch_espn_ufc()
        if not lutas:
            await msg.edit_text("❌ Sem UFC.")
            return
        txt = "🥊 <b>CARD UFC</b> 🥊\n\n"
        for fight in lutas: txt += format_ufc_card(fight)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V329 STABLE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V329...")
    await fetch_espn_soccer()
    asyncio.create_task(live_narrator_routine(app))
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
