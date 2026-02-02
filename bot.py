import logging
import requests
import datetime
import random
import os
import threading
import time
import sqlite3
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

# --- CONFIGURAÇÃO ---
# Se não encontrar a variável de ambiente, usa o token padrão (Cuidado com tokens expostos)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4")
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# Estados da Conversa
(MENU, ADD_VALOR, ADD_ODD, ADD_DESC) = range(4)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- BANCO DE DADOS (SQLite) ---
class BetDatabase:
    def __init__(self, db_path="bets.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        # check_same_thread=False evita erro de thread no Render
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.get_connection() as conn:
            conn.cursor().execute("""CREATE TABLE IF NOT EXISTS bets (id INTEGER PRIMARY KEY, user_id INTEGER, value REAL, odd REAL, description TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit()

    def add_bet(self, uid, val, odd, desc):
        with self.get_connection() as conn:
            conn.cursor().execute("INSERT INTO bets (user_id, value, odd, description) VALUES (?, ?, ?, ?)", (uid, val, odd, desc))
            conn.commit()

    def get_pending(self, uid):
        with self.get_connection() as conn:
            return conn.cursor().execute("SELECT id, value, odd, description FROM bets WHERE user_id = ? AND status = 'pending'", (uid,)).fetchall()

    def resolve_bet(self, bet_id, status): # status: 'green' ou 'red'
        with self.get_connection() as conn:
            conn.cursor().execute("UPDATE bets SET status = ? WHERE id = ?", (status, bet_id))
            conn.commit()

    def get_stats(self, uid):
        with self.get_connection() as conn:
            rows = conn.cursor().execute("SELECT status, value, odd FROM bets WHERE user_id = ? AND status != 'pending'", (uid,)).fetchall()
        invested = 0
        returned = 0
        greens = 0
        reds = 0
        for status, val, odd in rows:
            invested += val
            if status == 'green':
                returned += val * odd
                greens += 1
            else:
                reds += 1
        return invested, returned, greens, reds

db = BetDatabase()

# --- SERVIDOR FLASK (ANTI-SONO) ---
flask_app = Flask(__name__) # Mudei o nome para não conflitar

@flask_app.route('/')
def home(): return "Bot Apostas PRO Online ⚽"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

def keep_alive(): 
    while True: 
        try:
            requests.get("http://127.0.0.1:10000")
        except:
            pass
        time.sleep(600)

# --- LÓGICA ODDS (TIPS) ---
SOCCER_LEAGUES = ['soccer_brazil_campeonato', 'soccer_epl', 'soccer_spain_la_liga', 'soccer_uefa_champs_league']

def get_odds(sport):
    try: 
        resp = requests.get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds/", params={'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'})
        if resp.status_code == 200:
            return resp.json()
        return []
    except: 
        return []

async def create_tip_message():
    events = []
    # Pega apenas 1 liga para ser rápido, ou todas se quiser
    for l in SOCCER_LEAGUES: 
        data = get_odds(l)
        if data: events.extend(data)
    
    if not events: return "⚠️ <b>Aviso:</b> Sem dados da API no momento.", None
    
    tips = []
    for e in events[:15]: # Analisa os primeiros 15 jogos
        try:
            home = e['home_team']
            away = e['away_team']
            # Verifica se tem odds
            if not e['bookmakers']: continue
            outcomes = e['bookmakers'][0]['markets'][0]['outcomes']
            
            for o in outcomes:
                # Estratégia simples: Odd entre 1.50 e 2.20 (Valor)
                if 1.50 <= o['price'] <= 2.20:
                    tips.append(f"⚽ <b>{home} x {away}</b>\n🎯 {o['name']} @ {o['price']}")
                    break
        except: continue
    
    if not tips: return "⚠️ Sem oportunidades de valor encontradas.", None
    
    # Seleciona 3 dicas aleatórias
    selected_tips = random.sample(tips, min(3, len(tips)))
    msg = "🏆 <b>DICAS DO DIA (IA)</b>\n\n" + "\n\n".join(selected_tips)
    return msg, None

# --- FLUXOS DO BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📝 Registrar Aposta", callback_data='add_bet')],
        [InlineKeyboardButton("✅ Resolver Pendentes", callback_data='resolve_bet')],
        [InlineKeyboardButton("📈 Meu Relatório", callback_data='my_stats')],
        [InlineKeyboardButton("🎲 Gerar Tips (Canal)", callback_data='gen_tips')]
    ]
    
    msg = "⚽ <b>GESTOR DE BANCA PRO</b>\nO que deseja fazer hoje?"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return MENU

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    
    if q.data == 'gen_tips':
        await q.edit_message_text("⏳ <b>Analisando Mercado...</b>", parse_mode=ParseMode.HTML)
        try:
            txt, _ = await create_tip_message()
            # Envia para o canal se configurado, senão envia para o usuário
            try:
                await context.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                await q.edit_message_text(f"✅ <b>Postado no canal!</b>\n\n{txt}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
            except:
                 await q.edit_message_text(f"⚠️ Não consegui postar no canal (verifique admin).\n\n{txt}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
        except Exception as e:
            await q.edit_message_text(f"Erro na API: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
        return MENU

    if q.data == 'add_bet':
        await q.edit_message_text("💰 <b>Digite o VALOR da aposta:</b>\n(Ex: 50.00)", parse_mode=ParseMode.HTML)
        return ADD_VALOR
    
    if q.data == 'my_stats':
        inv, ret, g, r = db.get_stats(uid)
        profit = ret - inv
        roi = (profit / inv * 100) if inv > 0 else 0
        
        msg = (f"📊 <b>SEU RELATÓRIO</b>\n\n"
               f"💵 Investido: R$ {inv:.2f}\n"
               f"💰 Retorno: R$ {ret:.2f}\n"
               f"➖➖➖➖➖➖➖\n"
               f"✅ Greens: {g} | ❌ Reds: {r}\n"
               f"📈 <b>LUCRO: R$ {profit:.2f}</b> (ROI {roi:.1f}%)")
        
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Voltar", callback_data='back')]]), parse_mode=ParseMode.HTML)
        return MENU

    if q.data == 'resolve_bet':
        pending = db.get_pending(uid)
        if not pending:
            await q.edit_message_text("🤷‍♂️ Nenhuma aposta pendente.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
            return MENU
        
        kb = []
        for pid, val, odd, desc in pending:
            kb.append([InlineKeyboardButton(f"{desc} (R${val})", callback_data=f"res_{pid}")])
        kb.append([InlineKeyboardButton("🔙 Voltar", callback_data='back')])
        
        await q.edit_message_text("Qual aposta finalizou?", reply_markup=InlineKeyboardMarkup(kb))
        return MENU
    
    if q.data == 'back':
        await start(update, context)
        return MENU
    
    if q.data.startswith('res_'):
        pid = q.data.split('_')[1]
        context.user_data['res_id'] = pid
        kb = [[InlineKeyboardButton("✅ GREEN (Ganhou)", callback_data='set_green')],
              [InlineKeyboardButton("❌ RED (Perdeu)", callback_data='set_red')],
              [InlineKeyboardButton("🔙 Voltar", callback_data='resolve_bet')]]
        await q.edit_message_text("Qual foi o resultado?", reply_markup=InlineKeyboardMarkup(kb))
        return MENU

    if q.data.startswith('set_'):
        status = q.data.split('_')[1] # green or red
        pid = context.user_data['res_id']
        db.resolve_bet(pid, status)
        await q.edit_message_text(f"✅ Aposta marcada como <b>{status.upper()}</b>!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data='back')]]), parse_mode=ParseMode.HTML)
        return MENU

# --- WIZARD ADD BET ---
async def receive_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(',', '.'))
        context.user_data['bet_val'] = val
        await update.message.reply_text("🔢 <b>Qual a ODD?</b>\n(Ex: 1.80)", parse_mode=ParseMode.HTML)
        return ADD_ODD
    except:
        await update.message.reply_text("❌ Valor inválido. Digite apenas números.")
        return ADD_VALOR

async def receive_odd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        odd = float(update.message.text.replace(',', '.'))
        context.user_data['bet_odd'] = odd
        await update.message.reply_text("📝 <b>Descrição da Aposta:</b>\n(Ex: Flamengo vence, Over 2.5 gols...)", parse_mode=ParseMode.HTML)
        return ADD_DESC
    except:
        await update.message.reply_text("❌ Odd inválida. Digite apenas números (ex: 2.10).")
        return ADD_ODD

async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    desc = update.message.text
    val = context.user_data['bet_val']
    odd = context.user_data['bet_odd']
    
    db.add_bet(uid, val, odd, desc)
    
    msg = (f"✅ <b>Aposta Registrada!</b>\n\n"
           f"🎯 {desc}\n"
           f"💰 R$ {val:.2f} @ {odd:.2f}\n"
           f"<i>Boa sorte!</i>")
    
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data='back')]]), parse_mode=ParseMode.HTML)
    return MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Cancelado.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
    return MENU

if __name__ == '__main__':
    # Inicia threads em segundo plano
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    
    # Inicia Bot Telegram
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [CallbackQueryHandler(menu_handler)],
            ADD_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_valor)],
            ADD_ODD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_odd)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc)]
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(conv_handler)
    print("Bot de Apostas Iniciado...")
    # drop_pending_updates=True limpa comandos velhos que podem travar o bot
    application.run_polling(drop_pending_updates=True)
