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
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- AUTO-INSTALAÇÃO ---
try:
    import requests
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler
except ImportError:
    print("⚠️ Instalando dependências...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "flask", "matplotlib", "requests"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = os.getenv("ADMIN_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips_real.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BANCO DE DADOS =================
def load_db():
    default = {"users": {}, "keys": {}, "tips_history": [], "last_run": ""}
    if not os.path.exists(DB_FILE): return default
    try:
        with open(DB_FILE, "r") as f: return json.load(f)
    except: return default

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB (KEEP ALIVE VIA THREADS) =================
# Isso roda separado do bot para não travar
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"DVD TIPS REAL ON")
        def do_HEAD(self): # Importante para UptimeRobot
            self.send_response(200); self.end_headers()
    
    try:
        HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except Exception as e:
        print(f"Erro no Server Web: {e}")

# Função que pinga o próprio site a cada 10 min
def run_pinger():
    if not RENDER_URL: return
    while True:
        time.sleep(600)
        try:
            requests.get(RENDER_URL, timeout=10)
            print("Ping enviado para manter vivo.")
        except: pass

# Inicia as Threads de suporte (Web + Pinger)
threading.Thread(target=start_web_server, daemon=True).start()
threading.Thread(target=run_pinger, daemon=True).start()

# ================= INTEGRAÇÃO THE-ODDS-API (REAL) =================
def get_real_matches():
    """Busca jogos reais da API ou usa simulador se falhar"""
    if not ODDS_API_KEY:
        return generate_simulated_matches()
    
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
    
    try:
        response = requests.get(url)
        if response.status_code != 200:
            logger.error(f"Erro API: {response.text}")
            return generate_simulated_matches()
            
        data = response.json()
        matches = []
        
        now = datetime.utcnow()
        limit = now + timedelta(hours=24)
        
        for game in data:
            game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
            if game_time > limit: continue
            
            bookmakers = game.get('bookmakers', [])
            if not bookmakers: continue
            
            odds_data = bookmakers[0]['markets'][0]['outcomes']
            sorted_odds = sorted(odds_data, key=lambda x: x['price'])
            favorito = sorted_odds[0]
            
            if favorito['price'] < 1.30: continue
            
            matches.append({
                "match": f"{game['home_team']} x {game['away_team']}",
                "tip": f"Vence {favorito['name']}",
                "odd": favorito['price'],
                "league": game['sport_title']
            })
            
            if len(matches) >= 12: break
            
        if not matches: return generate_simulated_matches()
        return matches
        
    except Exception as e:
        logger.error(f"Erro Fatal API: {e}")
        return generate_simulated_matches()

def generate_simulated_matches():
    TEAMS = ["Flamengo", "Palmeiras", "Real Madrid", "City", "Bayern", "Arsenal", "Liverpool", "Barcelona"]
    matches = []
    random.shuffle(TEAMS)
    for i in range(0, len(TEAMS)-1, 2):
        t1, t2 = TEAMS[i], TEAMS[i+1]
        matches.append({
            "match": f"{t1} x {t2}",
            "tip": "Over 2.5 Gols",
            "odd": round(random.uniform(1.5, 2.1), 2),
            "league": "Simulado"
        })
    return matches

def generate_multiple_bet(matches_pool):
    if len(matches_pool) < 3: return None
    selection = random.sample(matches_pool, k=3)
    
    multi_odd = 1.0
    desc = []
    for m in selection:
        multi_odd *= m['odd']
        desc.append(f"• {m['match']} ({m['tip']}) @{m['odd']}")
    
    return {
        "match": "🔥 BILHETE PRONTO DO DIA 🔥",
        "tip": "\n".join(desc),
        "odd": round(multi_odd, 2)
    }

# ================= ENVIO AUTOMÁTICO =================
async def send_daily_batch(app):
    daily_selection = get_real_matches()
    multiple_bet = generate_multiple_bet(daily_selection)
    
    header = f"📅 **TIPS DE HOJE {datetime.now().strftime('%d/%m')}** 📅\n_Dados analisados via API_\n───────────────────"
    
    for uid in db["users"]:
        try:
            await app.bot.send_message(chat_id=uid, text=header, parse_mode="Markdown")
            await asyncio.sleep(2)
            
            for i, tip in enumerate(daily_selection):
                msg = f"🏆 **{tip.get('league', 'Futebol')}**\n⚽ {tip['match']}\n🎯 **{tip['tip']}**\n📈 Odd: {tip['odd']}"
                await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                await asyncio.sleep(1)
            
            if multiple_bet:
                msg_multi = f"🚀 **MÚLTIPLA DO DIA** 🚀\n\n{multiple_bet['tip']}\n\n💰 **ODD TOTAL: {multiple_bet['odd']}**"
                await app.bot.send_message(chat_id=uid, text=msg_multi, parse_mode="Markdown")
                
        except: pass

# Loop que verifica a hora (Roda dentro do Event Loop do Bot)
async def scheduler_loop(app):
    while True:
        try:
            now_br = datetime.utcnow() - timedelta(hours=3)
            # Verifica se é 08:00
            if now_br.strftime("%H:%M") == "08:00" and db["last_run"] != now_br.strftime("%Y-%m-%d"):
                logger.info("Enviando tips automáticas...")
                await send_daily_batch(app)
                db["last_run"] = now_br.strftime("%Y-%m-%d")
                save_db(db)
            await asyncio.sleep(50)
        except Exception as e:
            logger.error(f"Erro no Scheduler: {e}")
            await asyncio.sleep(60)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {}
    save_db(db)
    
    kb = []
    if str(uid) == str(ADMIN_ID):
        kb.append([InlineKeyboardButton("🚀 Gerar Tips Agora", callback_data="force_tips")])
    
    await update.message.reply_text("⚽ **DVD TIPS REAL V4.1**\nBot conectado e pronto.", reply_markup=InlineKeyboardMarkup(kb))

async def force_tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return
    await update.callback_query.message.reply_text("🔄 Gerando...")
    await send_daily_batch(context.application)
    await update.callback_query.message.reply_text("✅ Enviado!")

# ================= MAIN =================
if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    
    # Configuração correta para evitar conflito de loops
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(force_tips, pattern="^force_tips$"))
    
    # Inicia o agendador como uma tarefa background DO BOT
    # Isso evita o erro "NameError: keep_alive_async" e "no running event loop"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    print("🤖 DVD TIPS REAL V4.1 - INICIANDO...")
    
    # Injetamos o scheduler antes de iniciar o polling
    async def main_wrapper():
        async with app:
            await app.start()
            # Cria a tarefa do agendador
            asyncio.create_task(scheduler_loop(app))
            await app.updater.start_polling(drop_pending_updates=True)
            # Mantém rodando
            await asyncio.Event().wait()

    try:
        loop.run_until_complete(main_wrapper())
    except KeyboardInterrupt:
        pass