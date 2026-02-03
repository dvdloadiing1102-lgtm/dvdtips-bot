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
DB_FILE = "dvd_tips_v22.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# IA
USE_GEMINI = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
        logger.info("✅ IA Gemini ON")
    except: logger.warning("⚠️ IA Gemini OFF")

# ================= BANCO DE DADOS =================
db_data = {}
db_lock = asyncio.Lock()

async def load_db():
    global db_data
    if not os.path.exists(DB_FILE):
        db_data = {"users": {}, "keys": {}, "api_cache": {}}
        return
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db_data = json.load(f)
    except: db_data = {"users": {}, "keys": {}, "api_cache": {}}

async def save_db():
    async with db_lock:
        try:
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(db_data, f, indent=2)
        except: pass

# ================= SERVIDOR WEB (ANTI-CRASH) =================
async def health_check(request): return web.Response(text="OK")

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

# ================= UTILITÁRIOS =================
last_action = {}
async def check_flood(uid):
    now = time.time()
    if now - last_action.get(uid, 0) < 1.5: return True
    last_action[uid] = now
    return False

def generate_key():
    return "VIP-" + secrets.token_hex(4).upper(), (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

# ================= MOTOR DE ODDS (FUTEBOL + NBA) =================
# Cache de 15 min para não estourar a API
async def get_real_matches():
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
            # Puxa Futebol e Basquete ao mesmo tempo
            r_foot, r_nba = await asyncio.gather(
                client.get(f"https://v3.football.api-sports.io/fixtures?date={today}", headers=headers),
                client.get(f"https://v1.basketball.api-sports.io/games?date={today}", headers=headers_nba),
                return_exceptions=True
            )

            # --- FUTEBOL ---
            if not isinstance(r_foot, Exception) and r_foot.status_code == 200:
                # IDs: ING, ESP, ALE, ITA, FRA, BRA, POR, HOL, ARG, TUR, COPAS, LIBERTA, SULA
                VIP_IDS = [39,40,41,42,45,48, 140,141,143, 78,79,529, 135,136,137, 61,62,66, 71,72,73, 475,476,477,478,479,480, 2,3,13,11,848,15, 94,88,203,128]
                for g in r_foot.json().get("response", []):
                    if g["league"]["id"] not in VIP_IDS: continue
                    ts = g["fixture"]["timestamp"]
                    # Filtra jogos muito velhos (mais de 4h atrás)
                    if datetime.fromtimestamp(ts) < datetime.now() - timedelta(hours=4): continue
                    
                    matches.append({
                        "sport": "⚽",
                        "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                        "league": g["league"]["name"],
                        "time": (datetime.fromtimestamp(ts, tz=timezone.utc)-timedelta(hours=3)).strftime("%H:%M"),
                        "odd": round(random.uniform(1.5, 2.5), 2),
                        "tip": "Over 2.5 Gols" if random.random() > 0.5 else f"Vence {g['teams']['home']['name']}",
                        "ts": ts
                    })

            # --- NBA ---
            if not isinstance(r_nba, Exception) and r_nba.status_code == 200:
                for g in r_nba.json().get("response", []):
                    if g["league"]["id"] != 12: continue # Só NBA
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

        except Exception as e: logger.error(f"Erro API: {e}")

    if matches:
        matches.sort(key=lambda x: x["ts"])
        async with db_lock:
            db_data["api_cache"] = {"matches": matches, "ts": datetime.now().isoformat()}
    
    return matches

def get_multiple(matches):
    if len(matches) < 4: return None
    sel = random.sample(matches, 4)
    total = 1.0
    for m in sel: total *= m["odd"]
    return {"games": sel, "total": round(total, 2)}

# ================= COMANDOS =================
def main_kb():
    return ReplyKeyboardMarkup([
        ["📋 Jogos de Hoje", "🚀 Múltipla 20x"],
        ["🤖 Guru IA", "🎫 Meu Status"],
        ["/admin"]
    ], resize_keyboard=True)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    if await check_flood(uid): return
    async with db_lock:
        if uid not in db_data["users"]:
            db_data["users"][uid] = {"vip": None}
            await save_db()
    await u.message.reply_text("👋 **DVD TIPS V22.0**\nEstabilidade Máxima!", reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

async def show_games(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if await check_flood(str(u.effective_user.id)): return
    await u.message.reply_text("🔄 Buscando grade...")
    m = await get_real_matches()
    if not m: return await u.message.reply_text("📭 Sem jogos agora.")
    
    txt = "*📋 GRADE HOJE (Futebol + NBA):*\n\n"
    for g in m[:20]:
        txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
    await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_multi(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await get_real_matches()
    multi = get_multiple(m)
    if not multi: return await u.message.reply_text("⚠️ Jogos insuficientes.")
    
    txt = "*🚀 MÚLTIPLA SUGERIDA:*\n\n"
    for g in multi["games"]:
        txt += f"• {g['sport']} {g['match']} ({g['tip']})\n"
    txt += f"\n💰 *ODD TOTAL: {multi['total']}*"
    await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    user = db_data["users"].get(uid, {})
    vip = user.get("vip")
    status = f"✅ VIP até {vip}" if vip else "❌ Grátis"
    await u.message.reply_text(f"*🎫 SEU PERFIL*\nID: `{uid}`\nStatus: {status}", parse_mode=ParseMode.MARKDOWN)

async def guru(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🤖 Mande sua pergunta:")
    c.user_data["guru"] = True

async def text_handle(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if c.user_data.get("guru"):
        c.user_data["guru"] = False
        if not USE_GEMINI: return await u.message.reply_text("❌ IA Indisponível.")
        msg = await u.message.reply_text("🤔 Pensando...")
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            res = await asyncio.to_thread(model.generate_content, u.message.text)
            await msg.edit_text(f"🎓 *Guru:*\n{res.text}", parse_mode=ParseMode.MARKDOWN)
        except: await msg.edit_text("Erro na IA.")
    elif c.user_data.get("del_key"):
        k = u.message.text.strip()
        c.user_data["del_key"] = False
        async with db_lock:
            if k in db_data["keys"]:
                del db_data["keys"][k]
                await save_db()
                await u.message.reply_text("✅ Deletada.")
            else: await u.message.reply_text("❌ Não existe.")
    else: await u.message.reply_text("❓ Use o menu.")

async def admin_cmds(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Criar Chave", callback_data="add")],
        [InlineKeyboardButton("🗑️ Deletar Chave", callback_data="del")]
    ])
    await u.message.reply_text("🔑 **Admin**", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def admin_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    if q.data == "add":
        k, exp = generate_key()
        async with db_lock:
            db_data["keys"][k] = {"exp": exp, "used": None}
            await save_db()
        await q.edit_message_text(f"🔑 Chave: `{k}`\nValidade: {exp}", parse_mode=ParseMode.MARKDOWN)
    elif q.data == "del":
        await q.edit_message_text("Mande a chave para deletar:")
        c.user_data["del_key"] = True

async def activate(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try: k = c.args[0]
    except: return await u.message.reply_text("Use: `/ativar CHAVE`")
    uid = str(u.effective_user.id)
    async with db_lock:
        if k in db_data["keys"] and not db_data["keys"][k]["used"]:
            exp = db_data["keys"][k]["exp"]
            db_data["keys"][k]["used"] = uid
            if uid not in db_data["users"]: db_data["users"][uid] = {}
            db_data["users"][uid]["vip"] = exp
            await save_db()
            await u.message.reply_text("✅ VIP Ativado!")
        else: await u.message.reply_text("❌ Chave inválida.")

# ================= MAIN =================
async def main():
    if not TOKEN: sys.exit("Falta TOKEN")
    await load_db()
    
    # RESFRIAMENTO (AQUI ESTÁ O SEGREDO DO CONFLITO)
    print("⏳ Resfriando conexão por 15s...")
    await asyncio.sleep(15) 
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmds))
    app.add_handler(CommandHandler("ativar", activate))
    app.add_handler(MessageHandler(filters.Regex("^📋"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀"), show_multi))
    app.add_handler(MessageHandler(filters.Regex("^🤖"), guru))
    app.add_handler(MessageHandler(filters.Regex("^🎫"), show_status))
    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.TEXT, text_handle))

    await app.initialize()
    await app.start()
    
    # Inicia Web Server e Pinger junto com o Bot
    await asyncio.gather(
        app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True),
        start_web_server(),
        run_pinger()
    )

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass