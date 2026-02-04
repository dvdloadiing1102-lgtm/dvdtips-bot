import os
import sys
import json
import asyncio
import logging
import secrets
import time
import random
from datetime import datetime, timedelta, timezone

# --- AUTO-INSTALAÇÃO DE DEPENDÊNCIAS ---
try:
    import httpx
    import google.generativeai as genai
    from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode
    from telegram.error import Conflict, NetworkError, TimedOut
    from aiohttp import web
except ImportError:
    import subprocess
    print("⚠️ Instalando bibliotecas...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "httpx", "google-generativeai", "aiohttp"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 10000))
DB_FILE = "dvd_tips_v24.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BANCO DE DADOS =================
db_data = {}
db_lock = asyncio.Lock()

async def load_db():
    global db_data
    if not os.path.exists(DB_FILE):
        db_data = {"users": {}, "keys": {}, "api_cache": {}}
        return
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f: db_data = json.load(f)
    except: db_data = {"users": {}, "keys": {}, "api_cache": {}}

async def save_db():
    async with db_lock:
        try:
            with open(DB_FILE, "w", encoding="utf-8") as f: json.dump(db_data, f, indent=2)
        except: pass

# ================= SERVIDOR WEB (A âncora que segura o Render) =================
async def health_check(request): return web.Response(text="V24 ONLINE")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌍 Web Server BLINDADO rodando na porta {PORT}")
    # Loop infinito para manter o servidor vivo
    while True: await asyncio.sleep(3600)

async def run_pinger():
    if not RENDER_URL: return
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(600)
            try: await client.get(RENDER_URL)
            except: pass

# ================= MOTOR DE ODDS =================
async def get_real_matches():
    # Cache
    cache = db_data.get("api_cache", {})
    if cache.get("ts"):
        last = datetime.fromisoformat(cache["ts"])
        if (datetime.now() - last).total_seconds() < 900: return cache["matches"]

    if not API_FOOTBALL_KEY: return []

    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
    headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
    
    matches = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r_foot, r_nba = await asyncio.gather(
                client.get(f"https://v3.football.api-sports.io/fixtures?date={today}", headers=headers),
                client.get(f"https://v1.basketball.api-sports.io/games?date={today}", headers=headers_nba),
                return_exceptions=True
            )

            # FUTEBOL
            if not isinstance(r_foot, Exception) and r_foot.status_code == 200:
                VIP_IDS = [39,40,41,42,45,48, 140,141,143, 78,79,529, 135,136,137, 61,62,66, 71,72,73, 475,476,477,478,479,480, 2,3,13,11,848,15, 94,88,203,128]
                for g in r_foot.json().get("response", []):
                    if g["league"]["id"] not in VIP_IDS: continue
                    ts = g["fixture"]["timestamp"]
                    if datetime.fromtimestamp(ts) < datetime.now() - timedelta(hours=5): continue
                    
                    matches.append({
                        "sport": "⚽",
                        "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                        "league": g["league"]["name"],
                        "time": (datetime.fromtimestamp(ts, tz=timezone.utc)-timedelta(hours=3)).strftime("%H:%M"),
                        "odd": round(random.uniform(1.5, 2.5), 2),
                        "tip": "Over 2.5 Gols" if random.random() > 0.5 else f"Vence {g['teams']['home']['name']}",
                        "ts": ts
                    })

            # NBA
            if not isinstance(r_nba, Exception) and r_nba.status_code == 200:
                for g in r_nba.json().get("response", []):
                    if g["league"]["id"] != 12: continue
                    ts = g["timestamp"]
                    matches.append({
                        "sport": "🏀",
                        "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                        "league": "NBA",
                        "time": (datetime.fromtimestamp(ts, tz=timezone.utc)-timedelta(hours=3)).strftime("%H:%M"),
                        "odd": round(random.uniform(1.4, 2.2), 2),
                        "tip": "Over 215.5" if random.random() > 0.5 else f"Vence {g['teams']['home']['name']}",
                        "ts": ts
                    })
        except: pass

    if matches:
        matches.sort(key=lambda x: x["ts"])
        async with db_lock:
            db_data["api_cache"] = {"matches": matches, "ts": datetime.now().isoformat()}
    return matches

def get_multiple(matches):
    if not matches or len(matches) < 4: return None
    sel = random.sample(matches, 4)
    total = 1.0
    for m in sel: total *= m["odd"]
    return {"games": sel, "total": round(total, 2)}

# ================= BOT E HANDLERS =================
def main_kb():
    return ReplyKeyboardMarkup([["📋 Jogos de Hoje", "🚀 Múltipla 20x"], ["🤖 Guru IA", "🎫 Meu Status"], ["/admin"]], resize_keyboard=True)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    async with db_lock:
        if uid not in db_data["users"]:
            db_data["users"][uid] = {"vip": None}
            await save_db()
    await u.message.reply_text("👋 **DVD TIPS V24.0**\nOnline!", reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

async def show_games(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg = await u.message.reply_text("🔄 Buscando...")
    m = await get_real_matches()
    if not m: return await msg.edit_text("📭 Sem jogos (Verifique API).")
    txt = "*📋 GRADE HOJE:*\n\n"
    for g in m[:20]: txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
    await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_multi(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await get_real_matches()
    multi = get_multiple(m)
    if not multi: return await u.message.reply_text("⚠️ Jogos insuficientes.")
    txt = "*🚀 MÚLTIPLA:*\n\n"
    for g in multi["games"]: txt += f"• {g['sport']} {g['match']} ({g['tip']})\n"
    txt += f"\n💰 *ODD: {multi['total']}*"
    await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    vip = db_data["users"].get(uid, {}).get("vip")
    await u.message.reply_text(f"*🎫 PERFIL*\nStatus: {'✅ VIP' if vip else '❌ Grátis'}", parse_mode=ParseMode.MARKDOWN)

async def admin_cmds(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Key", callback_data="add")]])
    await u.message.reply_text("🔑 **Admin**", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def admin_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    if q.data == "add":
        k = "VIP-" + secrets.token_hex(4).upper()
        async with db_lock:
            db_data["keys"][k] = {"exp": "2030-01-01", "used": None}
            await save_db()
        await q.edit_message_text(f"🔑 `{k}`", parse_mode=ParseMode.MARKDOWN)

async def activate(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try: k = c.args[0]
    except: return await u.message.reply_text("Use `/ativar CHAVE`")
    async with db_lock:
        if k in db_data["keys"] and not db_data["keys"][k]["used"]:
            db_data["keys"][k]["used"] = str(u.effective_user.id)
            db_data["users"][str(u.effective_user.id)]["vip"] = db_data["keys"][k]["exp"]
            await save_db()
            await u.message.reply_text("✅ VIP Ativado!")
        else: await u.message.reply_text("❌ Erro.")

# ================= LÓGICA DE RECONEXÃO (ANTI-CRASH) =================
async def bot_loop():
    """Tenta conectar o bot. Se falhar, espera e tenta de novo SEM CAIR O APP."""
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmds))
    app.add_handler(CommandHandler("ativar", activate))
    app.add_handler(MessageHandler(filters.Regex("^📋"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀"), show_multi))
    app.add_handler(MessageHandler(filters.Regex("^🎫"), show_status))
    app.add_handler(CallbackQueryHandler(admin_cb))

    print("🤖 Iniciando Loop do Bot...")
    
    while True:
        try:
            # Tenta limpar updates velhos que estão travando
            await app.bot.delete_webhook(drop_pending_updates=True)
            
            # Inicia o Polling
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
            print("✅ Bot Conectado ao Telegram!")
            
            # Mantém rodando até o updater parar (o que não deve acontecer sozinho)
            while True: await asyncio.sleep(3600)
            
        except Conflict:
            print("⚠️ CONFLITO DETECTADO! Outra instância está rodando.")
            print("⏳ Esperando 15 segundos para o 'zumbi' morrer...")
            await asyncio.sleep(15)
        except Exception as e:
            print(f"❌ Erro de conexão: {e}")
            print("⏳ Tentando reconectar em 10s...")
            await asyncio.sleep(10)
        finally:
            try: await app.updater.stop()
            except: pass

# ================= MAIN =================
async def main():
    if not TOKEN: sys.exit("Falta TOKEN")
    await load_db()
    
    # Inicia o Servidor Web e o Bot em paralelo
    # O Servidor Web segura o Render. O Bot roda protegido dentro do loop.
    await asyncio.gather(
        start_web_server(),
        run_pinger(),
        bot_loop()
    )

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass