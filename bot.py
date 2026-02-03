import os
import sys
import json
import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

try:
    import httpx
    import google.generativeai as genai
    from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from aiohttp import web
except ImportError:
    import subprocess
    print("⚠️ Dependências não encontradas. Instalando...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "httpx", "google-generativeai", "aiohttp"])
        print("✅ Dependências instaladas.")
    except Exception as e:
        print(f"❌ Falha: {e}")
    sys.exit(1)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 10000))
DB_FILE = "dvd_tips_data.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

USE_GEMINI = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
        logger.info("✅ IA Gemini ativa.")
    except Exception as e:
        logger.warning(f"⚠️ Falha ao configurar IA: {e}")

db_data = {}
db_lock = asyncio.Lock()

async def load_db():
    global db_data
    default_db = {"users": {}, "keys": {}, "api_cache": {}}
    if not os.path.exists(DB_FILE):
        db_data = default_db
        return
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db_data = json.load(f)
        logger.info("DB carregado.")
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Erro ao carregar DB: {e}")
        db_data = default_db

async def save_db():
    async with db_lock:
        try:
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(db_data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Falha ao salvar DB: {e}")

async def health_check(request):
    return web.Response(text=f"Bot is alive! {datetime.now()}")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    try:
        await site.start()
        logger.info(f"Servidor web iniciado na porta {PORT}")
    except Exception as e:
        logger.error(f"Falha ao iniciar servidor: {e}")

async def run_pinger():
    if not RENDER_URL:
        logger.info("Pinger desativado.")
        return
    await asyncio.sleep(120)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(RENDER_URL, timeout=15)
                logger.info("Ping enviado.")
            except httpx.RequestError as e:
                logger.warning(f"Falha no ping: {e}")
            await asyncio.sleep(600)

last_action_time = {}

async def check_flood(update: Update, limit=2):
    user_id = str(update.effective_user.id)
    now = time.time()
    last = last_action_time.get(user_id, 0)
    if now - last < limit:
        await update.message.reply_text("⏳ Aguarde um momento.")
        return True
    last_action_time[user_id] = now
    return False

def generate_vip_key(days=30):
    key = "VIP-" + secrets.token_hex(4).upper()
    expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return key, expiry_date

CACHE_TTL = 1800

async def get_real_matches(force_refresh=False):
    cache = db_data.get("api_cache", {})
    if cache.get("timestamp") and not force_refresh:
        try:
            last_fetch = datetime.fromisoformat(cache["timestamp"])
            if (datetime.now() - last_fetch).total_seconds() < CACHE_TTL:
                return cache.get("matches", [])
        except:
            pass

    if not API_FOOTBALL_KEY:
        return []

    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
    url = f"https://v3.football.api-sports.io/fixtures?date={today}&status=NS"
    
    matches = []
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("response", [])
            VIP_LEAGUES = [39, 71, 72, 140, 61, 78, 135, 2, 3, 13, 11, 4, 9, 10]
            for game_data in data:
                if game_data["league"]["id"] not in VIP_LEAGUES and len(matches) > 15:
                    continue
                matches.append({
                    "match": f'{game_data["teams"]["home"]["name"]} vs {game_data["teams"]["away"]["name"]}',
                    "league": game_data["league"]["name"],
                    "time": datetime.fromtimestamp(game_data["fixture"]["timestamp"]).strftime("%H:%M"),
                })
        except Exception as e:
            logger.error(f"Erro API: {e}")
            return []

    if matches:
        matches.sort(key=lambda x: x["time"])
        async with db_lock:
            db_data["api_cache"] = {"matches": matches, "timestamp": datetime.now().isoformat()}

    return matches

def generate_multiple(matches, size=4):
    if not matches or len(matches) < size:
        return None
    selection = matches[:size]
    return {"games": selection}

async def ask_guru(text):
    if not USE_GEMINI:
        return "Guru IA indisponível."
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"Você é um especialista em apostas esportivas. Responda curto e direto (max 2 frases) sobre: {text}"
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro Guru: {e}")
        return "Erro ao consultar o Guru."

def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📋 Jogos de Hoje", "🚀 Múltipla"],
        ["🤖 Fale com o Guru", "🎫 Meu Status"],
        ["/admin"]
    ], resize_keyboard=True)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if await check_flood(update):
        return
    async with db_lock:
        if user_id not in db_data["users"]:
            db_data["users"][user_id] = {"vip_expiry": None}
    await update.message.reply_text("👋 Bem-vindo!", reply_markup=get_main_keyboard())

async def show_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update):
        return
    await update.message.reply_text("🔄 Buscando jogos...")
    matches = await get_real_matches()
    if not matches:
        await update.message.reply_text("📭 Nenhum jogo encontrado.")
        return
    msg = "📋 **JOGOS DE HOJE:**\n\n" + "\n".join([f"⏰ {m['time']} | {m['league']}\n⚽ {m['match']}\n" for m in matches[:15]])
    await update.message.reply_text(msg)

async def show_multiple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update):
        return
    matches = await get_real_matches()
    multi = generate_multiple(matches)
    if multi and multi["games"]:
        msg = "🚀 **MÚLTIPLA SUGERIDA**\n\n" + "\n".join([f"• {g['match']}" for g in multi['games']])
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("⚠️ Jogos insuficientes.")

async def show_leagues(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update):
        return
    matches = await get_real_matches()
    if matches:
        leagues = sorted(list(set([m['league'] for m in matches])))
        msg = "🏆 **Ligas com jogos hoje:**\n" + "\n".join([f"• {l}" for l in leagues])
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("📭 Nenhuma liga encontrada.")

async def guru_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update):
        return
    await update.message.reply_text("🤖 Qual é sua dúvida sobre apostas?")
    context.user_data['waiting_for_guru'] = True

async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update):
        return
    user_id = str(update.effective_user.id)
    user_data = db_data["users"].get(user_id, {})
    vip_expiry = user_data.get("vip_expiry", "N/A")
    msg = f"🎫 **SEU STATUS**\n\n**ID:** `{user_id}`\n"
    if vip_expiry:
        try:
            if datetime.strptime(vip_expiry, "%Y-%m-%d") > datetime.now():
                msg += f"**VIP:** Ativo até {vip_expiry}"
            else:
                msg += "**VIP:** Expirado"
        except:
            msg += "**VIP:** Inativo"
    else:
        msg += "**VIP:** Inativo"
    await update.message.reply_text(msg)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_for_guru'):
        context.user_data['waiting_for_guru'] = False
        question = update.message.text
        await update.message.reply_text("🤔 Analisando...")
        answer = await ask_guru(question)
        await update.message.reply_text(f"🎓 **Guru Responde:**\n{answer}")
    else:
        await update.message.reply_text("Comando não reconhecido.")

async def activate_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        key_to_activate = context.args[0]
    except (IndexError, TypeError):
        await update.message.reply_text("Uso: `/ativar <chave>`")
        return
    async with db_lock:
        if key_to_activate in db_data["keys"]:
            key_data = db_data["keys"][key_to_activate]
            if key_data["used_by"] is None:
                expiry_date = key_data["expiry_date"]
                db_data["users"][user_id]["vip_expiry"] = expiry_date
                db_data["keys"][key_to_activate]["used_by"] = user_id
                await update.message.reply_text(f"✅ VIP ativado! Válido até {expiry_date}.")
            else:
                await update.message.reply_text("❌ Chave já utilizada.")
        else:
            await update.message.reply_text("❌ Chave inválida.")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Acesso negado.")
        return
    admin_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Gerar Chave VIP", callback_data="admin_gen_key")],
        [InlineKeyboardButton("Listar Chaves", callback_data="admin_list_keys")],
        [InlineKeyboardButton("Deletar Chave", callback_data="admin_delete_key")]
    ])
    await update.message.reply_text("🔑 Painel de Administração", reply_markup=admin_keyboard)

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) != str(ADMIN_ID):
        await query.edit_message_text("⛔ Acesso negado.")
        return
    async with db_lock:
        if query.data == "admin_gen_key":
            key, expiry = generate_vip_key(days=30)
            db_data["keys"][key] = {"expiry_date": expiry, "used_by": None}
            await query.edit_message_text(f"🔑 Chave gerada: `{key}`\nValidade: {expiry}")
        elif query.data == "admin_list_keys":
            active_keys = [k for k, v in db_data["keys"].items() if v["used_by"] is None]
            if active_keys:
                msg = "🔑 **Chaves Ativas:**\n`" + "`\n`".join(active_keys) + "`"
            else:
                msg = "ℹ️ Nenhuma chave ativa."
            await query.edit_message_text(msg)
        elif query.data == "admin_delete_key":
            await query.edit_message_text("Envie a chave para deletar:")
            context.user_data['waiting_for_delete'] = True

async def main():
    if not TOKEN or not ADMIN_ID:
        logger.critical("ERRO: BOT_TOKEN e ADMIN_ID obrigatórios.")
        sys.exit(1)

    await load_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("ativar", activate_vip))

    app.add_handler(MessageHandler(filters.Regex("^📋 Jogos de Hoje$"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Múltipla$"), show_multiple))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Ligas$"), show_leagues))
    app.add_handler(MessageHandler(filters.Regex("^🤖 Fale com o Guru$"), guru_trigger))
    app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), show_status))

    app.add_handler(CallbackQueryHandler(admin_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    await app.initialize()
    await app.start()
    
    try:
        logger.info("Bot iniciado. Rodando serviços...")
        await asyncio.gather(
            app.updater.start_polling(allowed_updates=Update.ALL_TYPES),
            start_web_server(),
            run_pinger()
        )
    except KeyboardInterrupt:
        logger.info("Interrupção do usuário.")
    finally:
        logger.info("Desligando...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await save_db()
        logger.info("Desligamento completo.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot finalizado.")