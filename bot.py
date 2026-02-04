import os
import sys
import json
import asyncio
import logging
import secrets
import time
import random
from datetime import datetime, timedelta, timezone

# --- AUTO-INSTALAÇÃO ---
try:
    import httpx
    import google.generativeai as genai
    from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode
    from telegram.error import Conflict, NetworkError
    from aiohttp import web
except ImportError:
    import subprocess
    print("⚠️ Instalando libs...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "httpx", "google-generativeai", "aiohttp"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 10000))
DB_FILE = "dvd_tips_v25.json"

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

# ================= SERVIDOR WEB (KEEP-ALIVE) =================
async def health_check(request): return web.Response(text="BOT V25 RODANDO")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌍 Web Server na porta {PORT}")

async def run_pinger():
    if not RENDER_URL: return
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(600)
            try: await client.get(RENDER_URL)
            except: pass

# ================= MOTOR DE ODDS =================
async def get_real_matches():
    logger.info("🔍 Iniciando busca de jogos...")
    cache = db_data.get("api_cache", {})
    if cache.get("ts"):
        last = datetime.fromisoformat(cache["ts"])
        if (datetime.now() - last).total_seconds() < 900: 
            logger.info("✅ Usando Cache Local")
            return cache["matches"]

    if not API_FOOTBALL_KEY: 
        logger.error("❌ ERRO: API Key não encontrada!")
        return []

    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
    headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
    
    matches = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            logger.info(f"📡 Chamando API para data: {today}")
            r_foot, r_nba = await asyncio.gather(
                client.get(f"https://v3.football.api-sports.io/fixtures?date={today}", headers=headers),
                client.get(f"https://v1.basketball.api-sports.io/games?date={today}", headers=headers_nba),
                return_exceptions=True
            )

            # FUTEBOL
            if not isinstance(r_foot, Exception) and r_foot.status_code == 200:
                data = r_foot.json().get("response", [])
                logger.info(f"⚽ Futebol: {len(data)} jogos brutos encontrados.")
                
                VIP_IDS = [39,40,41,42,45,48, 140,141,143, 78,79,529, 135,136,137, 61,62,66, 71,72,73, 475,476,477,478,479,480, 2,3,13,11,848,15, 94,88,203,128]
                for g in data:
                    if g["league"]["id"] not in VIP_IDS: continue
                    ts = g["fixture"]["timestamp"]
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
                data = r_nba.json().get("response", [])
                logger.info(f"🏀 NBA: {len(data)} jogos brutos encontrados.")
                for g in data:
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

        except Exception as e: logger.error(f"❌ Erro na API: {e}")

    if matches:
        matches.sort(key=lambda x: x["ts"])
        logger.info(f"✅ Total final de jogos processados: {len(matches)}")
        async with db_lock:
            db_data["api_cache"] = {"matches": matches, "ts": datetime.now().isoformat()}
    else:
        logger.warning("⚠️ Nenhum jogo processado após filtros.")
    
    return matches

def get_multiple(matches):
    if not matches or len(matches) < 4: return None
    sel = random.sample(matches, 4)
    total = 1.0
    for m in sel: total *= m["odd"]
    return {"games": sel, "total": round(total, 2)}

# ================= HANDLERS =================
def main_kb():
    return ReplyKeyboardMarkup([["📋 Jogos de Hoje", "🚀 Múltipla 20x"], ["🤖 Guru IA", "🎫 Meu Status"], ["/admin"]], resize_keyboard=True)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    async with db_lock:
        if uid not in db_data["users"]:
            db_data["users"][uid] = {"vip": None}
            await save_db()
    logger.info(f"Usuário {uid} iniciou o bot.")
    await u.message.reply_text("👋 **DVD TIPS V25.0**\nBot Reiniciado com Sucesso!", reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

async def show_games(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg = await u.message.reply_text("🔄 Consultando API...")
    m = await get_real_matches()
    if not m: return await msg.edit_text("📭 Sem jogos na lista VIP hoje.")
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

async def text_handle(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("❓ Use os botões abaixo.")

# ================= MAIN =================
async def main():
    if not TOKEN: sys.exit("Falta TOKEN")
    await load_db()
    
    # Configuração BLINDADA
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📋"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀"), show_multi))
    app.add_handler(MessageHandler(filters.Regex("^🎫"), show_status))
    app.add_handler(MessageHandler(filters.TEXT, text_handle))

    print("🔥 INICIANDO BOT V25...")
    
    # Inicia WebServer
    await start_web_server()
    asyncio.create_task(run_pinger())
    
    # Loop de Conexão Infinita (Anti-Crash)
    while True:
        try:
            print("📡 Conectando ao Telegram...")
            await app.bot.delete_webhook(drop_pending_updates=True)
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            # Mantém rodando
            while True: await asyncio.sleep(3600)
            
        except Conflict:
            print("⛔ CONFLITO DETECTADO! (Outra instância está ativa).")
            print("⏳ Esperando 15s antes de tentar de novo...")
            await asyncio.sleep(15)
        except Exception as e:
            print(f"❌ Erro: {e}")
            await asyncio.sleep(5)
        finally:
            try: await app.updater.stop()
            except: pass

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass