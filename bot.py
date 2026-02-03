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

# --- AUTO-INSTALAÇÃO DE DEPENDÊNCIAS ---
try:
    import httpx
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler
except ImportError:
    print("⚠️ Instalando dependências...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "flask", "matplotlib", "requests", "httpx"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN") # Coloque seu token no Render
ADMIN_ID = os.getenv("ADMIN_ID") # Coloque SEU ID aqui para ser o dono
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BANCO DE DADOS (JSON) =================
def load_db():
    default = {
        "users": {},       # {id: {vip_expiry: str, bank: 1000, history: []}}
        "keys": {},        # {chave: dias}
        "tips": [],        # Histórico de tips enviadas
        "active_bets": []  # Apostas aguardando resultado
    }
    if not os.path.exists(DB_FILE): return default
    try:
        with open(DB_FILE, "r") as f: return json.load(f)
    except: return default

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB (KEEP ALIVE) =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"DVD TIPS ON")
        def do_HEAD(self):
            self.send_response(200); self.end_headers()
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=start_web_server, daemon=True).start()

async def keep_alive_async():
    if not RENDER_URL: return
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await asyncio.sleep(600)
                await client.get(RENDER_URL, timeout=10)
            except: pass

# ================= LÓGICA VIP & UTILITÁRIOS =================
def is_vip(user_id):
    user = db["users"].get(str(user_id))
    if not user or not user.get("vip_expiry"): return False
    expiry = datetime.strptime(user["vip_expiry"], "%Y-%m-%d %H:%M:%S")
    return expiry > datetime.now()

def check_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

def generate_key(days=30):
    key = "DVD-" + secrets.token_hex(4).upper()
    db["keys"][key] = days
    save_db(db)
    return key

# ================= SIMULAÇÃO DE IA E MARKET =================
def ai_analysis_mock(match_name):
    # Aqui entraria a chamada para o Gemini API real
    analises = [
        "O time da casa vem pressionado e deve atacar desde o início.",
        "Historicamente, este confronto tem muitos gols.",
        "O visitante está com desfalques importantes na zaga.",
        "Probabilidade alta de empate no primeiro tempo."
    ]
    return random.choice(analises) + " IA Confidence: 85%"

# Monitoramento de Odds (Simulado)
async def market_scanner(app):
    while True:
        await asyncio.sleep(300) # Scaneia a cada 5 minutos
        # Simulação: Se achar uma "Odd de Valor"
        if random.random() < 0.1: # 10% de chance de achar algo
            msg = "🚨 **ALERTA DE VALOR (Dropping Odds)**\n\nJogo: Team A vs Team B\nOdd caiu de 2.00 para 1.70!\n🔥 Aposte Agora!"
            # Envia para todos os usuários (ou só VIPs)
            for uid in db["users"]:
                try: await app.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                except: pass

# ================= GRÁFICOS =================
def generate_profit_chart(user_id):
    history = db["users"].get(str(user_id), {}).get("history", [])
    if not history: return None
    
    bankroll = [1000] # Banca inicial fictícia
    dates = ["Início"]
    
    current = 1000
    for bet in history[-10:]: # Últimas 10
        if bet['result'] == 'green': current += bet['profit']
        elif bet['result'] == 'red': current -= bet['stake']
        bankroll.append(current)
        dates.append(bet['date'][:5]) # dd/mm
        
    plt.figure(figsize=(6, 4))
    plt.plot(dates, bankroll, marker='o', color='green', linewidth=2)
    plt.title('Evolução da Banca (Simulado)')
    plt.grid(True, linestyle='--', alpha=0.5)
    
    buf = os.sys.modules['io'].BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    return buf

# ================= HANDLERS DO BOT =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    
    # Registra usuário se novo
    if uid not in db["users"]:
        db["users"][uid] = {"vip_expiry": None, "bank": 1000, "history": []}
        save_db(db)
    
    status = "💎 VIP ATIVO" if is_vip(uid) else "👤 Membro Grátis"
    if check_admin(uid): status = "👑 ADMIN (GOD MODE)"
    
    kb = [
        [InlineKeyboardButton("📊 Minha Banca", callback_data="my_stats"),
         InlineKeyboardButton("🔑 Ativar VIP", callback_data="enter_key")],
        [InlineKeyboardButton("🆘 Suporte", url="https://t.me/seusuario")]
    ]
    
    if check_admin(uid):
        kb.append([InlineKeyboardButton("📢 Enviar TIP (Admin)", callback_data="admin_panel")])
    
    await update.message.reply_text(
        f"⚽ **DVD TIPS V2.0**\n\nOlá {user.first_name}!\nStatus: **{status}**\n\nUse o menu abaixo:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

# --- SISTEMA VIP ---
async def enter_key_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("Digite sua chave VIP (Ex: `DVD-1A2B...`):")
    return 1 # Estado esperando chave

async def process_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    uid = str(update.effective_user.id)
    
    if key in db["keys"]:
        days = db["keys"].pop(key)
        new_expiry = datetime.now() + timedelta(days=days)
        db["users"][uid]["vip_expiry"] = new_expiry.strftime("%Y-%m-%d %H:%M:%S")
        save_db(db)
        await update.message.reply_text(f"✅ **VIP ATIVADO!**\nValidade: {days} dias.\nAgora você receberá as melhores Tips!", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Chave inválida ou já usada.")
    return ConversationHandler.END

# --- ÁREA DO ADMIN (ENVIAR TIPS) ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin(update.effective_user.id): return
    kb = [
        [InlineKeyboardButton("⚽ Criar Tip", callback_data="create_tip")],
        [InlineKeyboardButton("🔑 Gerar Chave VIP", callback_data="gen_key_menu")],
        [InlineKeyboardButton("✅ Marcar Green/Red", callback_data="result_menu")]
    ]
    await update.callback_query.edit_message_text("👑 **Painel do Chefe**", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# Gerar Chave
async def gen_key_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = generate_key(30)
    await update.callback_query.message.reply_text(f"🔑 **Nova Chave Gerada:**\n`{key}`\n(Copia e manda pro cliente)", parse_mode="Markdown")

# Criar TIP (Simples)
async def create_tip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text("Digite a TIP no formato:\n`TimeA vs TimeB | Aposta | Odd`")
    return 2 # Estado esperando tip

async def broadcast_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw = update.message.text.split("|")
        match, bet, odd = raw[0].strip(), raw[1].strip(), raw[2].strip()
        
        # Gera análise com IA (Mock)
        analysis = ai_analysis_mock(match)
        
        tip_id = str(uuid.uuid4())[:6]
        tip_data = {"id": tip_id, "match": match, "bet": bet, "odd": odd, "status": "pending", "date": datetime.now().strftime("%d/%m")}
        db["tips"].append(tip_data)
        save_db(db)
        
        msg = f"💎 **DVD TIP OURO** 💎\n\n⚽ **{match}**\n🎯 **{bet}**\n📈 Odd: {odd}\n\n🤖 **IA Diz:** _{analysis}_"
        
        # Envia para todos (Filtra VIPs se quiser depois)
        count = 0
        for uid in db["users"]:
            try:
                await context.bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                count += 1
            except: pass
            
        await update.message.reply_text(f"✅ Tip enviada para {count} usuários!")
    except:
        await update.message.reply_text("❌ Formato errado. Use `Time vs Time | Aposta | Odd`")
    return ConversationHandler.END

# --- RESULTADOS E GRÁFICOS ---
async def result_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not db["tips"]:
        await update.callback_query.edit_message_text("Sem tips pendentes.")
        return
    
    kb = []
    for tip in db["tips"][-5:]: # Ultimas 5
        if tip["status"] == "pending":
            kb.append([InlineKeyboardButton(f"✅ {tip['match']} (Green)", callback_data=f"set_green_{tip['id']}")])
            kb.append([InlineKeyboardButton(f"❌ {tip['match']} (Red)", callback_data=f"set_red_{tip['id']}")])
            
    await update.callback_query.edit_message_text("Definir Resultados:", reply_markup=InlineKeyboardMarkup(kb))

async def set_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    action, tip_id = data.split("_")[1], data.split("_")[2]
    
    # Atualiza DB
    for tip in db["tips"]:
        if tip["id"] == tip_id:
            tip["status"] = action
            
            # Notifica usuários e atualiza histórico (Simulação)
            result_msg = "✅ **GREEN!** Lucro no bolso!" if action == "green" else "❌ **RED.** Acontece, gestão de banca!"
            final_msg = f"🏁 **Resultado Final:**\n⚽ {tip['match']}\n{result_msg}"
            
            # Atualiza histórico fictício dos usuários para gerar gráfico
            for uid in db["users"]:
                profit = 100 if action == "green" else -100 # Valor fixo simulado
                db["users"][uid]["history"].append({"date": tip["date"], "result": action, "profit": profit, "stake": 50})
            
            save_db(db)
            
            # Broadcast resultado
            for uid in db["users"]:
                try: await context.bot.send_message(chat_id=uid, text=final_msg, parse_mode="Markdown")
                except: pass
                
    await query.edit_message_text(f"Resultado {action.upper()} definido!")

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.callback_query.from_user.id)
    chart = generate_profit_chart(uid)
    
    if chart:
        await update.callback_query.message.reply_photo(chart, caption="📊 **Sua Evolução Recente**", parse_mode="Markdown")
    else:
        await update.callback_query.message.reply_text("📉 Você ainda não tem histórico suficiente para gerar gráfico.")

# ================= EXECUÇÃO =================
if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERRO: Configure o TOKEN nas variáveis de ambiente.")
        sys.exit()

    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers Conversacionais
    vip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_key_handler, pattern="^enter_key$")],
        states={1: [MessageHandler(filters.TEXT, process_key)]},
        fallbacks=[]
    )
    
    tip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_tip_start, pattern="^create_tip$")],
        states={2: [MessageHandler(filters.TEXT, broadcast_tip)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(vip_conv)
    app.add_handler(tip_conv)
    
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(gen_key_menu, pattern="^gen_key_menu$"))
    app.add_handler(CallbackQueryHandler(result_menu, pattern="^result_menu$"))
    app.add_handler(CallbackQueryHandler(set_result, pattern="^set_"))
    app.add_handler(CallbackQueryHandler(my_stats, pattern="^my_stats$"))
    
    # Inicia scanner de mercado em segundo plano
    asyncio.create_task(keep_alive_async())
    # Note: Market scanner loop would need to be inside an async loop or separate thread correctly
    # For simplicity in this structure, we rely on manual tips, but the function is there.
    
    print("🤖 DVD TIPS V2.0 ONLINE!")
    app.run_polling()