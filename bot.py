import os
import sys
import json
import logging
import uuid
import threading
import time
import random
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- AUTO-INSTALAÇÃO SEGURA ---
try:
    import httpx
    import matplotlib
    matplotlib.use('Agg')
    import google.generativeai as genai
    from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler
    from telegram.error import Conflict
except ImportError:
    print("⚠️ Instalando dependências...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "flask", "matplotlib", "httpx", "google-generativeai"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = os.getenv("ADMIN_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips_v14.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuração IA
USE_GEMINI = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
        logger.info("✅ Gemini API Ativa")
    except:
        logger.warning("⚠️ Erro na chave Gemini")

# Estados
INPUT_ANALISE, INPUT_CALC, INPUT_GESTAO, INPUT_GURU, VIP_KEY = range(5)

# ================= BANCO DE DADOS (SINTAXE CORRIGIDA) =================
def load_db():
    default = {"users": {}, "keys": {}, "last_run": "", "api_cache": None, "api_cache_time": None}
    if not os.path.exists(DB_FILE):
        return default
    
    try:
        # CORREÇÃO: Linhas separadas para evitar SyntaxError
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return default

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB (KEEP ALIVE) =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DVD TIPS V14 ONLINE")
        def do_HEAD(self): 
            self.send_response(200)
            self.end_headers()
    try: 
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except: 
        pass

def run_pinger():
    if not RENDER_URL: return
    while True:
        time.sleep(600)
        try:
            # Import local para não conflitar
            import requests
            requests.get(RENDER_URL, timeout=10)
        except: 
            pass

threading.Thread(target=start_web_server, daemon=True).start()
threading.Thread(target=run_pinger, daemon=True).start()

# ================= INTELIGÊNCIA ARTIFICIAL =================
BACKUP_PHRASES = [
    "Confronto equilibrado, leve vantagem para o mandante.",
    "Ataques fortes, grande chance de gols.",
    "Jogo truncado, defesas devem prevalecer.",
    "Favorito vive boa fase e deve vencer.",
    "Odd de valor, vale o risco calculado."
]

async def get_smart_analysis(match, tip, context="tip"):
    if USE_GEMINI:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            loop = asyncio.get_running_loop()
            
            prompt = ""
            if context == "tip": 
                prompt = f"Futebol/Basquete: {match}. Minha aposta: {tip}. Justifique em 1 frase curta técnica (PT-BR)."
            elif context == "guru": 
                prompt = f"Responda curto sobre apostas esportivas: {match}"
            elif context == "analise": 
                prompt = f"Analise o jogo {match}. Dê o vencedor provável e expectativa de gols/pontos. Responda em Português."
            
            # Executa sem travar o bot
            response = await loop.run_in_executor(None, model.generate_content, prompt)
            if response.text: return response.text.strip()
        except Exception as e:
            logger.error(f"Erro IA: {e}")
    
    return random.choice(BACKUP_PHRASES)

# ================= MOTOR DE ODDS (FILTRO DATA RIGOROSO) =================
TARGET_LEAGUES = [
    'soccer_epl', 'soccer_england_efl_cup', 
    'soccer_italy_serie_a', 'soccer_italy_coppa_italia',
    'soccer_germany_bundesliga', 'soccer_germany_dfb_pokal',
    'soccer_spain_la_liga', 'soccer_spain_copa_del_rey',
    'soccer_france_ligue_one', 'soccer_uefa_champions_league',
    'soccer_brazil_serie_a', 'soccer_brazil_serie_b', 'soccer_brazil_campeonato',
    'soccer_libertadores', 'soccer_sulamericana',
    'basketball_nba'
]

async def get_real_matches(force_refresh=False):
    if not ODDS_API_KEY: return generate_simulated_matches()
    
    # Cache de 30 min
    if not force_refresh and db.get("api_cache") and db.get("api_cache_time"):
        last = datetime.strptime(db["api_cache_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last).total_seconds() < 1800: return db["api_cache"]
    
    matches = []
    
    # DATAS: Define o intervalo exato do "Dia de Hoje no Brasil"
    now_utc = datetime.now(timezone.utc)
    now_br = now_utc - timedelta(hours=3)
    
    # Final do dia de hoje (23:59:59)
    end_of_day_br = now_br.replace(hour=23, minute=59, second=59)
    end_of_day_utc = end_of_day_br + timedelta(hours=3)
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # Pede jogos "Upcoming"
            url = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu,uk,us&markets=h2h,totals"
            resp = await client.get(url)
            
            if resp.status_code == 200:
                data = resp.json()
                for game in data:
                    # 1. Filtro de Ligas (Elite)
                    if game['sport_key'] not in TARGET_LEAGUES: continue
                    
                    # 2. Filtro de Data (RIGOROSO)
                    game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    
                    # Se o jogo já passou há mais de 2h ou é DEPOIS de hoje -> Pula
                    if game_time < (now_utc - timedelta(hours=2)) or game_time > end_of_day_utc:
                        continue
                    
                    res = process_game(game)
                    if res: matches.append(res)
        except Exception as e:
            logger.error(f"Erro API: {e}")

    # Remove duplicados e ordena
    unique = []
    seen = set()
    for m in matches:
        if m['match'] not in seen:
            unique.append(m)
            seen.add(m['match'])
    
    unique.sort(key=lambda x: x['time'])
    
    if unique:
        db["api_cache"] = unique
        db["api_cache_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_db(db)
        return unique
        
    return generate_simulated_matches()

def process_game(game):
    try:
        game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        time_str = (game_time - timedelta(hours=3)).strftime("%H:%M")
        
        league = game['sport_title'].replace("Soccer ", "").replace("Basketball ", "")
        is_nba = 'NBA' in league
        
        bookmakers = game.get('bookmakers', [])
        if not bookmakers: return None
        
        h2h = next((m for m in bookmakers[0]['markets'] if m['key'] == 'h2h'), None)
        totals = next((m for m in bookmakers[0]['markets'] if m['key'] == 'totals'), None)
        
        tip, odd = None, 0
        
        if h2h:
            outcomes = sorted(h2h['outcomes'], key=lambda x: x['price'])
            fav = outcomes[0]
            min_odd = 1.10 if is_nba else 1.25
            if min_odd <= fav['price'] <= 2.30:
                tip, odd = f"Vence {fav['name']}", fav['price']
        
        if (not tip or odd < 1.25) and totals:
            line = totals['outcomes'][0]
            if is_nba:
                over = next((o for o in totals['outcomes'] if o['name'] == 'Over'), None)
                if over and 1.50 <= over['price'] <= 2.10:
                    point = line.get('point', 0)
                    tip, odd = f"Over {point} Pontos", over['price']
            else:
                over25 = next((o for o in totals['outcomes'] if o['name'] == 'Over' and o['point'] == 2.5), None)
                if over25 and 1.50 <= over25['price'] <= 2.10:
                    tip, odd = "Over 2.5 Gols", over25['price']

        if tip:
            return {"match": f"{game['home_team']} x {game['away_team']}", "tip": tip, "odd": odd, "league": league, "time": time_str}
    except: return None
    return None

def generate_simulated_matches():
    return [
        {"match": "Man City x Arsenal", "tip": "Ambas Marcam", "odd": 1.75, "league": "Simulado", "time": "17:00"},
        {"match": "Real Madrid x Barça", "tip": "Over 2.5", "odd": 1.80, "league": "Simulado", "time": "20:00"}
    ]

# ================= MENUS =================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["🔮 Analisar Jogo", "🧮 Calculadora"],
        ["🦓 Zebra do Dia", "🛡️ Aposta Segura"],
        ["💰 Gestão Banca", "🤖 Guru IA"],
        ["🏆 Ligas", "📋 Jogos de Hoje"],
        ["📚 Glossário", "🎫 Meu Status"]
    ], resize_keyboard=True)

# ================= HANDLERS =================
async def start(u, c):
    uid=str(u.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {"vip_expiry": ""}
    save_db(db)
    # Limpa webhook para evitar conflitos antigos
    await c.bot.delete_webhook(drop_pending_updates=True)
    await u.message.reply_text("👋 **DVD TIPS V14.0**\nFiltro: Jogos de HOJE (Brasil & Elite)", reply_markup=get_main_keyboard())

# --- Listas ---
async def direct_jogos(u, c):
    await u.message.reply_text("🔄 Buscando jogos de HOJE...")
    tips = await get_real_matches(True)
    if not tips: return await u.message.reply_text("📭 Sem jogos principais hoje.")
    
    txt = ""
    for t in tips[:12]:
        txt += f"⏰ {t['time']} | {t['league']}\n⚔️ {t['match']}\n👉 **{t['tip']}** (@{t['odd']})\n\n"
    await u.message.reply_text(f"📋 **Agenda Elite Hoje:**\n\n{txt}", parse_mode="Markdown")

async def direct_ligas(u, c):
    tips = await get_real_matches(False)
    if tips:
        ls = sorted(list(set([t['league'] for t in tips])))
        await u.message.reply_text(f"🏆 **Ligas Hoje:**\n" + "\n".join([f"• {l}" for l in ls]))
    else: await u.message.reply_text("📭 Nada.")

# --- Funcionalidades ---
async def start_analise(u, c): await u.message.reply_text("⚽/🏀 **Qual jogo?**"); return INPUT_ANALISE
async def handle_analise(u, c):
    await u.message.reply_text("🧠 _Analisando..._")
    res = await get_smart_analysis(u.message.text, "", "analise")
    await u.message.reply_text(f"🤖 **Análise:**\n{res}", parse_mode="Markdown"); return ConversationHandler.END

async def start_calc(u, c): await u.message.reply_text("🧮 `Valor Odd`"); return INPUT_CALC
async def handle_calc(u, c): 
    try: v,o=map(float,u.message.text.replace(",", ".").split()); await u.message.reply_text(f"✅ Lucro: {v*(o-1):.2f}")
    except: await u.message.reply_text("❌ Erro")
    return ConversationHandler.END

async def start_gestao(u, c): await u.message.reply_text("💰 Banca?"); return INPUT_GESTAO
async def handle_gestao(u, c):
    try: b=float(u.message.text.replace(",", ".")); await u.message.reply_text(f"📊 Aposta (2%): {b*0.02:.2f}")
    except: await u.message.reply_text("❌ Erro")
    return ConversationHandler.END

async def start_guru(u, c): await u.message.reply_text("🤖 Pergunte:"); return INPUT_GURU
async def handle_guru(u, c): 
    res = await get_smart_analysis(u.message.text, "", "guru")
    await u.message.reply_text(f"🎓 {res}"); return ConversationHandler.END

async def direct_zebra(u, c):
    t = await get_real_matches(False)
    m = max(t, key=lambda x: x['odd']) if t else None
    if m: await u.message.reply_text(f"🦓 **ZEBRA:**\n{m['match']}\n🎯 {m['tip']} (@{m['odd']})")
    else: await u.message.reply_text("📭 Nada.")

async def direct_segura(u, c):
    t = await get_real_matches(False)
    m = min(t, key=lambda x: x['odd']) if t else None
    if m: await u.message.reply_text(f"🛡️ **SEGURA:**\n{m['match']}\n🎯 {m['tip']} (@{m['odd']})")
    else: await u.message.reply_text("📭 Nada.")

async def direct_glossario(u, c): await u.message.reply_text("📚 **Glossário:**\nOver: Mais\nUnder: Menos")
async def direct_status(u, c): await u.message.reply_text(f"🎫 ID: `{u.effective_user.id}`", parse_mode="Markdown")

# --- Admin & VIP ---
def check_admin(uid): return str(uid) == str(ADMIN_ID)
def generate_key(d): k="K-"+secrets.token_hex(4).upper(); db["keys"][k]=d; save_db(db); return k

async def admin_cmd(u, c):
    if not check_admin(u.effective_user.id): return
    kb = [[InlineKeyboardButton("🚀 Enviar", callback_data="force_tips")], [InlineKeyboardButton("🔑 Chave", callback_data="gen_key")]]
    await u.message.reply_text("👑 Admin", reply_markup=InlineKeyboardMarkup(kb))

async def force_tips(u, c):
    await u.callback_query.message.reply_text("🚀 Buscando...")
    tips = await get_real_matches(True)
    if not tips: await u.callback_query.message.reply_text("❌ Nada."); return
    for uid in db["users"]:
        try:
            await c.bot.send_message(uid, "📅 **TIPS DE HOJE:**")
            for t in tips[:6]:
                rs = await get_smart_analysis(t['match'], t['tip'], "tip")
                await c.bot.send_message(uid, f"🏆 {t['league']}\n⏰ {t['time']} | ⚔️ {t['match']}\n🎯 **{t['tip']}** (@{t['odd']})\n🧠 _{rs}_", parse_mode="Markdown")
        except: pass
    await u.callback_query.message.reply_text("✅ Feito.")

async def gen_key_h(u, c): await u.callback_query.message.reply_text(f"`{generate_key(30)}`", parse_mode="Markdown")

# CORREÇÃO DO VIP: Unificação para evitar duplo trigger
async def start_vip(u, c): 
    if u.callback_query: await u.callback_query.answer()
    await u.message.reply_text("🔑 Chave:")
    return VIP_KEY

async def handle_vip(u, c):
    k=u.message.text.strip(); uid=str(u.effective_user.id)
    if k in db["keys"]:
        db["users"][uid]["vip_expiry"] = "Ativo"; save_db(db); await u.message.reply_text("✅ VIP Ativo!")
    else: await u.message.reply_text("❌")
    return ConversationHandler.END

async def cancel(u, c): await u.message.reply_text("❌", reply_markup=get_main_keyboard()); return ConversationHandler.END

# --- Scheduler ---
async def scheduler(app):
    while True:
        now = datetime.now() - timedelta(hours=3)
        if now.strftime("%H:%M") == "08:00" and db["last_run"] != now.strftime("%Y-%m-%d"):
            tips = await get_real_matches(True)
            if tips:
                for uid in db["users"]:
                    try:
                        await app.bot.send_message(uid, "☀️ **Tips de Hoje:**")
                        for t in tips[:5]: await app.bot.send_message(uid, f"⚽ {t['match']}\n🎯 {t['tip']} (@{t['odd']})")
                    except: pass
                db["last_run"] = now.strftime("%Y-%m-%d"); save_db(db)
        await asyncio.sleep(60)

async def post_init(app):
    asyncio.create_task(scheduler(app))
    await app.bot.delete_webhook(drop_pending_updates=True)

if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    try:
        app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
        
        # Handlers
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🔮 Analisar Jogo$"), start_analise)], states={INPUT_ANALISE: [MessageHandler(filters.TEXT, handle_analise)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🧮 Calculadora$"), start_calc)], states={INPUT_CALC: [MessageHandler(filters.TEXT, handle_calc)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^💰 Gestão Banca$"), start_gestao)], states={INPUT_GESTAO: [MessageHandler(filters.TEXT, handle_gestao)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🤖 Guru IA$"), start_guru)], states={INPUT_GURU: [MessageHandler(filters.TEXT, handle_guru)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        
        # VIP Handler Corrigido
        app.add_handler(ConversationHandler(
            entry_points=[CommandHandler("vip", start_vip), CallbackQueryHandler(start_vip, pattern="^enter_key$")], 
            states={VIP_KEY: [MessageHandler(filters.TEXT, handle_vip)]}, 
            fallbacks=[CommandHandler("cancel", cancel)]
        ))

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("admin", admin_cmd))
        app.add_handler(MessageHandler(filters.Regex("^🦓 Zebra do Dia$"), direct_zebra))
        app.add_handler(MessageHandler(filters.Regex("^🛡️ Aposta Segura$"), direct_segura))
        app.add_handler(MessageHandler(filters.Regex("^🏆 Ligas$"), direct_ligas))
        app.add_handler(MessageHandler(filters.Regex("^📋 Jogos de Hoje$"), direct_jogos))
        app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), direct_status))
        app.add_handler(MessageHandler(filters.Regex("^📚 Glossário$"), direct_glossario))
        
        app.add_handler(CallbackQueryHandler(force_tips, pattern="^force_tips$"))
        app.add_handler(CallbackQueryHandler(gen_key_h, pattern="^gen_key$"))

        print("🤖 V14.0 ONLINE")
        app.run_polling()
    except Conflict:
        print("🚨 CONFLITO! Reiniciando...")
        time.sleep(5)
        os.execv(sys.executable, ['python'] + sys.argv)