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
DB_FILE = "dvd_tips_v7.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

if GEMINI_API_KEY: genai.configure(api_key=GEMINI_API_KEY)

# Estados para conversas interativas
INPUT_ANALISE, INPUT_CALC, INPUT_GESTAO, INPUT_GURU, VIP_KEY = range(5)

# ================= BANCO DE DADOS (CORRIGIDO) =================
def load_db():
    default = {
        "users": {}, 
        "keys": {}, 
        "last_run": "", 
        "api_cache": None, 
        "api_cache_time": None
    }
    
    if not os.path.exists(DB_FILE):
        return default
        
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return default

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DVD TIPS APP ON")
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
            requests.get(RENDER_URL, timeout=10)
        except: 
            pass

threading.Thread(target=start_web_server, daemon=True).start()
threading.Thread(target=run_pinger, daemon=True).start()

# ================= INTEGRAÇÃO API + IA =================
def get_ai_analysis(match, tip, context="tip"):
    if not GEMINI_API_KEY: return "Análise indisponível (Falta Chave IA)."
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        if context == "tip":
            prompt = f"Jogo: {match}. Tip: {tip}. Justifique em 10 palavras. Sem aspas."
        elif context == "guru":
            prompt = f"Você é um especialista em apostas. Responda curto e direto: {match}"
        elif context == "analise":
            prompt = f"Analise o jogo {match} para apostas. Dê o vencedor provável e expectativa de gols. Máximo 3 linhas."
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except: return "IA Indisponível."

def get_real_matches(force_refresh=False):
    if not ODDS_API_KEY: return generate_simulated_matches()
    
    if not force_refresh and db.get("api_cache") and db.get("api_cache_time"):
        last_time = datetime.strptime(db["api_cache_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last_time).total_seconds() < 3600: return db["api_cache"]
    
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h,totals&oddsFormat=decimal"
    try:
        response = requests.get(url)
        if response.status_code != 200: return generate_simulated_matches()
        data = response.json()
        matches = []
        now = datetime.now(timezone.utc)
        
        for game in data:
            game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if game_time > now + timedelta(hours=24): continue
            
            time_str = (game_time - timedelta(hours=3)).strftime("%H:%M")
            bookmakers = game.get('bookmakers', [])
            if not bookmakers: continue
            
            markets = bookmakers[0]['markets']
            h2h = next((m for m in markets if m['key'] == 'h2h'), None)
            totals = next((m for m in markets if m['key'] == 'totals'), None)
            
            tip, odd = None, 0
            if h2h:
                odds = sorted(h2h['outcomes'], key=lambda x: x['price'])
                fav = odds[0]
                if 1.25 <= fav['price'] <= 2.30: tip, odd = f"Vence {fav['name']}", fav['price']
            
            if not tip and totals:
                for outcome in totals['outcomes']:
                    if outcome['name'] == 'Over' and outcome['point'] == 2.5 and 1.50 <= outcome['price'] <= 2.10:
                        tip, odd = "Over 2.5 Gols", outcome['price']
            
            if tip:
                matches.append({
                    "match": f"{game['home_team']} x {game['away_team']}",
                    "tip": tip, "odd": odd, "league": game['sport_title'], "time": time_str,
                    "reason": get_ai_analysis(f"{game['home_team']} x {game['away_team']}", tip)
                })
            if len(matches) >= 15: break
        
        if matches:
            db["api_cache"] = matches
            db["api_cache_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_db(db)
            return matches
        return generate_simulated_matches()
    except: return generate_simulated_matches()

def generate_simulated_matches():
    TEAMS = ["Flamengo", "Palmeiras", "Real Madrid", "City", "Arsenal"]
    matches = []
    for _ in range(5):
        t1, t2 = random.sample(TEAMS, 2)
        tip = "Over 2.5 Gols"
        matches.append({"match": f"{t1} x {t2}", "tip": tip, "odd": 1.80, "league": "Simulado", "time": "19:00", "reason": "Jogaço ofensivo."})
    return matches

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

# ================= FUNÇÕES INTERATIVAS =================

# 1. Analisar
async def start_analise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ **Qual jogo você quer analisar?**\n\nDigite o nome dos times (ex: `Flamengo x Vasco`):", parse_mode="Markdown")
    return INPUT_ANALISE

async def handle_analise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = update.message.text
    await update.message.reply_text("🧠 _Consultando IA..._", parse_mode="Markdown")
    res = get_ai_analysis(match, "", "analise")
    await update.message.reply_text(f"🤖 **Análise DVD AI:**\n\n{res}", parse_mode="Markdown")
    return ConversationHandler.END

# 2. Calculadora
async def start_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧮 **Calculadora de Lucro**\n\nDigite o valor da aposta e a odd separados por espaço.\nExemplo: `50 1.80`")
    return INPUT_CALC

async def handle_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.replace(",", ".")
        val, odd = map(float, text.split())
        lucro = val * (odd - 1)
        total = val * odd
        await update.message.reply_text(f"💰 **Resultado:**\n\nAposta: R$ {val:.2f}\nRetorno: R$ {total:.2f}\n✅ **Lucro Líquido:** R$ {lucro:.2f}", parse_mode="Markdown")
    except:
        await update.message.reply_text("❌ Formato inválido. Tente de novo (ex: `100 2.0`).")
        return INPUT_CALC
    return ConversationHandler.END

# 3. Gestão
async def start_gestao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 **Gestão de Banca**\n\nQual o valor total da sua banca hoje? (Apenas números)")
    return INPUT_GESTAO

async def handle_gestao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        banca = float(update.message.text.replace(",", "."))
        safe = banca * 0.02
        agg = banca * 0.05
        await update.message.reply_text(f"📊 **Gestão Recomendada:**\n\n🛡️ Conservador (2%): **R$ {safe:.2f}**\n🔥 Agressivo (5%): **R$ {agg:.2f}**", parse_mode="Markdown")
    except:
        await update.message.reply_text("❌ Digite apenas números.")
        return INPUT_GESTAO
    return ConversationHandler.END

# 4. Guru
async def start_guru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 **Guru das Apostas**\n\nQual sua dúvida sobre apostas? Pergunte qualquer coisa!")
    return INPUT_GURU

async def handle_guru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quest = update.message.text
    res = get_ai_analysis(quest, "", "guru")
    await update.message.reply_text(f"🎓 **Guru Responde:**\n\n{res}", parse_mode="Markdown")
    return ConversationHandler.END

# Funções Diretas
async def direct_zebra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache")
    if not tips: 
        get_real_matches(force_refresh=True)
        tips = db.get("api_cache")
        
    if not tips: return await update.message.reply_text("📭 Sem jogos analisados no momento.")
    
    zebra = max(tips, key=lambda x: x['odd'])
    await update.message.reply_text(f"🦓 **ZEBRA DO DIA:**\n\n⚽ {zebra['match']}\n🎯 {zebra['tip']}\n📈 **Odd: {zebra['odd']}**", parse_mode="Markdown")

async def direct_segura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache")
    if not tips: 
        get_real_matches(force_refresh=True)
        tips = db.get("api_cache")

    if not tips: return await update.message.reply_text("📭 Sem jogos analisados.")
    
    segura = min(tips, key=lambda x: x['odd'])
    await update.message.reply_text(f"🛡️ **APOSTA SEGURA:**\n\n⚽ {segura['match']}\n🎯 {segura['tip']}\n📉 **Odd: {segura['odd']}**", parse_mode="Markdown")

async def direct_ligas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache") or []
    if not tips: return await update.message.reply_text("📭 Sem dados.")
    ligas = list(set([t['league'] for t in tips]))
    txt = "\n".join([f"• {l}" for l in ligas])
    await update.message.reply_text(f"🏆 **Ligas Hoje:**\n\n{txt}", parse_mode="Markdown")

async def direct_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache") or []
    if not tips: return await update.message.reply_text("📭 Sem dados.")
    txt = "\n".join([f"• {t['time']} | {t['match']}" for t in tips[:12]])
    await update.message.reply_text(f"📋 **Lista de Jogos:**\n\n{txt}", parse_mode="Markdown")

async def direct_glossario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = "📚 **Glossário:**\n\n**Over 2.5:** 3 gols ou mais.\n**Under 2.5:** Menos de 3 gols.\n**BTTS:** Ambas Marcam.\n**1x2:** Casa, Empate ou Fora.\n**DNB:** Empate Anula."
    await update.message.reply_text(txt, parse_mode="Markdown")

async def direct_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user = db["users"].get(uid, {})
    expiry = user.get("vip_expiry", "Free")
    bank = user.get("bank", 1000)
    await update.message.reply_text(f"🎫 **PERFIL VIP**\n\n👤 ID: `{uid}`\n📅 Plano: **{expiry}**\n💰 Banca Virtual: R$ {bank}", parse_mode="Markdown")

# ================= ADMIN & SISTEMA =================
def is_vip(uid): return db["users"].get(str(uid), {}).get("vip_expiry", "") > datetime.now().strftime("%Y-%m-%d")
def check_admin(uid): return str(uid) == str(ADMIN_ID)
def generate_key(days): key = "KEY-" + secrets.token_hex(4).upper(); db["keys"][key] = days; save_db(db); return key

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {"vip_expiry": "", "bank": 1000}
    save_db(db)
    
    await update.message.reply_text(
        "👋 **Bem-vindo ao DVD TIPS APP!**\n\nUse o menu abaixo para navegar.",
        reply_markup=get_main_keyboard()
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    kb = [[InlineKeyboardButton("🚀 Enviar Tips", callback_data="force_tips")], [InlineKeyboardButton("🔑 Gerar Chave", callback_data="gen_key")]]
    await update.message.reply_text("👑 **Painel Admin**", reply_markup=InlineKeyboardMarkup(kb))

async def force_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("🚀 Enviando...")
    get_real_matches(force_refresh=True)
    tips = db.get("api_cache", [])
    header = f"📅 **TIPS {datetime.now().strftime('%d/%m')}**"
    for uid in db["users"]:
        try:
            await context.bot.send_message(uid, header, parse_mode="Markdown")
            for t in tips[:6]:
                await context.bot.send_message(uid, f"⚽ {t['match']}\n🎯 {t['tip']} (@{t['odd']})\n🧠 _{t['reason']}_", parse_mode="Markdown")
        except: pass
    await update.callback_query.message.reply_text("✅ Feito!")

async def gen_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = generate_key(30)
    await update.callback_query.message.reply_text(f"🔑 Chave: `{key}`", parse_mode="Markdown")

async def start_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔑 Digite sua chave VIP:")
    return VIP_KEY

async def handle_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    uid = str(update.effective_user.id)
    if key in db["keys"]:
        days = db["keys"].pop(key)
        new_expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        db["users"][uid]["vip_expiry"] = new_expiry
        save_db(db)
        await update.message.reply_text("✅ **VIP ATIVADO!**", parse_mode="Markdown")
    else: await update.message.reply_text("❌ Chave inválida.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelado.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# ================= MAIN =================
if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔮 Analisar Jogo$"), start_analise)],
        states={INPUT_ANALISE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analise)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧮 Calculadora$"), start_calc)],
        states={INPUT_CALC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_calc)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💰 Gestão Banca$"), start_gestao)],
        states={INPUT_GESTAO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gestao)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🤖 Guru IA$"), start_guru)],
        states={INPUT_GURU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_guru)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("vip", start_vip), InlineKeyboardButton("🔑 Ativar VIP", callback_data="enter_key")],
        states={VIP_KEY: [MessageHandler(filters.TEXT, handle_vip)]},
        fallbacks=[]
    ))
    # Handler para o botão "Ativar VIP" quando fosse callback (removido do menu principal, mas mantido pra compatibilidade)
    app.add_handler(CallbackQueryHandler(start_vip, pattern="^enter_key$"))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    
    # Handlers Botões
    app.add_handler(MessageHandler(filters.Regex("^🦓 Zebra do Dia$"), direct_zebra))
    app.add_handler(MessageHandler(filters.Regex("^🛡️ Aposta Segura$"), direct_segura))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Ligas$"), direct_ligas))
    app.add_handler(MessageHandler(filters.Regex("^📋 Jogos Hoje$"), direct_jogos))
    app.add_handler(MessageHandler(filters.Regex("^📚 Glossário$"), direct_glossario))
    app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), direct_status))

    app.add_handler(CallbackQueryHandler(force_tips, pattern="^force_tips$"))
    app.add_handler(CallbackQueryHandler(gen_key_handler, pattern="^gen_key$"))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print("🤖 DVD TIPS V7.2 - ONLINE")
    
    async def main_wrapper():
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()
    try: loop.run_until_complete(main_wrapper())
    except KeyboardInterrupt: pass