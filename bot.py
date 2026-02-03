#############################################
# config.py – Configurações e chaves
#############################################
import os

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips_v19_pro.json"

#############################################
# db.py – Banco de dados seguro com lock + logs
#############################################
import json, threading, datetime

db_lock = threading.Lock()

def load_db():
    default = {"users": {}, "keys": {}, "last_run": "", "api_cache": [], "api_cache_time": None, "logs": []}
    try:
        with open("dvd_tips_v19_pro.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_db(data):
    with db_lock:
        with open("dvd_tips_v19_pro.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

def log_action(user_id, action):
    db = load_db()
    db["logs"].append({"user": user_id, "action": action, "time": datetime.datetime.now().isoformat()})
    save_db(db)

#############################################
# utils.py – Funções VIP / Flood
#############################################
import secrets, datetime, time

def generate_vip_key(days=30):
    key = "VIP-" + secrets.token_hex(5).upper()
    expiry = (datetime.datetime.now() + datetime.timedelta(days=days)).isoformat()
    return key, expiry

def check_vip(uid, db):
    expiry = db["users"].get(uid, {}).get("vip_expiry")
    if not expiry:
        return False
    return datetime.datetime.fromisoformat(expiry) > datetime.datetime.now()

last_action_time = {}

def check_flood(uid, limit_seconds=3):
    now = time.time()
    last = last_action_time.get(uid, 0)
    if now - last < limit_seconds:
        return True
    last_action_time[uid] = now
    return False

#############################################
# webserver.py – HTTP + Pinger Render
#############################################
import threading, time, requests
from http.server import HTTPServer, BaseHTTPRequestHandler

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DVD TIPS V19 PRO ONLINE")
        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except:
        pass

def run_pinger(RENDER_URL):
    if not RENDER_URL:
        return
    while True:
        time.sleep(600)
        try:
            requests.get(RENDER_URL, timeout=10)
        except:
            pass

#############################################
# ai_service.py – Gemini IA + fallback
#############################################
import asyncio, random

USE_GEMINI = False
BACKUP_PHRASES = [
    "Favorito claro, odds indicam vitória tranquila.",
    "Jogo equilibrado, tendência de empate ou under.",
    "Ataques eficientes, boa chance para Over 2.5.",
    "Time da casa muito forte em seus domínios.",
    "Odd de valor identificada, vale a entrada."
]

try:
    import google.generativeai as genai
    from config import GEMINI_API_KEY
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
except:
    USE_GEMINI = False

async def get_smart_analysis(match, tip="", mode="tip"):
    if not USE_GEMINI:
        return random.choice(BACKUP_PHRASES)
    prompts = {
        "tip": f"Jogo: {match}. Tip: {tip}. Justifique em 1 frase técnica.",
        "guru": f"Responda curto sobre apostas: {match}",
        "analise": f"Analise {match}. Vencedor e gols."
    }
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        loop = asyncio.get_running_loop()
        response = await asyncio.wait_for(loop.run_in_executor(None, model.generate_content, prompts[mode]), timeout=6)
        return response.text.strip() if response.text else random.choice(BACKUP_PHRASES)
    except:
        return random.choice(BACKUP_PHRASES)

#############################################
# football_service.py – API Football + Cache
#############################################
import httpx, math
from datetime import datetime, timedelta, timezone
from db import load_db, save_db
from config import API_FOOTBALL_KEY
import random

CACHE_TTL = 1800

def is_cache_valid(db):
    ts = db.get("api_cache_time")
    if not ts:
        return False
    last = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - last).total_seconds() < CACHE_TTL

async def get_real_matches(force=False):
    db = load_db()
    if not API_FOOTBALL_KEY:
        return []

    if not force and is_cache_valid(db):
        return db.get("api_cache", [])

    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    headers = {"x-rapidapi-host":"v3.football.api-sports.io","x-rapidapi-key":API_FOOTBALL_KEY}
    url = f"https://v3.football.api-sports.io/fixtures?date={today}&status=NS"

    matches = []
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("response", [])
        except:
            data = []

    VIP_LEAGUES = {39, 140, 61, 78, 135, 2, 3, 13, 11}

    for g in data:
        if g["league"]["id"] not in VIP_LEAGUES:
            continue
        home, away = g["teams"]["home"]["name"], g["teams"]["away"]["name"]
        ts = g["fixture"]["timestamp"]
        matches.append({
            "match": f"{home} x {away}",
            "league": g["league"]["name"],
            "time": datetime.fromtimestamp(ts).strftime("%H:%M"),
            "odd": round(random.uniform(1.5, 2.4), 2),
            "tip": random.choice([f"Vence {home}", "Over 2.5 Gols"])
        })

    matches.sort(key=lambda x: x["time"])
    db["api_cache"], db["api_cache_time"] = matches, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_db(db)
    return matches

def generate_multiple(matches, size=4):
    if len(matches) < size:
        return None
    selection = random.sample(matches, size)
    total_odd = round(math.prod(m["odd"] for m in selection), 2)
    return {"games": selection, "total_odd": total_odd}

#############################################
# main.py – Bot Telegram completo
#############################################
import asyncio, logging, threading
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from db import log_action
from webserver import start_web_server, run_pinger
from config import TOKEN, RENDER_URL
from football_service import get_real_matches, generate_multiple
from ai_service import get_smart_analysis
from utils import check_flood

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= Handlers =================
from telegram import ReplyKeyboardMarkup

def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🔮 Analisar Jogo", "🚀 Múltipla 20x"],
        ["🦓 Zebra do Dia", "🛡️ Aposta Segura"],
        ["💰 Gestão Banca", "🤖 Guru IA"],
        ["🏆 Ligas", "📋 Jogos de Hoje"],
        ["📚 Glossário", "🎫 Meu Status"]
    ], resize_keyboard=True)

async def start(update, context):
    uid = str(update.effective_user.id)
    if check_flood(uid):
        await update.message.reply_text("⚠️ Ação bloqueada: flood detectado")
        return
    log_action(uid, "start_bot")
    await update.message.reply_text("👋 **DVD TIPS V19 PRO**\nBot Online!", reply_markup=get_main_keyboard())

async def direct_multipla(update, context):
    tips = await get_real_matches()
    multi = generate_multiple(tips)
    if multi:
        txt = "🚀 **MÚLTIPLA DO DIA**\n\n"
        for m in multi['games']:
            txt += f"• {m['match']} ({m['tip']})\n"
        txt += f"\n💰 **ODD TOTAL: {multi['total_odd']:.2f}**"
        await update.message.reply_text(txt)
    else:
        await update.message.reply_text("⚠️ Poucos jogos para múltipla.")

# ================= Inicialização Bot =================
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=run_pinger, args=(RENDER_URL,), daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Múltipla 20x$"), direct_multipla))

    logger.info("🤖 DVD TIPS V19 PRO ONLINE")
    app.run_polling()