import os
import sys
import json
import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

# --- AUTO-INSTALAÇÃO DE DEPENDÊNCIAS ---
try:
    import httpx
    import google.generativeai as genai
    from telegram import ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
    from telegram.constants import ParseMode
    from aiohttp import web
except ImportError:
    import subprocess
    print("⚠️ Dependências não encontradas. Instalando...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "httpx", "google-generativeai", "aiohttp"])
        print("✅ Dependências instaladas. Reiniciando...")
        os.execv(sys.executable, ['python'] + sys.argv)
    except Exception as e:
        print(f"❌ Falha crítica na instalação: {e}")
        sys.exit(1)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 10000))
DB_FILE = "dvd_tips_data.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuração IA
USE_GEMINI = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
        logger.info("✅ IA Gemini ativa.")
    except Exception as e:
        logger.warning(f"⚠️ Falha ao configurar IA: {e}")

# ================= BANCO DE DADOS =================
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
        logger.info("DB carregado com sucesso.")
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

# ================= SERVIDOR WEB (KEEP-ALIVE) =================
async def health_check(request):
    return web.Response(text=f"Bot Online! {datetime.now()}")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    try:
        await site.start()
        logger.info(f"Servidor web rodando na porta {PORT}")
    except Exception as e:
        logger.error(f"Falha no servidor web: {e}")

async def run_pinger():
    if not RENDER_URL:
        logger.info("Pinger desativado (RENDER_URL não definida).")
        return
    await asyncio.sleep(60)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(RENDER_URL, timeout=10)
            except httpx.RequestError:
                pass
            await asyncio.sleep(600) # Ping a cada 10 min

# ================= UTILITÁRIOS =================
last_action_time = {}

async def check_flood(update: Update, limit=2):
    user_id = str(update.effective_user.id)
    now = time.time()
    last = last_action_time.get(user_id, 0)
    if now - last < limit:
        await update.message.reply_text("⏳ Por favor, não envie comandos tão rápido.")
        return True
    last_action_time[user_id] = now
    return False

def generate_vip_key(days=30):
    key = "VIP-" + secrets.token_hex(4).upper()
    expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return key, expiry_date

# ================= MOTOR DE ODDS =================
CACHE_TTL = 1800 # 30 minutos

async def get_real_matches(force_refresh=False):
    # Verifica Cache
    cache = db_data.get("api_cache", {})
    if cache.get("timestamp") and not force_refresh:
        try:
            last_fetch = datetime.fromisoformat(cache["timestamp"])
            if (datetime.now() - last_fetch).total_seconds() < CACHE_TTL:
                return cache.get("matches", [])
        except:
            pass

    if not API_FOOTBALL_KEY:
        logger.error("Falta API_FOOTBALL_KEY")
        return []

    # Busca na API
    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
    url = f"https://v3.football.api-sports.io/fixtures?date={today}&status=NS"
    
    matches = []
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json().get("response", [])
            
            # IDs das Ligas Principais para filtrar
            VIP_LEAGUES = [39, 71, 72, 140, 61, 78, 135, 2, 3, 13, 11, 4, 9, 10, 34]
            
            for game_data in data:
                # Se a lista estiver grande, filtra só as VIPs. Se estiver vazia, pega tudo.
                if game_data["league"]["id"] not in VIP_LEAGUES and len(matches) > 20:
                    continue
                
                # Ajuste de Fuso Horário
                ts = game_data["fixture"]["timestamp"]
                game_time = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)
                
                matches.append({
                    "match": f'{game_data["teams"]["home"]["name"]} x {game_data["teams"]["away"]["name"]}',
                    "league": game_data["league"]["name"],
                    "time": game_time.strftime("%H:%M"),
                })
        except Exception as e:
            logger.error(f"Erro na API Football: {e}")
            return []

    if matches:
        matches.sort(key=lambda x: x["time"])
        async with db_lock:
            db_data["api_cache"] = {"matches": matches, "timestamp": datetime.now().isoformat()}
            # Salvar DB aqui não é estritamente necessário para cache, mas garante persistência
            
    return matches

def generate_multiple(matches, size=4):
    if not matches or len(matches) < size:
        return None
    selection = matches[:size]
    return {"games": selection}

async def ask_guru(text):
    if not USE_GEMINI:
        return "Guru IA indisponível (Chave não configurada)."
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"Você é um tipster profissional. Responda curto e direto (max 2 linhas) sobre: {text}"
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro Guru: {e}")
        return "O Guru está dormindo agora. Tente depois."

# ================= COMANDOS E HANDLERS =================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📋 Jogos de Hoje", "🚀 Múltipla"],
        ["🤖 Fale com o Guru", "🎫 Meu Status"],
        ["/admin"] # Botão para facilitar acesso admin
    ], resize_keyboard=True)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if await check_flood(update): return
    
    async with db_lock:
        if user_id not in db_data["users"]:
            db_data["users"][user_id] = {"vip_expiry": None}
            await save_db() # Salva novo usuário
            
    await update.message.reply_text("👋 **DVD TIPS V19 PRO**\nSeja bem-vindo!", reply_markup=get_main_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def show_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update): return
    await update.message.reply_text("🔄 Buscando grade...")
    
    matches = await get_real_matches()
    if not matches:
        await update.message.reply_text("📭 Nenhum jogo encontrado para hoje.")
        return
        
    # Formata a lista
    msg = "*📋 JOGOS DE HOJE:*\n\n"
    for m in matches[:15]:
        msg += f"⏰ {m['time']} | {m['league']}\n⚽ {m['match']}\n\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def show_multiple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update): return
    
    matches = await get_real_matches()
    multi = generate_multiple(matches)
    
    if multi and multi["games"]:
        msg = "*🚀 MÚLTIPLA SUGERIDA*\n\n"
        for g in multi['games']:
            msg += f"• {g['match']}\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("⚠️ Jogos insuficientes para montar múltipla.")

async def show_leagues(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Função auxiliar, caso queira adicionar botão depois
    matches = await get_real_matches()
    if matches:
        leagues = sorted(list(set([m['league'] for m in matches])))
        msg = "*🏆 Ligas Ativas:*\n" + "\n".join([f"• {l}" for l in leagues])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def guru_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update): return
    await update.message.reply_text("🤖 **Guru IA:**\nQual sua dúvida sobre apostas?", parse_mode=ParseMode.MARKDOWN)
    context.user_data['waiting_for_guru'] = True

async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update): return
    user_id = str(update.effective_user.id)
    user_data = db_data["users"].get(user_id, {})
    vip_expiry = user_data.get("vip_expiry", "N/A")
    
    msg = f"*🎫 SEU STATUS*\n\n*ID:* `{user_id}`\n"
    
    if vip_expiry:
        try:
            if datetime.strptime(vip_expiry, "%Y-%m-%d") > datetime.now():
                msg += f"*VIP:* ✅ Ativo até {vip_expiry}"
            else:
                msg += "*VIP:* ❌ Expirado"
        except:
            msg += "*VIP:* ❌ Inativo"
    else:
        msg += "*VIP:* ❌ Inativo"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    # Lógica do Guru
    if context.user_data.get('waiting_for_guru'):
        context.user_data['waiting_for_guru'] = False
        await update.message.reply_text("🤔 Pensando...")
        answer = await ask_guru(text)
        await update.message.reply_text(f"🎓 *Guru Responde:*\n{answer}", parse_mode=ParseMode.MARKDOWN)
        return

    # Lógica de Deletar Chave (Admin) - CORRIGIDA
    if context.user_data.get('waiting_for_delete'):
        context.user_data['waiting_for_delete'] = False
        key_to_delete = text.strip()
        
        async with db_lock:
            if key_to_delete in db_data["keys"]:
                del db_data["keys"][key_to_delete]
                await save_db() # Salva imediatamente
                await update.message.reply_text(f"✅ Chave `{key_to_delete}` deletada.", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ Chave não encontrada.")
        return

    await update.message.reply_text("❓ Comando não reconhecido. Use o menu.")

async def activate_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        key_to_activate = context.args[0]
    except (IndexError, TypeError):
        await update.message.reply_text("⚠️ Uso correto: `/ativar SUA-CHAVE-AQUI`", parse_mode=ParseMode.MARKDOWN)
        return
        
    async with db_lock:
        if key_to_activate in db_data["keys"]:
            key_data = db_data["keys"][key_to_activate]
            if key_data["used_by"] is None:
                expiry_date = key_data["expiry_date"]
                
                # Atualiza usuário e chave
                if user_id not in db_data["users"]:
                    db_data["users"][user_id] = {}
                db_data["users"][user_id]["vip_expiry"] = expiry_date
                db_data["keys"][key_to_activate]["used_by"] = user_id
                
                await save_db() # Salva imediatamente
                await update.message.reply_text(f"✅ **VIP ATIVADO!**\nVálido até: {expiry_date}", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ Esta chave já foi usada.")
        else:
            await update.message.reply_text("❌ Chave inválida.")

# --- ADMINISTRAÇÃO ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Acesso negado.")
        return
        
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Gerar Chave", callback_data="admin_gen_key")],
        [InlineKeyboardButton("📜 Listar Chaves", callback_data="admin_list_keys")],
        [InlineKeyboardButton("🗑️ Deletar Chave", callback_data="admin_delete_key")]
    ])
    await update.message.reply_text("🔑 **Painel Admin**", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if str(query.from_user.id) != str(ADMIN_ID):
        await query.edit_message_text("⛔ Acesso negado.")
        return
        
    if query.data == "admin_gen_key":
        key, expiry = generate_vip_key(days=30)
        async with db_lock:
            db_data["keys"][key] = {"expiry_date": expiry, "used_by": None}
            await save_db() # Salva a chave nova
        await query.edit_message_text(f"🔑 **Nova Chave:**\n`{key}`\n\nValidade: {expiry}", parse_mode=ParseMode.MARKDOWN)
        
    elif query.data == "admin_list_keys":
        active_keys = [k for k, v in db_data["keys"].items() if v["used_by"] is None]
        if active_keys:
            msg = "🔑 **Chaves Disponíveis:**\n\n`" + "`\n`".join(active_keys) + "`"
        else:
            msg = "ℹ️ Nenhuma chave disponível."
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        
    elif query.data == "admin_delete_key":
        await query.edit_message_text("🗑️ Envie a chave que deseja deletar no chat:")
        context.user_data['waiting_for_delete'] = True

# ================= EXECUÇÃO =================
async def main():
    if not TOKEN or not ADMIN_ID:
        logger.critical("ERRO: Variáveis de ambiente (TOKEN, ADMIN_ID) não configuradas.")
        sys.exit(1)

    await load_db()
    
    app = Application.builder().token(TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("ativar", activate_vip))

    # Botões de Texto
    app.add_handler(MessageHandler(filters.Regex("^📋 Jogos de Hoje$"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Múltipla$"), show_multiple))
    app.add_handler(MessageHandler(filters.Regex("^🤖 Fale com o Guru$"), guru_trigger))
    app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), show_status))

    # Callbacks e Texto Geral
    app.add_handler(CallbackQueryHandler(admin_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Inicialização
    await app.initialize()
    await app.start()
    
    try:
        logger.info("Bot iniciado. Serviços web e pinger ativos.")
        await asyncio.gather(
            app.updater.start_polling(allowed_updates=Update.ALL_TYPES),
            start_web_server(),
            run_pinger()
        )
    except KeyboardInterrupt:
        logger.info("Parando...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await save_db()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass