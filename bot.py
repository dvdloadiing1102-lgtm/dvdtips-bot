# ================= BOT V332 (RESGATE TOTAL: UFC + GRADE FORÇADA + ALERTAS) =================
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

# MEMÓRIA GLOBAL (CACHE)
DATA_CACHE = {
    "soccer": [],
    "nba": [],
    "ufc": [],
    "last_update": None
}

# CONTROLE DE ALERTAS (Evita repetição)
ALERT_MEMORY = {}

# --- 2. HELPERS MATEMÁTICOS ---

def american_to_decimal(american_str):
    try:
        if "EV" in str(american_str).upper(): return 2.00
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

# --- 3. PARSERS (LEITURA DE DADOS) ---

def parse_odds_string(details_str, home_name, away_name):
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
        
        team_focused = None
        type_team = ""
        
        if abbr.lower() in home_name.lower()[:4]: team_focused = home_name; type_team = "HOME"
        elif abbr.lower() in away_name.lower()[:4]: team_focused = away_name; type_team = "AWAY"
        else:
            if "-" in number_str: return f"Favorito: {abbr}", odd_decimal, "🔥", True
            return pick, odd_decimal, icon, False

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

def format_nba_card(game):
    return (
        f"🏀 <b>NBA | {game['clock']}</b>\n"
        f"⚔️ <b>{safe_html(game['match'])}</b>\n"
        f"{game['tv']}\n"
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
    if red_odd > 0:
        odds_str = f"💰 {fight['red']}: @{red_odd}\n💰 {fight['blue']}: @{blue_odd}"

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

# --- 5. MOTOR DE BUSCA (ATUALIZA CACHE) ---

async def update_data():
    global DATA_CACHE
    date_str = get_api_date_str()
    br_tz = timezone(timedelta(hours=-3))
    
    # 1. FUTEBOL
    leagues = {
        'bra.1': '🇧🇷 Brasileirão', 'bra.copa_do_brasil': '🏆 Copa do Brasil',
        'bra.camp.paulista': '🇧🇷 Paulistão', 'bra.camp.carioca': '🇧🇷 Carioca',
        'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
        'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'
    }
    
    soccer_list = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        status = event['status']['type']['state']
                        clock = event['status']['type']['detail']
                        if status == 'pre': status = 'agendado'
                        elif status == 'in': status = 'in'
                        else: status = 'post'; clock = "Finalizado"
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        sh = int(comp[0]['score']); sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', 'Local a definir')
                        
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                        tv_str = ", ".join(tv_channels) if tv_channels else ""
                        if 'bra' in code and not tv_str: tv_str = "Premiere / Globo"
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        soccer_list.append({
                            "id": event['id'], "raw": event,
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status, "clock": clock,
                            "score_home": sh, "score_away": sa,
                            "venue": venue, "tv": tv_str
                        })
            except: pass
            
    # 2. NBA
    nba_list = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    clock = event['status']['type']['detail']
                    tv_list = comp.get('broadcasts', []); tv_str = tv_list[0]['names'][0] if tv_list else "NBA League Pass"
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    
                    pick = f"Vitória do {t_home['team']['name']}"
                    if odds != '-' and len(odds.split(' ')) > 1:
                        if odds.split(' ')[0] in t_away['team']['abbreviation']: pick = f"Vitória do {t_away['team']['name']}"

                    nba_list.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"), "clock": clock, "tv": tv_str,
                        "pick": pick, "odds": odds
                    })
    except: pass

    # 3. UFC
    ufc_list = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    for comp_inner in event['competitions']:
                        venue = comp_inner.get('venue', {}).get('fullName', 'Arena UFC')
                        fighters = comp_inner['competitors']
                        red = fighters[0]['athlete']['fullName']; blue = fighters[1]['athlete']['fullName']
                        weight_class = comp_inner.get('type', {}).get('abbreviation', 'MMA')
                        is_title = comp_inner.get('type', {}).get('slug', '') == 'title-fight'
                        card_segment = comp_inner.get('card', 'main')
                        
                        ufc_list.append({
                            "red": red, "blue": blue, "time": dt.strftime("%d/%m - %H:%M"),
                            "venue": venue, "weight": weight_class, "title": is_title,
                            "card": card_segment, "red_odds": "-200", "blue_odds": "+150"
                        })
    except: pass

    # SALVA TUDO
    DATA_CACHE['soccer'] = sorted(soccer_list, key=lambda x: x['time'])
    DATA_CACHE['nba'] = nba_list
    DATA_CACHE['ufc'] = ufc_list
    DATA_CACHE['last_update'] = datetime.now()

# --- 6. ALERTAS (NARRADOR) ---

async def check_alerts(app):
    global ALERT_MEMORY
    
    # Usa cópia para não travar
    games = list(DATA_CACHE['soccer'])
    
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

# --- 7. ROTINA MESTRA ---

async def master_loop(app):
    while True:
        await update_data() # Atualiza
        await check_alerts(app) # Verifica
        
        # Automação de Horário
        now = datetime.now(timezone(timedelta(hours=-3)))
        if now.hour == 8 and now.minute == 0:
            if DATA_CACHE['soccer']:
                txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
                for g in DATA_CACHE['soccer']:
                    c = format_card(g, g['raw'])
                    if len(txt)+len(c) > 4000:
                        await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                    txt += c
                if txt: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        
        await asyncio.sleep(60)

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

# --- 8. MENU ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V332 (RESGATE)</b>", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete de Ouro", callback_data="ticket")],
        [InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ]), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        # Se cache vazio, FORÇA update
        if not DATA_CACHE['soccer']:
            await q.message.reply_text("🔄 Buscando dados atualizados...")
            await update_data()
        
        if not DATA_CACHE['soccer']:
            await q.message.reply_text("❌ Sem jogos hoje.")
            return

        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in DATA_CACHE['soccer']:
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ufc":
        # Se cache vazio, FORÇA update
        if not DATA_CACHE['ufc']:
            await q.message.reply_text("🥊 Buscando Card...")
            await update_data()

        if not DATA_CACHE['ufc']:
            await q.message.reply_text("❌ Sem lutas no calendário.")
            return

        txt = "🥊 <b>CARD UFC</b> 🥊\n\n"
        for f in DATA_CACHE['ufc']:
            card = format_ufc_card(f)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "nba":
        if not DATA_CACHE['nba']: await update_data()
        if not DATA_CACHE['nba']:
            await q.message.reply_text("❌ Sem NBA.")
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in DATA_CACHE['nba']: txt += format_nba_card(g)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ticket":
        if not DATA_CACHE['soccer']: await update_data()
        cands = []
        for g in DATA_CACHE['soccer']:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            details = odds_list[0].get('details', '-') if odds_list else '-'
            p, odd, _, is_fav = parse_odds_string(details, g['home'], g['away'])
            if is_fav and odd >= 1.20 and odd <= 2.20:
                cands.append({'m': g['match'], 'p': p, 'o': odd})
        
        if len(cands) < 3:
            await q.message.reply_text("❌ Jogos insuficientes para bilhete.")
            return
            
        random.shuffle(cands)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        tot = 1.0
        for c in cands[:3]:
            tot *= c['o']
            msg += f"✅ <b>{c['m']}</b>\n🎯 {c['p']} (@{c['o']:.2f})\n\n"
        msg += f"🔥 <b>ODD FINAL: {tot:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

# --- SERVER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V332 FULL RESTORE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V332...")
    await update_data()
    asyncio.create_task(master_loop(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
