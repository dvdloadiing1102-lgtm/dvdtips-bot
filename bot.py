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
    from telegram.error import Conflict, NetworkError
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
    await asyncio.sleep(120)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.get(RENDER_URL, timeout=10)
            except httpx.RequestError:
                pass
            await asyncio.sleep(600)

# ================= UTILITÁRIOS =================
last_action_time = {}

async def check_flood(update: Update, limit=1.5):
    user_id = str(update.effective_user.id)
    now = time.time()
    last = last_action_time.get(user_id, 0)
    if now - last < limit:
        return True
    last_action_time[user_id] = now
    return False

def generate_vip_key(days=30):
    key = "VIP-" + secrets.token_hex(4).upper()
    expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return key, expiry_date

# ================= MOTOR DE ODDS (FUTEBOL + NBA) =================
CACHE_TTL = 900 # 15 minutos

async def get_real_matches(force_refresh=False):
    cache = db_data.get("api_cache", {})
    if cache.get("timestamp") and not force_refresh:
        try:
            last_fetch = datetime.fromisoformat(cache["timestamp"])
            if (datetime.now() - last_fetch).total_seconds() < CACHE_TTL:
                return cache.get("matches", [])
        except: pass

    if not API_FOOTBALL_KEY:
        logger.error("Falta API_FOOTBALL_KEY")
        return []

    # Ajuste de data
    today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    
    headers = {
        "x-rapidapi-host": "v3.football.api-sports.io", 
        "x-rapidapi-key": API_FOOTBALL_KEY
    }
    
    headers_nba = {
        "x-rapidapi-host": "v1.basketball.api-sports.io", 
        "x-rapidapi-key": API_FOOTBALL_KEY
    }

    url_football = f"https://v3.football.api-sports.io/fixtures?date={today}"
    url_basketball = f"https://v1.basketball.api-sports.io/games?date={today}"
    
    matches = []
    
    async with httpx.AsyncClient(timeout=30) as client:
        # Requisições em paralelo (Futebol + Basquete)
        try:
            responses = await asyncio.gather(
                client.get(url_football, headers=headers),
                client.get(url_basketball, headers=headers_nba),
                return_exceptions=True
            )
            
            resp_foot = responses[0]
            resp_bask = responses[1]
            
            # --- PROCESSAR FUTEBOL ---
            if not isinstance(resp_foot, Exception) and resp_foot.status_code == 200:
                data_f = resp_foot.json().get("response", [])
                
                # Lista de IDs FUTEBOL (Copas, Estaduais, Elite)
                VIP_LEAGUES_F = [
                    39, 40, 41, 42, 45, 48,       # Inglaterra
                    140, 141, 143,                # Espanha
                    78, 79, 529,                  # Alemanha
                    135, 136, 137,                # Itália
                    61, 62, 66,                   # França
                    71, 72, 73,                   # Brasil Nacional
                    475, 476, 477, 478, 479, 480, 484, # Brasil Estaduais
                    2, 3, 13, 11, 848, 15,        # Continental
                    94, 96, 88, 203, 128          # Outros
                ]
                
                now_br = datetime.now(timezone.utc) - timedelta(hours=3)
                
                for game in data_f:
                    if game["league"]["id"] not in VIP_LEAGUES_F: continue
                    
                    ts = game["fixture"]["timestamp"]
                    game_time = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)
                    
                    if game_time < (now_br - timedelta(hours=3)): continue # Filtra jogos muito antigos

                    home = game["teams"]["home"]["name"]
                    away = game["teams"]["away"]["name"]
                    
                    # Simulação Odd Futebol
                    odd_val = round(random.uniform(1.5, 2.4), 2)
                    tip_val = f"Vence {home}" if random.random() > 0.45 else "Over 2.5 Gols"
                    
                    matches.append({
                        "sport": "⚽",
                        "match": f'{home} x {away}',
                        "league": game["league"]["name"],
                        "time": game_time.strftime("%H:%M"),
                        "odd": odd_val,
                        "tip": tip_val,
                        "ts": ts
                    })

            # --- PROCESSAR BASQUETE (NBA) ---
            if not isinstance(resp_bask, Exception) and resp_bask.status_code == 200:
                data_b = resp_bask.json().get("response", [])
                
                # ID 12 = NBA
                VIP_LEAGUES_B = [12] 
                
                for game in data_b:
                    if game["league"]["id"] not in VIP_LEAGUES_B: continue
                    
                    ts = game["timestamp"]
                    game_time = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)
                    
                    # NBA roda de madrugada, vamos mostrar jogos das próximas 24h
                    # (Não filtramos por "passado" tão rigidamente pq a API retorna o dia da data)
                    
                    home = game["teams"]["home"]["name"]
                    away = game["teams"]["away"]["name"]
                    
                    # Simulação Odd Basquete
                    odd_val = round(random.uniform(1.4, 2.2), 2)
                    tip_val = f"Vence {home}" if random.random() > 0.5 else "Over 215.5 Pts"
                    
                    matches.append({
                        "sport": "🏀",
                        "match": f'{home} x {away}',
                        "league": "NBA",
                        "time": game_time.strftime("%H:%M"),
                        "odd": odd_val,
                        "tip": tip_val,
                        "ts": ts
                    })

        except Exception as e:
            logger.error(f"Erro geral API: {e}")
            return []

    if matches:
        # Ordena por horário (Timestamp)
        matches.sort(key=lambda x: x["ts"])
        async with db_lock:
            db_data["api_cache"] = {"matches": matches, "timestamp": datetime.now().isoformat()}
            
    return matches

def generate_multiple(matches, size=4):
    if not matches or len(matches) < size: return None
    # Prioriza jogos que ainda não começaram ou são NBA
    selection = random.sample(matches, min(len(matches), size))
    total_odd = 1.0
    for m in selection: total_odd *= m['odd']
    return {"games": selection, "total_odd": round(total_odd, 2)}

async def ask_guru(text):
    if not USE_GEMINI: return "Guru IA indisponível."
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"Você é um tipster profissional. Responda curto e direto (max 2 linhas) sobre: {text}"
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except: return "Erro ao consultar o Guru."

# ================= COMANDOS E HANDLERS =================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📋 Jogos de Hoje", "🚀 Múltipla"],
        ["🤖 Fale com o Guru", "🎫 Meu Status"],
        ["/admin"]
    ], resize_keyboard=True)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if await check_flood(update): return
    async with db_lock:
        if user_id not in db_data["users"]:
            db_data["users"][user_id] = {"vip_expiry": None}
            await save_db()
    await update.message.reply_text("👋 **DVD TIPS V21.0**\nFutebol & NBA Ativados!", reply_markup=get_main_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def show_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update): return
    await update.message.reply_text("🔄 Buscando Futebol e NBA...")
    matches = await get_real_matches()
    
    if not matches:
        await update.message.reply_text("📭 Nenhum jogo das ligas selecionadas (Futebol/NBA) encontrado para hoje.")
        return
        
    msg = "*📋 GRADE DE HOJE:*\n\n"
    for m in matches[:25]: # Aumentei limite para caber NBA
        icon = m['sport']
        msg += f"{icon} {m['time']} | {m['league']}\n⚔️ {m['match']}\n👉 *{m['tip']}* (@{m['odd']})\n\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def show_multiple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_flood(update): return
    matches = await get_real_matches()
    multi = generate_multiple(matches)
    if multi and multi["games"]:
        msg = "*🚀 MÚLTIPLA SUGERIDA*\n\n"
        for g in multi['games']: 
            icon = g['sport']
            msg += f"• {icon} {g['match']} ({g['tip']})\n"
        msg += f"\n💰 *ODD TOTAL: {multi['total_odd']}*"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text("⚠️ Jogos insuficientes para montar múltipla.")

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
            if datetime.strptime(vip_expiry, "%Y-%m-%d") > datetime.now(): msg += f"*VIP:* ✅ Ativo até {vip_expiry}"
            else: msg += "*VIP:* ❌ Expirado"
        except: msg += "*VIP:* ❌ Inativo"
    else: msg += "*VIP:* ❌ Inativo"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if context.user_data.get('waiting_for_guru'):
        context.user_data['waiting_for_guru'] = False
        await update.message.reply_text("🤔 Pensando...")
        answer = await ask_guru(text)
        await update.message.reply_text(f"🎓 *Guru Responde:*\n{answer}", parse_mode=ParseMode.MARKDOWN)
        return
    if context.user_data.get('waiting_for_delete'):
        context.user_data['waiting_for_delete'] = False
        key_to_delete = text.strip()
        async with db_lock:
            if key_to_delete in db_data["keys"]:
                del db_data["keys"][key_to_delete]
                await save_db()
                await update.message.reply_text(f"✅ Chave `{key_to_delete}` deletada.", parse_mode=ParseMode.MARKDOWN)
            else: await update.message.reply_text("❌ Chave não encontrada.")
        return
    await update.message.reply_text("❓ Comando não reconhecido. Use o menu.")

async def activate_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try: key_to_activate = context.args[0]
    except (IndexError, TypeError):
        await update.message.reply_text("⚠️ Uso correto: `/ativar SUA-CHAVE-AQUI`", parse_mode=ParseMode.MARKDOWN)
        return
    async with db_lock:
        if key_to_activate in db_data["keys"]:
            key_data = db_data["keys"][key_to_activate]
            if key_data["used_by"] is None:
                expiry_date = key_data["expiry_date"]
                if user_id not in db_data["users"]: db_data["users"][user_id] = {}
                db_data["users"][user_id]["vip_expiry"] = expiry_date
                db_data["keys"][key_to_activate]["used_by"] = user_id
                await save_db()
                await update.message.reply_text(f"✅ **VIP ATIVADO!**\nVálido até: {expiry_date}", parse_mode=ParseMode.MARKDOWN)
            else: await update.message.reply_text("❌ Esta chave já foi usada.")
        else: await update.message.reply_text("❌ Chave inválida.")

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
            await save_db()
        await query.edit_message_text(f"🔑 **Nova Chave:**\n`{key}`\n\nValidade: {expiry}", parse_mode=ParseMode.MARKDOWN)
    elif query.data == "admin_list_keys":
        active_keys = [k for k, v in db_data["keys"].items() if v["used_by"] is None]
        msg = "🔑 **Chaves Ativas:**\n\n`" + "`\n`".join(active_keys) + "`" if active_keys else "ℹ️ Nenhuma chave ativa."
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
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("ativar", activate_vip))
    app.add_handler(MessageHandler(filters.Regex("^📋 Jogos de Hoje$"), show_games))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Múltipla$"), show_multiple))
    app.add_handler(MessageHandler(filters.Regex("^🤖 Fale com o Guru$"), guru_trigger))
    app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), show_status))
    app.add_handler(CallbackQueryHandler(admin_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    await app.initialize()
    await app.start()
    
    try:
        logger.info("Bot iniciado. Tentando conectar...")
        while True:
            try:
                await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
                break
            except Conflict:
                logger.warning("⚠️ Conflito de Bot! Esperando 10s...")
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Erro fatal: {e}")
                raise e

        logger.info("✅ Bot Conectado!")
        await asyncio.gather(start_web_server(), run_pinger())
        while True: await asyncio.sleep(3600)
            
    except KeyboardInterrupt:
        logger.info("Interrupção do usuário.")
    finally:
        try: await app.updater.stop()
        except: pass
        await app.stop()
        await app.shutdown()
        await save_db()
        logger.info("Desligado.")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass