# ================= BOT V333 (STARTUP IMEDIATO: O FIM DO SILÊNCIO) =================
import os
import logging
import asyncio
import threading
import html
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Tenta importar bibliotecas externas e avisa se faltar
try:
    import httpx
    import feedparser
    from dotenv import load_dotenv
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults
except ImportError as e:
    print(f"❌ ERRO CRÍTICO: Faltam bibliotecas! Instale: pip install python-telegram-bot httpx feedparser python-dotenv")
    print(f"Detalhe: {e}")
    exit(1)

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# Validação de Token
if not BOT_TOKEN:
    print("❌ ERRO: 'BOT_TOKEN' não encontrado no .env ou variáveis de ambiente!")
    exit(1)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# CACHE GLOBAL
DATA_CACHE = {
    "soccer": [],
    "nba": [],
    "ufc": [],
    "last_update": None,
    "status": "Iniciando..." # Status do sistema
}

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

# --- 3. PARSERS ---

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

        if "-" in number_str:
            pick = f"Vitória do {team_focused}"
            icon = "🏠" if type_team == "HOME" else "🔥"
            is_favorite = True
        else:
            pick = f"Zebra: {team_focused}"; icon = "🦓"; is_favorite = False
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
    return f"🏀 <b>NBA | {game['clock']}</b>\n⚔️ <b>{safe_html(game['match'])}</b>\n{game['tv']}\n✅ {safe_html(game['pick'])}\n📊 Spread: {safe_html(game['odds'])}\n━━━━━━━━━━━━━━━━━━━━\n"

def format_ufc_card(fight):
    red_odd = american_to_decimal(fight['red_odds'])
    blue_odd = american_to_decimal(fight['blue_odds'])
    odds_str = f"💰 {fight['red']}: @{red_odd}\n💰 {fight['blue']}: @{blue_odd}" if red_odd > 0 else "⚠️ Aguardando Odds"
    title_str = "🏆 <b>VALENDO CINTURÃO</b>\n" if fight['title'] else ""
    return f"🥊 <b>UFC | {fight['time']}</b>\n📍 {safe_html(fight['venue'])}\nℹ️ {fight['card']}\n{title_str}🔴 {safe_html(fight['red'])}\n          Vs\n🔵 {safe_html(fight['blue'])}\n{odds_str}\n━━━━━━━━━━━━━━━━━━━━\n"

# --- 5. MOTOR DE BUSCA (ASSÍNCRONO E SEGURO) ---

async def update_data():
    global DATA_CACHE
    DATA_CACHE['status'] = "Atualizando..."
    logger.info("🔄 Iniciando atualização de dados...")
    
    date_str = get_api_date_str()
    br_tz = timezone(timedelta(hours=-3))
    
    # FUTEBOL
    soccer_list = []
    leagues = {'bra.1': '🇧🇷 Brasileirão', 'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'bra.copa_do_brasil': '🏆 Copa BR'}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
                            venue = event['competitions'][0].get('venue', {}).get('fullName', 'Local a definir')
                            
                            broadcasts = event['competitions'][0].get('broadcasts', [])
                            tv = broadcasts[0]['names'][0] if broadcasts else ("Premiere / Globo" if 'bra' in code else "")
                            
                            dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                            
                            soccer_list.append({
                                "id": event['id'], "raw": event,
                                "match": f"{home} x {away}", "home": home, "away": away,
                                "time": dt.strftime("%H:%M"), "league": name,
                                "status": status, "clock": clock,
                                "score_home": sh, "score_away": sa,
                                "venue": venue, "tv": tv
                            })
                except: pass
    except: pass
    
    # SALVA FUTEBOL
    if soccer_list:
        DATA_CACHE['soccer'] = sorted(soccer_list, key=lambda x: x['time'])

    # UFC
    ufc_list = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
                            "red_odds": "-200", "blue_odds": "+150"
                        })
    except: pass
    if ufc_list: DATA_CACHE['ufc'] = ufc_list

    DATA_CACHE['status'] = "Online"
    DATA_CACHE['last_update'] = datetime.now()
    logger.info("✅ Dados Atualizados!")

# --- 6. LOOPS ---

async def master_loop(app):
    while True:
        await update_data()
        
        # Alertas simples
        if DATA_CACHE['soccer']:
            for g in DATA_CACHE['soccer']:
                gid = g['id']; sh = g['score_home']; sa = g['score_away']
                if gid not in ALERT_MEMORY:
                    ALERT_MEMORY[gid] = {'h': sh, 'a': sa}
                    continue
                if g['status'] == 'in' and (sh > ALERT_MEMORY[gid]['h'] or sa > ALERT_MEMORY[gid]['a']):
                    msg = f"⚽ <b>GOL!</b> {g['match']} ({sh}-{sa})"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                ALERT_MEMORY[gid] = {'h': sh, 'a': sa}
        
        await asyncio.sleep(60)

# --- 7. BOT ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    status_icon = "🟢" if DATA_CACHE['status'] == "Online" else "🟠"
    await u.message.reply_text(
        f"🦁 <b>PAINEL V333</b>\nStatus: {status_icon} {DATA_CACHE['status']}\n\nClique abaixo:", 
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚽ Grade", callback_data="fut"), InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
            [InlineKeyboardButton("🎫 Bilhete", callback_data="ticket"), InlineKeyboardButton("🏀 NBA", callback_data="nba")]
        ]), 
        parse_mode=ParseMode.HTML
    )

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if DATA_CACHE['status'] != "Online" and not DATA_CACHE['soccer']:
        await q.message.reply_text("🟠 O sistema está inicializando e baixando dados... Tente em 10 segundos.")
        await update_data() # Força update
        return

    if q.data == "fut":
        if not DATA_CACHE['soccer']:
            await q.message.reply_text("❌ Nenhum jogo encontrado na API hoje.")
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
        if not DATA_CACHE['ufc']:
            await q.message.reply_text("❌ Sem UFC na API.")
            return
        txt = "🥊 <b>UFC CARD</b>\n\n"
        for f in DATA_CACHE['ufc']: txt += format_ufc_card(f)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ticket":
        cands = [g for g in DATA_CACHE['soccer'] if g['status'] != 'post']
        valid = []
        for g in cands:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            d = odds_list[0].get('details', '-') if odds_list else '-'
            p, o, _, fav = parse_odds_string(d, g['home'], g['away'])
            if fav and 1.15 <= o <= 2.20: valid.append({'m': g['match'], 'p': p, 'o': o})
        
        if len(valid) < 2:
            await q.message.reply_text("❌ Jogos insuficientes para bilhete.")
            return
            
        random.shuffle(valid)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        tot = 1.0
        for v in valid[:3]:
            tot *= v['o']
            msg += f"✅ <b>{v['m']}</b>\n🎯 {v['p']} (@{v['o']:.2f})\n\n"
        msg += f"🔥 <b>TOTAL: {tot:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

# --- STARTUP ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE V333")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    # NÃO BLOQUEIA O STARTUP. RODA EM SEGUNDO PLANO.
    asyncio.create_task(master_loop(app))
    
def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    print("✅ BOT V333 INICIADO! AGUARDANDO COMANDOS...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
