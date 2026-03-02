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

DATA_CACHE = {"soccer": [], "nba": [], "ufc": [], "last_update": None}
ALERT_MEMORY = {}
LAST_GRADE_SENT = None

# --- HELPERS ---

def american_to_decimal(american_str):
    try:
        if "EV" in str(american_str).upper():
            return 2.00
        val = float(american_str)
        if val == 0:
            return 1.0
        if val < 0:
            return round((100 / abs(val)) + 1, 2)
        return round((val / 100) + 1, 2)
    except:
        return 0.0

def get_br_now():
    return datetime.now(timezone(timedelta(hours=-3)))

def safe_html(text):
    return html.escape(str(text)) if text else ""

# --- UPDATE DATA ---

async def update_data():
    global DATA_CACHE
    br_tz = timezone(timedelta(hours=-3))
    date_str = get_br_now().strftime("%Y%m%d")

    soccer_list = []
    leagues = {
        'bra.1': '🇧🇷 Brasileirão',
        'uefa.champions': '🇪🇺 UCL'
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}"
                r = await client.get(url)
                if r.status_code != 200:
                    continue

                data = r.json()
                for event in data.get("events", []):
                    comp = event["competitions"][0]["competitors"]

                    home = comp[0]["team"]["name"]
                    away = comp[1]["team"]["name"]

                    sh = int(comp[0].get("score") or 0)
                    sa = int(comp[1].get("score") or 0)

                    dt = datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ").replace(
                        tzinfo=timezone.utc).astimezone(br_tz)

                    soccer_list.append({
                        "id": event["id"],
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "score_home": sh,
                        "score_away": sa,
                        "time": dt.strftime("%H:%M"),
                        "status": event["status"]["type"]["state"],
                        "clock": event["status"]["type"]["detail"]
                    })
            except Exception as e:
                logger.error(f"Erro futebol: {e}")

    # UFC (corrigido)
    ufc_list = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard")
            if r.status_code == 200:
                data = r.json()
                for event in data.get("events", []):
                    for comp in event.get("competitions", []):
                        fighters = comp.get("competitors", [])
                        if len(fighters) < 2:
                            continue

                        red = fighters[0]["athlete"]["fullName"]
                        blue = fighters[1]["athlete"]["fullName"]

                        ufc_list.append({
                            "red": red,
                            "blue": blue,
                            "time": "Em breve",
                            "venue": comp.get("venue", {}).get("fullName", "Arena UFC")
                        })
    except Exception as e:
        logger.error(f"Erro UFC: {e}")

    DATA_CACHE["soccer"] = soccer_list
    DATA_CACHE["ufc"] = ufc_list
    DATA_CACHE["last_update"] = get_br_now()

# --- ALERTAS ---

async def check_alerts(app):
    global ALERT_MEMORY
    for game in DATA_CACHE["soccer"]:
        gid = game["id"]
        sh = game["score_home"]
        sa = game["score_away"]

        if gid not in ALERT_MEMORY:
            ALERT_MEMORY[gid] = (sh, sa)
            continue

        old_sh, old_sa = ALERT_MEMORY[gid]

        if sh > old_sh or sa > old_sa:
            scorer = game["home"] if sh > old_sh else game["away"]
            msg = f"⚽ <b>GOOOOL DO {safe_html(scorer.upper())}</b>\n{game['match']}\n{sh} - {sa}"
            try:
                await app.bot.send_message(CHANNEL_ID, msg)
            except:
                pass

        ALERT_MEMORY[gid] = (sh, sa)

# --- LOOP PRINCIPAL ---

async def master_loop(app):
    global LAST_GRADE_SENT

    while True:
        await update_data()
        await check_alerts(app)

        now = get_br_now()
        today_str = now.strftime("%d/%m/%Y")

        if now.hour == 8 and now.minute == 0:
            if LAST_GRADE_SENT != today_str:
                if DATA_CACHE["soccer"]:
                    txt = f"🦁 <b>GRADE VIP | {today_str}</b>\n\n"
                    for g in DATA_CACHE["soccer"]:
                        txt += f"{g['match']} - {g['time']}\n"
                    await app.bot.send_message(CHANNEL_ID, txt)
                LAST_GRADE_SENT = today_str

        await asyncio.sleep(60)

# --- TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 PAINEL V332 ONLINE")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ONLINE")

def run_server():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await update_data()
    asyncio.create_task(master_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()