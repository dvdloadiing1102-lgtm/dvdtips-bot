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
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
DB_FILE = "dvd_tips_ai.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ================= BANCO DE DADOS (CORRIGIDO) =================
def load_db():
    default = {
        "users": {}, 
        "keys": {}, 
        "tips_history": [], 
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
            self.wfile.write(b"DVD TIPS AI V6.1 ON")
            
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

# ================= INTELIGÊNCIA ARTIFICIAL =================
def get_ai_analysis(match, tip):
    if not GEMINI_API_KEY:
        return "Análise baseada em estatísticas recentes."
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Atue como um analista de apostas profissional.
        Jogo: {match}
        Minha Tip: {tip}
        Escreva UMA frase curta (max 15 palavras) justificando a tip. 
        Seja técnico e empolgante. Sem aspas.
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro Gemini: {e}")
        return "Tendência forte baseada no histórico."

async def ask_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = " ".join(context.args)
    if not user_msg:
        await update.message.reply_text("Use: `/analisar Flamengo x Vasco`", parse_mode="Markdown")
        return

    await update.message.reply_text("🧠 _Analisando estatísticas..._", parse_mode="Markdown")
    
    if not GEMINI_API_KEY:
        await update.message.reply_text("IA não configurada.")
        return

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"Analise o jogo {user_msg} para apostas. Dê vencedor e gols. Seja breve."
        response = model.generate_content(prompt)
        await update.message.reply_text(f"🤖 **DVD AI Diz:**\n\n{response.text}", parse_mode="Markdown")
    except:
        await update.message.reply_text("Erro ao conectar na IA.")

# ================= DADOS E API =================
def get_real_matches(force_refresh=False):
    if not ODDS_API_KEY: return generate_simulated_matches()
    
    if not force_refresh and db.get("api_cache") and db.get("api_cache_time"):
        last_time = datetime.strptime(db["api_cache_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last_time).total_seconds() < 3600:
            return db["api_cache"]
    
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
                if 1.30 <= fav['price'] <= 2.20:
                    tip, odd = f"Vence {fav['name']}", fav['price']
            
            if not tip and totals:
                for outcome in totals['outcomes']:
                    if outcome['name'] == 'Over' and outcome['point'] == 2.5 and 1.50 <= outcome['price'] <= 2.00:
                        tip, odd = "Mais de 2.5 Gols", outcome['price']
            
            if tip:
                ai_reason = get_ai_analysis(f"{game['home_team']} x {game['away_team']}", tip)
                matches.append({
                    "match": f"{game['home_team']} x {game['away_team']}",
                    "tip": tip, 
                    "odd": odd, 
                    "league": game['sport_title'], 
                    "time": time_str,
                    "reason": ai_reason
                })
            
            if len(matches) >= 10: break
        
        if matches:
            db["api_cache"] = matches
            db["api_cache_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_db(db)
            return matches
            
        return generate_simulated_matches()
    except Exception as e:
        logger.error(f"Erro API: {e}")
        return generate_simulated_matches()

def generate_simulated_matches():
    TEAMS = ["Flamengo", "Palmeiras", "Real Madrid", "City", "Arsenal"]
    matches = []
    for _ in range(5):
        t1, t2 = random.sample(TEAMS, 2)
        tip = "Over 2.5 Gols"
        matches.append({
            "match": f"{t1} x {t2}", 
            "tip": tip, 
            "odd": 1.80, 
            "league": "Simulado", 
            "time": "19:00",
            "reason": get_ai_analysis(f"{t1} x {t2}", tip)
        })
    return matches

def generate_multiple_bet(matches_pool):
    if len(matches_pool) < 3: return None
    selection = random.sample(matches_pool, k=3)
    multi_odd = 1.0
    desc = []
    for m in selection:
        multi_odd *= m['odd']
        desc.append(f"• {m['time']} | {m['match']}\n   👉 {m['tip']} (@{m['odd']})")
    return {
        "match": "🔥 BILHETE PRONTO DO DIA 🔥", 
        "tip": "\n".join(desc), 
        "odd": round(multi_odd, 2)
    }

# ================= ENVIO =================
async def send_daily_batch(app):
    selection = get_real_matches()
    multiple = generate_multiple_bet(selection)
    header = f"📅 **TIPS DE HOJE {datetime.now().strftime('%d/%m')}**\n_Powered by Gemini AI_\n───────────────────"
    
    for uid in db["users"]:
        try:
            await app.bot.send_message(chat_id=uid, text=header, parse_mode="Markdown")
            await asyncio.sleep(1)
            for tip in selection:
                msg = (f"🏆 **{tip.get('league', 'Futebol')}** • ⏰ {tip['time']}\n"
                       f"⚽ {tip['match']}\n"
                       f"🎯 **{tip['tip']}**\n"
                       f"🧠 _{tip['reason']}_\n"
                       f"📈 Odd: {tip['odd']}")
                await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                await asyncio.sleep(1.5)
            if multiple:
                msg_multi = f"🚀 **MÚLTIPLA DO DIA** 🚀\n\n{multiple['tip']}\n\n💰 **ODD TOTAL: {multiple['odd']}**"
                await app.bot.send_message(chat_id=uid, text=msg_multi, parse_mode="Markdown")
        except: pass

async def scheduler_loop(app):
    while True:
        try:
            now = datetime.utcnow() - timedelta(hours=3)
            if now.strftime("%H:%M") == "08:00" and db["last_run"] != now.strftime("%Y-%m-%d"):
                await send_daily_batch(app)
                db["last_run"] = now.strftime("%Y-%m-%d")
                save_db(db)
            await asyncio.sleep(50)
        except: await asyncio.sleep(60)

# ================= HANDLERS =================
def is_vip(uid): return db["users"].get(str(uid), {}).get("vip_expiry", "") > datetime.now().strftime("%Y-%m-%d")
def check_admin(uid): return str(uid) == str(ADMIN_ID)
def generate_key(days): key = "KEY-" + secrets.token_hex(4).upper(); db["keys"][key] = days; save_db(db); return key

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {"vip_expiry": "", "bank": 1000}
    save_db(db)
    
    kb = [[InlineKeyboardButton("🔑 VIP", callback_data="enter_key")]]
    if check_admin(uid): kb.append([InlineKeyboardButton("⚙️ Admin", callback_data="admin_panel")])
    
    await update.message.reply_text(
        f"🤖 **DVD TIPS AI V6.1**\nUse `/analisar [jogo]` para pedir dicas à IA!", 
        reply_markup=InlineKeyboardMarkup(kb), 
        parse_mode="Markdown"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    kb = [
        [InlineKeyboardButton("🚀 Enviar Tips (AI)", callback_data="force_tips")], 
        [InlineKeyboardButton("🔑 Gerar Chave", callback_data="gen_key")]
    ]
    await update.callback_query.edit_message_text("👑 **Painel Admin**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def force_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    await update.callback_query.message.reply_text("🧠 IA analisando jogos... Aguarde.")
    await send_daily_batch(context.application)
    await update.callback_query.message.reply_text("✅ Enviado!")

async def gen_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    key = generate_key(30)
    await update.callback_query.message.reply_text(f"🔑 Chave: `{key}`", parse_mode="Markdown")

async def enter_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("Digite a chave:")
    return 1

async def process_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    uid = str(update.effective_user.id)
    if key in db["keys"]:
        days = db["keys"].pop(key)
        new_expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        db["users"][uid]["vip_expiry"] = new_expiry
        save_db(db)
        await update.message.reply_text("✅ VIP Ativado!")
    else:
        await update.message.reply_text("❌ Inválido.")
    return ConversationHandler.END

# ================= MAIN =================
if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analisar", ask_ai_command))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(force_tips, pattern="^force_tips$"))
    app.add_handler(CallbackQueryHandler(gen_key_handler, pattern="^gen_key$"))
    
    conv_vip = ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_key, pattern="^enter_key$")],
        states={1: [MessageHandler(filters.TEXT, process_key)]},
        fallbacks=[]
    )
    app.add_handler(conv_vip)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print("🤖 DVD TIPS AI V6.1 - ONLINE")
    
    async def main_wrapper():
        async with app:
            await app.start()
            asyncio.create_task(scheduler_loop(app))
            await app.updater.start_polling(drop_pending_updates=True)
            await asyncio.Event().wait()
            
    try:
        loop.run_until_complete(main_wrapper())
    except KeyboardInterrupt:
        pass