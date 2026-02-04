import os
import sys
import json
import asyncio
import logging
import secrets
import random
from datetime import datetime, timedelta, timezone

# --- AUTO-INSTALAÇÃO ---
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
ADMIN_ID = os.getenv("ADMIN_ID") # CERTIFIQUE-SE QUE ISSO ESTÁ CERTO NO RENDER
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.environ.get("PORT", 10000))
DB_FILE = "dvd_tips_v29.json"

# LOGS EXTREMOS PARA DIAGNÓSTICO
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

# ================= WEB SERVER (SEGURA O RENDER) =================
async def health_check(request):
    return web.Response(text="SISTEMA V29 ONLINE")

async def start_web_service():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ [WEB] Servidor rodando na porta {PORT}")

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
            # Processamento simplificado para economizar espaço
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
    return ReplyKeyboardMarkup([["📋 Jogos de Hoje", "🚀 Múltipla 20x"], ["🎫 Meu Status", "/admin"]], resize_keyboard=True)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 COMANDO START RECEBIDO DE: {u.effective_user.id}")
    uid = str(u.effective_user.id)
    async with db_lock:
        if uid not in db_data["users"]:
            db_data["users"][uid] = {"vip": None}
            await save_db()
    await u.message.reply_text("👋 **DVD TIPS V29**\nEstou te ouvindo!", reply_markup=main_kb(), parse_mode=ParseMode.MARKDOWN)

async def show_games(u: Update, c: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 PEDIDO DE JOGOS DE: {u.effective_user.id}")
    msg = await u.message.reply_text("🔄 ...")
    m = await get_real_matches()
    if not m: return await msg.edit_text("📭 Grade Vazia.")
    txt = "*📋 GRADE HOJE:*\n\n"
    for g in m[:25]: txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
    await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_multi(u: Update, c: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 PEDIDO DE MÚLTIPLA DE: {u.effective_user.id}")
    m = await get_real_matches()
    multi = get_multiple(m)
    if not multi: return await u.message.reply_text("⚠️ Poucos jogos.")
    txt = "*🚀 MÚLTIPLA:*\n\n"
    for g in multi["games"]: txt += f"• {g['sport']} {g['match']} ({g['tip']})\n"
    txt += f"\n💰 *ODD: {multi['total']}*"
    await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def show_status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 PEDIDO DE STATUS DE: {u.effective_user.id}")
    uid = str(u.effective_user.id)
    vip = db_data["users"].get(uid, {}).get("vip")
    await u.message.reply_text(f"*🎫 STATUS:* {'✅ VIP' if vip else '❌ Free'}", parse_mode=ParseMode.MARKDOWN)

async def text_handle(u: Update, c: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📩 TEXTO RECEBIDO: {u.message.text}")
    await u.message.reply_text("❓ Botão.")

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

# ================= EXECUÇÃO PRINCIPAL =================
async def main():
    if not TOKEN: sys.exit("❌ ERRO: Faltam variáveis.")
    await load_db()

    # 1. Site Fake (Prioridade)
    await start_web_service()

    # 2. Configura Bot
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmds))
    app.add_handler(CommandHandler("ativar", activate))
    app.add_handler(MessageHandler(filters.Regex("^📋"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀"), show_multi))
    app.add_handler(MessageHandler(filters.Regex("^🎫"), show_status))
    app.add_handler(CallbackQueryHandler(admin_cb))
    app.add_handler(MessageHandler(filters.TEXT, text_handle))

    logger.info("🔥 INICIANDO BOT V29 (DIAGNÓSTICO)...")
    
    # 3. Tentativa de Enviar Mensagem de Boas Vindas ao Admin (TESTE DE VIDA)
    if ADMIN_ID:
        try:
            logger.info(f"📤 Tentando enviar sinal de vida para Admin: {ADMIN_ID}")
            async with app.bot:
                await app.bot.send_message(chat_id=ADMIN_ID, text="🤖 **ESTOU VIVO!**\nO código rodou. Se você está vendo isso, o problema de envio foi resolvido.", parse_mode=ParseMode.MARKDOWN)
                logger.info("✅ SINAL DE VIDA ENVIADO COM SUCESSO!")
        except Exception as e:
            logger.error(f"❌ FALHA AO ENVIAR SINAL DE VIDA: {e}")
            logger.error("⚠️ Isso indica que o Token está errado ou o Admin ID não iniciou o bot ainda.")

    # 4. Loop de Conexão
    while True:
        try:
            # Limpa webhook velho
            await app.bot.delete_webhook(drop_pending_updates=True)
            logger.info("📡 Conectando (Polling)...")
            
            # Inicia
            await app.initialize()
            await app.start()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            while True: 
                logger.info("💓 Batimento cardíaco (Bot Online)...")
                await asyncio.sleep(60) # Loga a cada minuto que está vivo
            
        except Conflict:
            logger.error("🚨 CONFLITO CRÍTICO! Outra instância ainda está rodando.")
            logger.error("💤 Dormindo 30s...")
            try: await app.updater.stop(); await app.stop(); await app.shutdown()
            except: pass
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"❌ Erro Geral: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass