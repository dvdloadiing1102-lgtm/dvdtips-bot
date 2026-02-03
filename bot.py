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

# Configuração IA Robusta
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logger.info("✅ Gemini API Configurada com sucesso!")
    except Exception as e:
        logger.error(f"❌ Erro ao configurar Gemini: {e}")
else:
    logger.warning("⚠️ GEMINI_API_KEY não encontrada! As funções de IA não funcionarão.")

# Estados
INPUT_ANALISE, INPUT_CALC, INPUT_GESTAO, INPUT_GURU, VIP_KEY = range(5)

# ================= BANCO DE DADOS =================
def load_db():
    default = {"users": {}, "keys": {}, "last_run": "", "api_cache": None, "api_cache_time": None}
    if not os.path.exists(DB_FILE): return default
    try: with open(DB_FILE, "r") as f: return json.load(f)
    except: return default

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"DVD TIPS V7.5 ON")
        def do_HEAD(self): self.send_response(200); self.end_headers()
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

# ================= INTEGRAÇÃO API + IA =================
def get_ai_analysis(match, tip, context="tip"):
    if not GEMINI_API_KEY: 
        return "⚠️ IA Indisponível (Verifique Logs)."
    
    try:
        # Tenta modelo Flash primeiro (mais rápido)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        if context == "tip":
            prompt = f"Jogo: {match}. Tip: {tip}. Justifique em 10 palavras técnicas. Sem aspas."
        elif context == "guru":
            prompt = f"Você é um tipster profissional. Responda curto: {match}"
        elif context == "analise":
            prompt = f"Analise o jogo {match} para apostas de hoje. Dê o vencedor provável e gols. Responda em português, máximo 3 linhas."
        
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro IA Primário: {e}")
        try:
            # Fallback para modelo Pro se o Flash falhar
            model = genai.GenerativeModel('gemini-pro')
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e2:
            logger.error(f"Erro IA Secundário: {e2}")
            return "Erro na conexão com IA."

def get_real_matches(force_refresh=False):
    if not ODDS_API_KEY: return generate_simulated_matches()
    
    if not force_refresh and db.get("api_cache") and db.get("api_cache_time"):
        last_time = datetime.strptime(db["api_cache_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last_time).total_seconds() < 2700: return db["api_cache"]
    
    # URL: Upcoming (Próximos Jogos) - Garante jogos de Terça
    url = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu,uk,us,au&markets=h2h,totals&oddsFormat=decimal"
    
    try:
        response = requests.get(url)
        if response.status_code != 200: 
            logger.error(f"Erro API Odds: {response.text}")
            return generate_simulated_matches()
        
        data = response.json()
        matches = []
        
        # Data Atual no Brasil (UTC-3)
        now_utc = datetime.now(timezone.utc)
        
        for game in data:
            if 'soccer' not in game['sport_key']: continue
            
            game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            
            # Pega jogos que começam entre AGORA e daqui a 24h
            if not (now_utc < game_time < now_utc + timedelta(hours=24)): continue
            
            # Converte para Horário de Brasília para exibição
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
                if 1.20 <= fav['price'] <= 2.50: 
                    tip, odd = f"Vence {fav['name']}", fav['price']
            
            if not tip and totals:
                for outcome in totals['outcomes']:
                    if outcome['name'] == 'Over' and outcome['point'] == 2.5:
                        if 1.50 <= outcome['price'] <= 2.20:
                            tip, odd = "Over 2.5 Gols", outcome['price']
            
            if tip:
                matches.append({
                    "match": f"{game['home_team']} x {game['away_team']}",
                    "tip": tip, "odd": odd, "league": game['sport_title'], "time": time_str
                })
            
            if len(matches) >= 15: break
        
        if matches:
            db["api_cache"] = matches
            db["api_cache_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_db(db)
            return matches
        
        return generate_simulated_matches()
    except Exception as e:
        logger.error(f"Erro Geral API: {e}")
        return generate_simulated_matches()

def generate_simulated_matches():
    TEAMS = ["Flamengo", "Palmeiras", "Milan", "Barcelona", "Arsenal"]
    matches = []
    for _ in range(5):
        t1, t2 = random.sample(TEAMS, 2)
        matches.append({"match": f"{t1} x {t2}", "tip": "Over 2.5 Gols", "odd": 1.80, "league": "Simulado (Erro API)", "time": "19:00"})
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
    await update.message.reply_text("⚽ **Qual jogo você quer analisar?**\n\nDigite o nome (ex: `Bologna x Milan`):", parse_mode="Markdown")
    return INPUT_ANALISE

async def handle_analise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match = update.message.text
    await update.message.reply_text("🧠 _DVD AI Analisando..._", parse_mode="Markdown")
    res = get_ai_analysis(match, "", "analise")
    await update.message.reply_text(f"🤖 **Análise:**\n\n{res}", parse_mode="Markdown")
    return ConversationHandler.END

# 2. Calculadora
async def start_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧮 **Calculadora**\nDigite: `Valor Odd` (ex: `50 1.80`)")
    return INPUT_CALC

async def handle_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.replace(",", ".")
        val, odd = map(float, text.split())
        lucro = val * (odd - 1)
        total = val * odd
        await update.message.reply_text(f"💰 **Retorno:** R$ {total:.2f}\n✅ **Lucro:** R$ {lucro:.2f}", parse_mode="Markdown")
    except:
        await update.message.reply_text("❌ Erro. Use: `100 2.0`")
        return INPUT_CALC
    return ConversationHandler.END

# 3. Gestão
async def start_gestao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 **Qual o valor da sua banca?**")
    return INPUT_GESTAO

async def handle_gestao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        banca = float(update.message.text.replace(",", "."))
        safe = banca * 0.02
        await update.message.reply_text(f"📊 **Gestão (2%):** R$ {safe:.2f} por aposta.", parse_mode="Markdown")
    except:
        await update.message.reply_text("❌ Apenas números.")
        return INPUT_GESTAO
    return ConversationHandler.END

# 4. Guru
async def start_guru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 **Pergunte ao Guru:**\n(Ex: O que é Handicap?)")
    return INPUT_GURU

async def handle_guru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quest = update.message.text
    res = get_ai_analysis(quest, "", "guru")
    await update.message.reply_text(f"🎓 **Guru:** {res}", parse_mode="Markdown")
    return ConversationHandler.END

# Funções Diretas
async def direct_zebra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache")
    if not tips: 
        await update.message.reply_text("🔄 Buscando jogos...")
        get_real_matches(force_refresh=True)
        tips = db.get("api_cache")
        
    if not tips: return await update.message.reply_text("📭 Sem dados da API.")
    
    zebras = [t for t in tips if t['odd'] > 2.0]
    if not zebras: zebras = tips
    zebra = max(zebras, key=lambda x: x['odd'])
    
    await update.message.reply_text(f"🦓 **ZEBRA DO DIA:**\n\n⚽ {zebra['match']}\n🎯 {zebra['tip']}\n📈 **Odd: {zebra['odd']}**", parse_mode="Markdown")

async def direct_segura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache")
    if not tips: 
        get_real_matches(force_refresh=True)
        tips = db.get("api_cache")

    if not tips: return await update.message.reply_text("📭 Sem dados.")
    
    segura = min(tips, key=lambda x: x['odd'])
    await update.message.reply_text(f"🛡️ **SEGURANÇA:**\n\n⚽ {segura['match']}\n🎯 {segura['tip']}\n📉 **Odd: {segura['odd']}**", parse_mode="Markdown")

async def direct_ligas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache") or []
    if not tips: return await update.message.reply_text("📭 Sem dados.")
    ligas = list(set([t['league'] for t in tips]))
    txt = "\n".join([f"🏆 {l}" for l in ligas[:15]])
    await update.message.reply_text(f"🌍 **Ligas Encontradas Hoje:**\n\n{txt}", parse_mode="Markdown")

async def direct_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache") or []
    if not tips: return await update.message.reply_text("📭 Sem dados.")
    txt = "\n".join([f"⏰ {t['time']} | {t['match']}" for t in tips[:12]])
    await update.message.reply_text(f"📋 **Agenda de Jogos (Próx 24h):**\n\n{txt}", parse_mode="Markdown")

async def direct_glossario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = "📚 **Glossário:**\n\n**Over 2.5:** +3 gols.\n**Under 2.5:** -3 gols.\n**DNB:** Empate devolve.\n**HT:** 1º Tempo."
    await update.message.reply_text(txt, parse_mode="Markdown")

async def direct_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    user = db["users"].get(uid, {})
    expiry = user.get("vip_expiry", "Free")
    await update.message.reply_text(f"🎫 **STATUS**\nID: `{uid}`\nPlano: **{expiry}**", parse_mode="Markdown")

# ================= SISTEMA =================
def check_admin(uid): return str(uid) == str(ADMIN_ID)
def generate_key(days): key = "KEY-" + secrets.token_hex(4).upper(); db["keys"][key] = days; save_db(db); return key

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {"vip_expiry": ""}
    save_db(db)
    
    await update.message.reply_text(
        "👋 **DVD TIPS APP V7.5**\nSelecione uma opção:",
        reply_markup=get_main_keyboard()
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    kb = [[InlineKeyboardButton("🚀 Enviar Tips", callback_data="force_tips")], [InlineKeyboardButton("🔑 Criar Chave", callback_data="gen_key")]]
    await update.message.reply_text("👑 **Admin**", reply_markup=InlineKeyboardMarkup(kb))

async def force_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("🚀 Buscando 'Upcoming'...")
    get_real_matches(force_refresh=True)
    tips = db.get("api_cache", [])
    
    if not tips:
        await update.callback_query.message.reply_text("❌ Nenhum jogo encontrado na API.")
        return

    # Cabeçalho com data correta
    br_time = datetime.now(timezone.utc) - timedelta(hours=3)
    header = f"📅 **TIPS {br_time.strftime('%d/%m')} (Terça)**"
    
    for uid in db["users"]:
        try:
            await context.bot.send_message(uid, header, parse_mode="Markdown")
            for t in tips[:6]:
                # Gera justificativa IA na hora do envio
                reason = get_ai_analysis(f"{t['match']}", t['tip'], "tip")
                await context.bot.send_message(uid, f"⚽ {t['match']}\n🎯 {t['tip']} (@{t['odd']})\n🧠 _{reason}_", parse_mode="Markdown")
        except: pass
    await update.callback_query.message.reply_text("✅ Feito!")

async def gen_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = generate_key(30)
    await update.callback_query.message.reply_text(f"🔑 `{key}`", parse_mode="Markdown")

async def start_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer()
    await update.message.reply_text("🔑 Digite a chave VIP:")
    return VIP_KEY

async def handle_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    uid = str(update.effective_user.id)
    if key in db["keys"]:
        days = db["keys"].pop(key)
        new_expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        db["users"][uid]["vip_expiry"] = new_expiry
        save_db(db)
        await update.message.reply_text(f"✅ VIP até {new_expiry}!")
    else: await update.message.reply_text("❌ Inválido.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelado.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers Conversa
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔮 Analisar Jogo$"), start_analise)],
        states={INPUT_ANALISE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analise)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
        
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧮 Calculadora$"), start_calc)],
        states={INPUT_CALC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_calc)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
        
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💰 Gestão Banca$"), start_gestao)],
        states={INPUT_GESTAO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gestao)]},
        fallbacks=[CommandHandler("cancel", cancel)]))
        
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🤖 Guru IA$"), start_guru)],
        states={INPUT_GURU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_guru)]},
        fallbacks=[CommandHandler("cancel", cancel)]))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("vip", start_vip), CallbackQueryHandler(start_vip, pattern="^enter_key$")],
        states={VIP_KEY: [MessageHandler(filters.TEXT, handle_vip)]},
        fallbacks=[]))

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
    print("🤖 DVD TIPS V7.5 - ONLINE")
    
    async def main_wrapper():
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()
    try: loop.run_until_complete(main_wrapper())
    except KeyboardInterrupt: pass