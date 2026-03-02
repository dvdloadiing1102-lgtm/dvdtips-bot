# ================= BOT V335 (O RESGATE DA V327 + ALERTAS SEGUROS) =================
import os
import logging
import asyncio
import threading
import html
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Importações Seguras
try:
    import httpx
    import feedparser
    from dotenv import load_dotenv
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults
except ImportError:
    print("❌ ERRO: Faltam bibliotecas. Instale: pip install python-telegram-bot httpx feedparser python-dotenv")
    exit(1)

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- VARIÁVEIS GLOBAIS (CACHE) ---
TODAYS_GAMES = []
TODAYS_NBA = []
TODAYS_UFC = []
ALERT_MEMORY = {}

# --- 2. HELPERS MATEMÁTICOS (DA V327 - FUNCIONANDO) ---

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

# --- 3. PARSERS E FORMATADORES ---

def parse_odds_string(details_str, home_name, away_name):
    pick = "Aguardando Odds"; odd_decimal = 0.0; icon = "⏳"; is_favorite = False
    if not details_str or details_str == '-': return pick, odd_decimal, icon, is_favorite

    try:
        if "EV" in str(details_str).upper(): return "Jogo Equilibrado", 1.90, "⚖️", False
        parts = details_str.split(' ')
        abbr = parts[0]; number_str = parts[1] if len(parts) > 1 else "0"
        odd_decimal = american_to_decimal(number_str)
        
        team_focused = None; type_team = ""
        if abbr.lower() in home_name.lower()[:4]: team_focused = home_name; type_team = "HOME"
        elif abbr.lower() in away_name.lower()[:4]: team_focused = away_name; type_team = "AWAY"
        else:
            if "-" in number_str: return f"Favorito: {abbr}", odd_decimal, "🔥", True
            return pick, odd_decimal, icon, False

        # SE TIVER MENOS (-), É FAVORITO
        if "-" in number_str:
            pick = f"Vitória do {team_focused}"
            icon = "🏠" if type_team == "HOME" else "🔥"
            is_favorite = True
        else:
            pick = f"Zebra: {team_focused}"; icon = "🦓"; is_favorite = False
    except: pass
    return pick, odd_decimal, icon, is_favorite

def format_card(game, api_raw):
    odds_list = api_raw['competitions'][0].get('odds', [])
    details = odds_list[0].get('details', '-') if odds_list else '-'
    pick, odd, icon, _ = parse_odds_string(details, game['home'], game['away'])
    
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

def format_ufc_card(fight):
    red_odd = american_to_decimal(fight['red_odds'])
    blue_odd = american_to_decimal(fight['blue_odds'])
    odds_str = f"💰 {fight['red']}: @{red_odd}\n💰 {fight['blue']}: @{blue_odd}" if red_odd > 0 else "⚠️ Aguardando Odds"
    title_str = "🏆 <b>VALENDO CINTURÃO</b>\n" if fight['title'] else ""
    return f"🥊 <b>UFC | {fight['time']}</b>\n📍 {safe_html(fight['venue'])}\nℹ️ {fight['card']}\n{title_str}🔴 {safe_html(fight['red'])}\n          Vs\n🔵 {safe_html(fight['blue'])}\n{odds_str}\n━━━━━━━━━━━━━━━━━━━━\n"

def format_nba_card(game):
    return f"🏀 <b>NBA | {game['clock']}</b>\n⚔️ <b>{safe_html(game['match'])}</b>\n{game['tv']}\n✅ {safe_html(game['pick'])}\n📊 Spread: {safe_html(game['odds'])}\n━━━━━━━━━━━━━━━━━━━━\n"

# --- 4. MOTORES DE BUSCA (A VOLTA DO MODO V327) ---

async def fetch_espn_soccer():
    global TODAYS_GAMES
    date_str = get_api_date_str()
    print(f"🔄 BUSCANDO FUTEBOL NA API PARA: {date_str}") # Log no console
    
    leagues = {'bra.1': '🇧🇷 Brasileirão', 'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'bra.copa_do_brasil': '🏆 Copa BR'}
    found_games = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        for code, name in leagues.items():
            try:
                r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}")
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        status = event['status']['type']['state']
                        clock = event['status']['type']['detail']
                        status = 'in' if status == 'in' else ('post' if status == 'post' else 'agendado')
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        sh = int(comp[0]['score']); sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', '-')
                        
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv = broadcasts[0]['names'][0] if broadcasts else ("Premiere/Globo" if 'bra' in code else "")
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        found_games.append({
                            "id": event['id'], "raw": event,
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status, "clock": clock,
                            "score_home": sh, "score_away": sa,
                            "venue": venue, "tv": tv
                        })
            except: pass

    found_games.sort(key=lambda x: x['time'])
    TODAYS_GAMES = found_games
    print(f"✅ FUTEBOL ENCONTRADO: {len(TODAYS_GAMES)} JOGOS") # Log
    return found_games

async def fetch_espn_ufc():
    global TODAYS_UFC
    print("🔄 BUSCANDO UFC...")
    ufc_list = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    for comp in event['competitions']:
                        fighters = comp['competitors']
                        red = fighters[0]['athlete']['fullName']; blue = fighters[1]['athlete']['fullName']
                        ufc_list.append({
                            "red": red, "blue": blue, "time": dt.strftime("%d/%m %H:%M"),
                            "venue": comp.get('venue', {}).get('fullName', '-'),
                            "card": comp.get('card', 'main'), "title": comp.get('type', {}).get('slug') == 'title-fight',
                            "red_odds": "-200", "blue_odds": "+150" # Placeholder, API as vezes falha odd
                        })
    except: pass
    TODAYS_UFC = ufc_list
    print(f"✅ UFC ENCONTRADO: {len(TODAYS_UFC)} LUTAS")
    return ufc_list

async def fetch_espn_nba():
    global TODAYS_NBA
    date_str = get_api_date_str()
    nba_list = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    pick = f"Vitória do {t_home['team']['name']}"
                    if odds != '-' and len(odds.split(' ')) > 1:
                        if odds.split(' ')[0] in t_away['team']['abbreviation']: pick = f"Vitória do {t_away['team']['name']}"

                    nba_list.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"), "clock": event['status']['type']['detail'], 
                        "tv": "NBA League Pass", "pick": pick, "odds": odds
                    })
    except: pass
    TODAYS_NBA = nba_list
    return nba_list

# --- 5. LOOP DE ALERTAS (NARRADOR) - INDEPENDENTE ---

async def alert_loop(app):
    print("🎙️ SISTEMA DE ALERTAS INICIADO (SEGUNDO PLANO)")
    global ALERT_MEMORY
    while True:
        await asyncio.sleep(60) # Checa a cada minuto
        try:
            # Não faz fetch aqui se a lista já estiver populada recentemente, ou faz fetch silencioso
            # Para garantir, usamos fetch silencioso
            games = await fetch_espn_soccer() 
            
            for game in games:
                gid = game['id']; status = game['status']
                sh = game['score_home']; sa = game['score_away']
                
                if gid not in ALERT_MEMORY:
                    ALERT_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}
                    continue
                old = ALERT_MEMORY[gid]
                
                # GOL
                if status == 'in' and (sh > old['h'] or sa > old['a']):
                    scorer = game['home'] if sh > old['h'] else game['away']
                    msg = f"⚽ <b>GOOOOOOL DO {scorer.upper()}!</b>\n\n🏟️ {game['match']}\n⏱️ {game['clock']}\n🔢 {sh} - {sa}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                # GREEN/RED
                if status == 'post' and old['status'] == 'in':
                    odds_list = game['raw']['competitions'][0].get('odds', [])
                    details = odds_list[0].get('details', '-') if odds_list else '-'
                    pick, _, _, is_fav = parse_odds_string(details, game['home'], game['away'])
                    if is_fav:
                        msg = f"🏁 <b>FINALIZADO</b>\n\n⚽ {game['match']}\n🔢 {sh} - {sa}\n🎯 Pick: {pick}"
                        try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                        except: pass
                
                ALERT_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}
        except Exception as e:
            print(f"⚠️ Erro no Alerta: {e}")

async def news_loop(app):
    while True:
        await asyncio.sleep(14400) # 4h
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                msg = f"🌍 <b>GIRO DE NOTÍCIAS</b>\n\n📰 {safe_html(entry.title)}\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass

# --- 6. MENU (O SEGREDO DA V335) ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V335 (HÍBRIDO REAL)</b>\nSe a grade não aparecer, clique de novo que ele força a busca.", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut"), InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
        [InlineKeyboardButton("🎫 Bilhete", callback_data="ticket"), InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ]), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        # ESTRATÉGIA V335: SE ESTIVER VAZIO, BUSCA AGORA!
        if not TODAYS_GAMES:
            await q.message.reply_text("🔄 Buscando jogos na ESPN...")
            await fetch_espn_soccer()
        
        if not TODAYS_GAMES:
            await q.message.reply_text("❌ A ESPN não retornou jogos para hoje.")
            return

        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ufc":
        if not TODAYS_UFC: await fetch_espn_ufc()
        if not TODAYS_UFC:
            await q.message.reply_text("❌ Sem UFC hoje.")
            return
        txt = "🥊 <b>CARD UFC</b>\n\n"
        for f in TODAYS_UFC: txt += format_ufc_card(f)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "nba":
        if not TODAYS_NBA: await fetch_espn_nba()
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in TODAYS_NBA: txt += format_nba_card(g)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()
        
    elif q.data == "ticket":
        if not TODAYS_GAMES: await fetch_espn_soccer()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        valid = []
        for g in cands:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            d = odds_list[0].get('details', '-') if odds_list else '-'
            p, o, _, fav = parse_odds_string(d, g['home'], g['away'])
            if fav and 1.20 <= o <= 2.20: valid.append({'m': g['match'], 'p': p, 'o': o})
        
        if len(valid) < 2:
            await q.message.reply_text("❌ Jogos insuficientes.")
            return
        random.shuffle(valid)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        t = 1.0
        for v in valid[:3]:
            t *= v['o']
            msg += f"✅ <b>{v['m']}</b>\n🎯 {v['p']} (@{v['o']:.2f})\n\n"
        msg += f"🔥 <b>TOTAL: {t:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

# --- SERVER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE V335")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    print("🚀 BOT INICIADO!")
    # Inicia loops em background
    asyncio.create_task(alert_loop(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
