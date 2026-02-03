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

# --- AUTO-INSTALAÇÃO ---
try:
    import requests
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import google.generativeai as genai
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler
    from telegram.error import Conflict
except ImportError:
    print("⚠️ Instalando dependências...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "flask", "matplotlib", "requests", "google-generativeai"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = os.getenv("ADMIN_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips_v81.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuração IA
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logger.info("✅ IA Conectada!")
    except: logger.warning("⚠️ Erro IA")

# Estados
INPUT_ANALISE, INPUT_CALC, INPUT_GESTAO, INPUT_GURU, VIP_KEY = range(5)

# ================= BANCO DE DADOS =================
def load_db():
    default = {"users": {}, "keys": {}, "last_run": "", "api_cache": None, "api_cache_time": None}
    if not os.path.exists(DB_FILE): return default
    try:
        with open(DB_FILE, "r") as f: return json.load(f)
    except: return default

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DVD TIPS V8.1 ON")
        def do_HEAD(self): 
            self.send_response(200)
            self.end_headers()
    try: HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except: pass

def run_pinger():
    if not RENDER_URL: return
    while True:
        time.sleep(600)
        try: requests.get(RENDER_URL, timeout=10)
        except: pass

threading.Thread(target=start_web_server, daemon=True).start()
threading.Thread(target=run_pinger, daemon=True).start()

# ================= INTEGRAÇÃO API (FUTEBOL + NBA) =================
def get_ai_analysis(match, tip, context="tip"):
    if not GEMINI_API_KEY: return "Boa sorte!"
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        if context == "tip":
            prompt = f"Jogo: {match}. Aposta: {tip}. Justifique em 1 frase técnica curta. PT-BR."
        elif context == "guru":
            prompt = f"Você é tipster. Responda curto: {match}"
        elif context == "analise":
            prompt = f"Analise {match} para apostas hoje. Vencedor e Pontos/Gols. Responda PT-BR."
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except: return "Análise indisponível."

def get_real_matches(force_refresh=False):
    if not ODDS_API_KEY: return generate_simulated_matches()
    
    # Cache 30 min
    if not force_refresh and db.get("api_cache") and db.get("api_cache_time"):
        last = datetime.strptime(db["api_cache_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last).total_seconds() < 1800: return db["api_cache"]
    
    matches = []
    
    # LISTA DE LIGAS (Agora com NBA!)
    leagues = [
        'soccer_brazil_serie_a', 
        'soccer_brazil_campeonato',
        'basketball_nba', # <--- NBA ADICIONADA
        'soccer_uefa_champions_league',
        'soccer_england_league1'
    ]
    
    # 1. Busca Ligas Específicas
    for league in leagues:
        try:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?apiKey={ODDS_API_KEY}&regions=eu,us&markets=h2h,totals"
            resp = requests.get(url)
            if resp.status_code == 200:
                data = resp.json()
                for game in data:
                    matches.append(process_game_data(game))
        except: pass

    # 2. Busca "Próximos" Genérico (Fallback)
    if len(matches) < 5:
        try:
            url_up = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu,us&markets=h2h,totals"
            resp = requests.get(url_up)
            if resp.status_code == 200:
                for game in resp.json():
                    # Aceita Futebol e Basquete
                    if 'soccer' in game['sport_key'] or 'basketball' in game['sport_key']:
                        matches.append(process_game_data(game))
        except: pass

    # Limpeza e Ordenação
    matches = [m for m in matches if m is not None]
    # Remove duplicados (por nome do jogo)
    seen = set()
    unique_matches = []
    for m in matches:
        if m['match'] not in seen:
            unique_matches.append(m)
            seen.add(m['match'])
            
    unique_matches.sort(key=lambda x: x['time'])
    
    if unique_matches:
        db["api_cache"] = unique_matches
        db["api_cache_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_db(db)
        return unique_matches
    
    return generate_simulated_matches()

def process_game_data(game):
    try:
        now_utc = datetime.now(timezone.utc)
        game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        
        # Janela de 30h (pra pegar NBA da madrugada)
        if not (now_utc - timedelta(hours=2) < game_time < now_utc + timedelta(hours=30)): return None
        
        time_str = (game_time - timedelta(hours=3)).strftime("%H:%M")
        sport = game['sport_key']
        is_nba = 'basketball' in sport
        
        bookmakers = game.get('bookmakers', [])
        if not bookmakers: return None
        
        # Procura mercados
        h2h = None
        totals = None
        
        for m in bookmakers[0]['markets']:
            if m['key'] == 'h2h': h2h = m
            if m['key'] == 'totals': totals = m
            
        tip, odd = None, 0
        
        # --- ESTRATÉGIA ---
        
        # 1. Tenta Vencedor (Moneyline)
        if h2h:
            outcomes = sorted(h2h['outcomes'], key=lambda x: x['price'])
            fav = outcomes[0]
            # NBA aceita odds menores (1.10+), Futebol (1.25+)
            min_odd = 1.10 if is_nba else 1.25
            if min_odd <= fav['price'] <= 2.50:
                tip = f"Vence {fav['name']}"
                odd = fav['price']
        
        # 2. Tenta Over/Under (Se vencedor estiver ruim ou muito baixo)
        if (not tip or odd < 1.20) and totals:
            # Pega a linha principal (primeira que aparecer)
            line = totals['outcomes'][0]
            point = line.get('point', 0)
            
            if is_nba:
                # NBA: Over XXX
                # Verifica se a odd do Over é boa (1.50 - 2.20)
                over_outcome = next((o for o in totals['outcomes'] if o['name'] == 'Over'), None)
                if over_outcome and 1.50 <= over_outcome['price'] <= 2.20:
                    tip = f"Over {point} Pontos"
                    odd = over_outcome['price']
            else:
                # FUTEBOL: Over 2.5
                over_25 = next((o for o in totals['outcomes'] if o['name'] == 'Over' and o['point'] == 2.5), None)
                if over_25 and 1.50 <= over_25['price'] <= 2.20:
                    tip = "Over 2.5 Gols"
                    odd = over_25['price']

        if tip:
            return {
                "match": f"{game['home_team']} x {game['away_team']}",
                "tip": tip,
                "odd": odd,
                "league": "🏀 NBA" if is_nba else "⚽ Futebol",
                "time": time_str
            }
    except: return None
    return None

def generate_simulated_matches():
    return [
        {"match": "Lakers x Warriors", "tip": "Over 225.5 Pontos", "odd": 1.90, "league": "🏀 Simulado", "time": "23:00"},
        {"match": "Flamengo x Vasco", "tip": "Vence Flamengo", "odd": 1.85, "league": "⚽ Simulado", "time": "21:00"}
    ]

# ================= MENUS =================
def get_main_keyboard():
    keyboard = [
        ["🔮 Analisar Jogo", "🧮 Calculadora"],
        ["🦓 Zebra do Dia", "🛡️ Aposta Segura"],
        ["💰 Gestão Banca", "🤖 Guru IA"],
        ["🏆 Ligas", "📋 Jogos Hoje"],
        ["📚 Glossário", "🎫 Meu Status"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# ================= FUNÇÕES DO BOT =================
# (Mantive todas as funções da V8.0 aqui, só ajustei o start para limpar webhook)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {"vip_expiry": ""}
    save_db(db)
    await context.bot.delete_webhook(drop_pending_updates=True) # Limpa conflitos
    await update.message.reply_text("👋 **DVD TIPS V8.1**\n(Agora com NBA! 🏀)", reply_markup=get_main_keyboard())

# --- RESTO DAS FUNÇÕES IGUAIS (Calculadora, Gestão, IA, etc) ---
async def start_analise(u, c): await u.message.reply_text("⚽/🏀 **Qual jogo?**"); return INPUT_ANALISE
async def handle_analise(u, c): 
    res = get_ai_analysis(u.message.text, "", "analise")
    await u.message.reply_text(f"🤖 {res}", parse_mode="Markdown"); return ConversationHandler.END

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
async def handle_guru(u, c): await u.message.reply_text(get_ai_analysis(u.message.text, "", "guru")); return ConversationHandler.END

async def direct_zebra(u, c):
    t = db.get("api_cache") or get_real_matches(True)
    m = max(t, key=lambda x: x['odd']) if t else None
    if m: await u.message.reply_text(f"🦓 **ZEBRA:**\n{m['league']}\n⚔️ {m['match']}\n🎯 {m['tip']} (@{m['odd']})")
    else: await u.message.reply_text("📭 Nada.")

async def direct_segura(u, c):
    t = db.get("api_cache") or get_real_matches(True)
    m = min(t, key=lambda x: x['odd']) if t else None
    if m: await u.message.reply_text(f"🛡️ **SEGURA:**\n{m['league']}\n⚔️ {m['match']}\n🎯 {m['tip']} (@{m['odd']})")
    else: await u.message.reply_text("📭 Nada.")

async def direct_ligas(u, c):
    t = db.get("api_cache") or get_real_matches(True)
    if t:
        ls = list(set([x['league'] for x in t]))
        await u.message.reply_text("🏆 **Ligas:**\n"+"\n".join([f"• {l}" for l in ls[:15]]))
    else: await u.message.reply_text("📭 Nada.")

async def direct_jogos(u, c):
    t = db.get("api_cache") or get_real_matches(True)
    if t:
        txt = "\n".join([f"{x['time']} | {x['match']}" for x in t[:10]])
        await u.message.reply_text(f"📋 **Jogos:**\n{txt}")
    else: await u.message.reply_text("📭 Nada.")

async def direct_glossario(u, c): await u.message.reply_text("📚 **Glossário:**\nOver: Mais\nUnder: Menos\nMoneyline: Vencedor")
async def direct_status(u, c): await u.message.reply_text(f"🎫 ID: `{u.effective_user.id}`")

def check_admin(uid): return str(uid) == str(ADMIN_ID)
def generate_key(d): k="K-"+secrets.token_hex(4).upper(); db["keys"][k]=d; save_db(db); return k

async def admin_cmd(u, c):
    if not check_admin(u.effective_user.id): return
    kb = [[InlineKeyboardButton("🚀 Enviar", callback_data="force_tips")], [InlineKeyboardButton("🔑 Chave", callback_data="gen_key")]]
    await u.message.reply_text("👑 Admin", reply_markup=InlineKeyboardMarkup(kb))

async def force_tips(u, c):
    await u.callback_query.message.reply_text("🚀 Buscando...")
    tips = get_real_matches(True)
    if not tips: await u.callback_query.message.reply_text("❌ Nada encontrado."); return
    
    for uid in db["users"]:
        try:
            await c.bot.send_message(uid, "📅 **TIPS DE HOJE:**")
            for t in tips[:6]:
                rs = get_ai_analysis(t['match'], t['tip'])
                await c.bot.send_message(uid, f"{t['league']} • {t['time']}\n⚔️ {t['match']}\n🎯 {t['tip']} (@{t['odd']})\n🧠 _{rs}_", parse_mode="Markdown")
        except: pass
    await u.callback_query.message.reply_text("✅ Feito.")

async def gen_key_h(u, c): await u.callback_query.message.reply_text(f"`{generate_key(30)}`", parse_mode="Markdown")
async def start_vip(u, c): 
    if u.callback_query: await u.callback_query.answer()
    await u.message.reply_text("🔑 Chave:"); return VIP_KEY
async def handle_vip(u, c):
    k=u.message.text.strip(); uid=str(u.effective_user.id)
    if k in db["keys"]:
        db["users"][uid]["vip_expiry"] = "Ativo"
        save_db(db)
        await u.message.reply_text("✅ VIP Ativo!")
    else: await u.message.reply_text("❌")
    return ConversationHandler.END
async def cancel(u, c): await u.message.reply_text("❌", reply_markup=get_main_keyboard()); return ConversationHandler.END

if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    try:
        app = ApplicationBuilder().token(TOKEN).build()
        
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🔮 Analisar Jogo$"), start_analise)], states={INPUT_ANALISE: [MessageHandler(filters.TEXT, handle_analise)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🧮 Calculadora$"), start_calc)], states={INPUT_CALC: [MessageHandler(filters.TEXT, handle_calc)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^💰 Gestão Banca$"), start_gestao)], states={INPUT_GESTAO: [MessageHandler(filters.TEXT, handle_gestao)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🤖 Guru IA$"), start_guru)], states={INPUT_GURU: [MessageHandler(filters.TEXT, handle_guru)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[CommandHandler("vip", start_vip), CallbackQueryHandler(start_vip, pattern="^enter_key$")], states={VIP_KEY: [MessageHandler(filters.TEXT, handle_vip)]}, fallbacks=[]))

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("admin", admin_cmd))
        app.add_handler(MessageHandler(filters.Regex("^🦓 Zebra do Dia$"), direct_zebra))
        app.add_handler(MessageHandler(filters.Regex("^🛡️ Aposta Segura$"), direct_segura))
        app.add_handler(MessageHandler(filters.Regex("^🏆 Ligas$"), direct_ligas))
        app.add_handler(MessageHandler(filters.Regex("^📋 Jogos Hoje$"), direct_jogos))
        app.add_handler(MessageHandler(filters.Regex("^📚 Glossário$"), direct_glossario))
        app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), direct_status))
        app.add_handler(CallbackQueryHandler(force_tips, pattern="^force_tips$"))
        app.add_handler(CallbackQueryHandler(gen_key_h, pattern="^gen_key$"))

        print("🤖 V8.1 ONLINE")
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        print("🚨 CONFLITO! Reiniciando...")
        time.sleep(5)
        os.execv(sys.executable, ['python'] + sys.argv)