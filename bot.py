import os
import sys
import json
import logging
import uuid
import threading
import time
import random
import secrets
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- AUTO-INSTALAÇÃO ---
try:
    import requests # Usaremos requests para o ping (mais estável com threads)
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
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BANCO DE DADOS =================
def load_db():
    default = {
        "users": {}, "keys": {}, "tips": [], "active_bets": []
    }
    if not os.path.exists(DB_FILE): return default
    try:
        with open(DB_FILE, "r") as f: return json.load(f)
    except: return default

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB (ANTI-ERRO 501 E KEEP ALIVE) =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"DVD TIPS ON")
        def do_HEAD(self): # Corrige o erro do UptimeRobot
            self.send_response(200); self.end_headers()
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# Função de PING (Usando Threading para não travar o asyncio)
def run_pinger():
    if not RENDER_URL: return
    while True:
        time.sleep(600) # 10 minutos
        try:
            requests.get(RENDER_URL, timeout=10)
            print("Ping enviado com sucesso.")
        except:
            pass

# Inicia os serviços de fundo
threading.Thread(target=start_web_server, daemon=True).start()
threading.Thread(target=run_pinger, daemon=True).start()

# ================= LÓGICA VIP & UTILITÁRIOS =================
def is_vip(user_id):
    user = db["users"].get(str(user_id))
    if not user or not user.get("vip_expiry"): return False
    try:
        expiry = datetime.strptime(user["vip_expiry"], "%Y-%m-%d %H:%M:%S")
        return expiry > datetime.now()
    except: return False

def check_admin(user_id):
    # Se ADMIN_ID não estiver configurado, ninguém é admin
    if not ADMIN_ID: return False
    return str(user_id) == str(ADMIN_ID)

def generate_key(days=30):
    key = "DVD-" + secrets.token_hex(4).upper()
    db["keys"][key] = days
    save_db(db)
    return key

def ai_analysis_mock(match_name):
    analises = [
        "O mandante tem 70% de posse de bola nos últimos jogos.",
        "Must win game para o visitante.",
        "Defesas fracas, alta chance de Over 2.5 gols.",
        "O histórico do confronto favorece o empate."
    ]
    return random.choice(analises) + " (IA Confidence: 88%)"

# ================= GRÁFICOS =================
def generate_profit_chart(user_id):
    history = db["users"].get(str(user_id), {}).get("history", [])
    if not history: return None
    
    bankroll = [1000]
    dates = ["Início"]
    current = 1000
    
    # Pega os últimos 15 registros
    for bet in history[-15:]:
        if bet['result'] == 'green': current += bet['profit']
        elif bet['result'] == 'red': current -= bet['stake']
        bankroll.append(current)
        dates.append(bet['date'][:5])
        
    plt.figure(figsize=(6, 4))
    plt.plot(range(len(bankroll)), bankroll, marker='o', color='#00ff00', linewidth=2)
    plt.title('Crescimento da Banca 🚀')
    plt.grid(True, linestyle='--', alpha=0.3)
    plt.facecolor = '#f0f0f0'
    
    buf = os.sys.modules['io'].BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    return buf

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    
    if uid not in db["users"]:
        db["users"][uid] = {"vip_expiry": None, "bank": 1000, "history": []}
        save_db(db)
    
    status = "💎 VIP ATIVO" if is_vip(uid) else "👤 Membro Grátis"
    if check_admin(uid): status = "👑 ADMIN"
    
    kb = [
        [InlineKeyboardButton("📊 Minha Banca", callback_data="my_stats"),
         InlineKeyboardButton("🔑 Ativar VIP", callback_data="enter_key")],
        [InlineKeyboardButton("🆘 Suporte", url="https://t.me/seusuario")]
    ]
    
    if check_admin(uid):
        kb.append([InlineKeyboardButton("⚙️ Painel Admin", callback_data="admin_panel")])
    
    await update.message.reply_text(
        f"⚽ **DVD TIPS V2.0**\n\nOlá {user.first_name}!\nStatus: **{status}**\n\nUse o menu abaixo:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

# --- SISTEMA VIP ---
async def enter_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("Digite sua chave VIP (Ex: `DVD-XXXX`):")
    return 1

async def process_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    uid = str(update.effective_user.id)
    
    if key in db["keys"]:
        days = db["keys"].pop(key)
        new_expiry = datetime.now() + timedelta(days=days)
        db["users"][uid]["vip_expiry"] = new_expiry.strftime("%Y-%m-%d %H:%M:%S")
        save_db(db)
        await update.message.reply_text(f"✅ **VIP ATIVADO!**\nValidade: {days} dias.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Chave inválida.")
    return ConversationHandler.END

# --- ADMIN ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    kb = [
        [InlineKeyboardButton("⚽ Nova Tip", callback_data="create_tip")],
        [InlineKeyboardButton("🔑 Gerar Chave", callback_data="gen_key_menu")],
        [InlineKeyboardButton("🏁 Resultados", callback_data="result_menu")]
    ]
    await update.callback_query.edit_message_text("👑 **Painel Admin**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def gen_key_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = generate_key(30)
    await update.callback_query.message.reply_text(f"🔑 Chave (30 dias):\n`{key}`", parse_mode="Markdown")

async def create_tip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("Envie a TIP:\n`Jogo | Aposta | Odd`")
    return 2

async def broadcast_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw = update.message.text.split("|")
        match, bet, odd = raw[0].strip(), raw[1].strip(), raw[2].strip()
        analysis = ai_analysis_mock(match)
        
        tip_id = str(uuid.uuid4())[:6]
        tip_data = {"id": tip_id, "match": match, "bet": bet, "odd": odd, "status": "pending", "date": datetime.now().strftime("%d/%m")}
        db["tips"].append(tip_data)
        save_db(db)
        
        msg = f"💎 **TIP DO DVD** 💎\n\n⚽ **{match}**\n🎯 **{bet}**\n📈 Odd: {odd}\n\n🧠 _{analysis}_"
        
        for uid in db["users"]:
            try: await context.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
            except: pass
            
        await update.message.reply_text("✅ Tip enviada!")
    except:
        await update.message.reply_text("❌ Erro. Use: `Time A vs B | Aposta | 1.90`")
    return ConversationHandler.END

# --- RESULTADOS ---
async def result_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = []
    for tip in db["tips"][-5:]:
        if tip["status"] == "pending":
            kb.append([
                InlineKeyboardButton(f"✅ {tip['match']}", callback_data=f"set_green_{tip['id']}"),
                InlineKeyboardButton(f"❌", callback_data=f"set_red_{tip['id']}")
            ])
    if not kb: await update.callback_query.edit_message_text("Sem tips pendentes.")
    else: await update.callback_query.edit_message_text("Definir Resultado:", reply_markup=InlineKeyboardMarkup(kb))

async def set_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    action, tip_id = data.split("_")[1], data.split("_")[2]
    
    for tip in db["tips"]:
        if tip["id"] == tip_id:
            tip["status"] = action
            save_db(db)
            
            # Atualiza histórico dos usuários (Simulação de Lucro)
            profit = 100 if action == "green" else -100
            for uid in db["users"]:
                db["users"][uid]["history"].append({"date": tip["date"], "result": action, "profit": profit, "stake": 100})
            save_db(db)
            
            res_txt = "✅ GREEN! 💰" if action == "green" else "❌ RED."
            for uid in db["users"]:
                try: await context.bot.send_message(chat_id=uid, text=f"🏁 Resultado: {tip['match']}\n{res_txt}")
                except: pass
                
    await query.edit_message_text(f"Marcado como {action}!")

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.callback_query.from_user.id)
    chart = generate_profit_chart(uid)
    if chart: await update.callback_query.message.reply_photo(chart, caption="📈 Seu Gráfico")
    else: await update.callback_query.message.reply_text("Sem dados ainda.")

# ================= MAIN =================
if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERRO: Token não encontrado.")
        sys.exit()

    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    
    # Conversas
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_key_handler, pattern="^enter_key$")],
        states={1: [MessageHandler(filters.TEXT, process_key)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(create_tip_start, pattern="^create_tip$")],
        states={2: [MessageHandler(filters.TEXT, broadcast_tip)]},
        fallbacks=[]
    ))
    
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(gen_key_menu, pattern="^gen_key_menu$"))
    app.add_handler(CallbackQueryHandler(result_menu, pattern="^result_menu$"))
    app.add_handler(CallbackQueryHandler(set_result, pattern="^set_"))
    app.add_handler(CallbackQueryHandler(my_stats, pattern="^my_stats$"))
    
    print("🤖 DVD TIPS V2.0 RODANDO...")
    app.run_polling(drop_pending_updates=True)