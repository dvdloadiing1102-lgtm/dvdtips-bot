import os
import sys
import json
import asyncio
import logging
import secrets
import random
from datetime import datetime, timedelta, timezone

# --- AUTO-INSTALAÇÃO DE DEPENDÊNCIAS ---
try:
    import httpx
    import google.generativeai as genai
    from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ApplicationBuilder
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
PORT = int(os.environ.get("PORT", 10000))
DB_FILE = "dvd_tips_v31.json"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
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

# ================= MOTOR DE ODDS =================
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
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            r_ft, r_bk = await asyncio.gather(
                client.get(f"https://v3.football.api-sports.io/fixtures?date={today}", headers=headers),
                client.get(f"https://v1.basketball.api-sports.io/games?date={today}", headers=headers_nba),
                return_exceptions=True
            )
            # FUTEBOL
            if not isinstance(r_ft, Exception) and r_ft.status_code == 200:
                VIP_IDS = [39,40,41,42,48, 140,141,143, 78,79,529, 135,136,137, 61,62,66, 71,72,73, 475,479, 2,3,13,11, 203,128]
                for g in r_ft.json().get("response", []):
                    if g["league"]["id"] not in VIP_IDS: continue
                    ts = g["fixture"]["timestamp"]
                    if datetime.fromtimestamp(ts) < datetime.now() - timedelta(hours=4): continue
                    matches.append({
                        "sport": "⚽", "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                        "league": g["league"]["name"], "time": (datetime.fromtimestamp(ts, tz=timezone.utc)-timedelta(hours=3)).strftime("%H:%M"),
                        "odd": round(random.uniform(1.5, 2.5), 2), "tip": "Over 2.5" if random.random() > 0.5 else "Casa", "ts": ts
                    })
            # NBA
            if not isinstance(r_bk, Exception) and r_bk.status_code == 200:
                for g in r_bk.json().get("response", []):
                    if g["league"]["id"] != 12: continue
                    ts = g["timestamp"]
                    matches.append({
                        "sport": "🏀", "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                        "league": "NBA", "time": (datetime.fromtimestamp(ts, tz=timezone.utc)-timedelta(hours=3)).strftime("%H:%M"),
                        "odd": round(random.uniform(1.4, 2.2), 2), "tip": "Casa", "ts": ts
                    })
        except: pass

    if matches:
        matches.sort(key=lambda x: x["ts"])
        async with db_lock: db_data["api_cache"] = {"matches": matches, "ts": datetime.now().isoformat()}
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
    await u.message.reply_text("👋 **DVD TIPS V31**\nReiniciado e Limpo!", reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

async def show_games(u: Update, c: ContextTypes.DEFAULT_TYPE):
    msg = await u.message.reply_text("🔄 ...")
    m = await get_real_matches()
    if not m: return await msg.edit_text("📭 Vazio.")
    txt = "*📋 GRADE HOJE:*\n\n"
    for g in m[:25]: txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
    await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_multi(u: Update, c: ContextTypes.DEFAULT_TYPE):
    m = await get_real_matches()
    multi = get_multiple(m)
    if not multi: return await u.message.reply_text("⚠️ Poucos jogos.")
    txt = "*🚀 MÚLTIPLA:*\n\n"
    for g in multi["games"]: txt += f"• {g['sport']} {g['match']} ({g['tip']})\n"
    txt += f"\n💰 *ODD: {multi['total']}*"
    await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    uid = str(u.effective_user.id)
    vip = db_data["users"].get(uid, {}).get("vip")
    await u.message.reply_text(f"*🎫 STATUS:* {'✅ VIP' if vip else '❌ Free'}", parse_mode=ParseMode.MARKDOWN)

async def guru(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🤖 Pergunte:")
    c.user_data["guru"] = True

async def text_handle(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if c.user_data.get("guru"):
        c.user_data["guru"] = False
        if not GEMINI_API_KEY: return await u.message.reply_text("❌ IA Off.")
        msg = await u.message.reply_text("🤔 ...")
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            res = await asyncio.to_thread(model.generate_content, u.message.text)
            await msg.edit_text(f"🎓 *Guru:*\n{res.text}", parse_mode=ParseMode.MARKDOWN)
        except: await msg.edit_text("Erro IA.")
    else: await u.message.reply_text("❓ Menu")

async def admin_cmds(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Key", callback_data="add")]])
    await u.message.reply_text("🔑 Admin", reply_markup=kb)

async def admin_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "add":
        k = "VIP-" + secrets.token_hex(4).upper()
        async with db_lock:
            db_data["keys"][k] = {"exp": "2030-12-31", "used": None}
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
            await u.message.reply_text("✅ OK!")
        else: await u.message.reply_text("❌ Erro.")

# ================= WEB SERVER (SEGURA O RENDER) =================
async def health_check(request): return web.Response(text="V31 ONLINE")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ WEB SERVER: Porta {PORT}")

# ================= MAIN (A FÊNIX) =================
async def main():
    if not TOKEN: sys.exit("Falta TOKEN")
    await load_db()
    
    # 1. Inicia o Site (Para o Render não desligar)
    await start_web()

    # 2. Loop de Reinicialização (Fênix)
    logger.info("🤖 Iniciando Loop V31...")
    
    while True:
        app = None # Garante que está vazio
        try:
            # CRIAMOS UM BOT NOVO A CADA LOOP (O segredo está aqui)
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

            # Inicializa e Roda
            logger.info("🔌 Conectando ao Telegram...")
            await app.initialize()
            await app.start()
            
            # Limpa qualquer webhook velho que esteja travando
            await app.bot.delete_webhook(drop_pending_updates=True)
            
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            logger.info("✅ Bot V31 Operacional!")
            
            # Mantém vivo enquanto não der erro
            while True: 
                await asyncio.sleep(600)
                # Verifica se o bot ainda está rodando internamente
                if not app.updater.running:
                    raise RuntimeError("Updater parou sozinho!")

        except Conflict:
            logger.error("🚨 CONFLITO! Outro bot está usando a chave.")
            logger.error("💤 Reiniciando em 20s...")
            # Encerra o bot atual antes de criar o novo
            if app:
                try: await app.updater.stop(); await app.stop(); await app.shutdown()
                except: pass
            await asyncio.sleep(20)
            
        except Exception as e:
            logger.error(f"❌ Erro Geral: {e}")
            if app:
                try: await app.updater.stop(); await app.stop(); await app.shutdown()
                except: pass
            await asyncio.sleep(5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass