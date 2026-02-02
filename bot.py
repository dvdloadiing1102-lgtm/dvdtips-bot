import logging
import requests
import datetime
import asyncio
import random
import os
import pytz
import threading
import time
import sqlite3
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIG ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4")
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# Estados
(MENU, ADD_VALOR, ADD_ODD, ADD_DESC) = range(4)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- BANCO DE DADOS APOSTAS ---
class BetDatabase:
    def __init__(self, db_path="bets.db"):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.cursor().execute("""CREATE TABLE IF NOT EXISTS bets (id INTEGER PRIMARY KEY, user_id INTEGER, value REAL, odd REAL, description TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit()

    def add_bet(self, uid, val, odd, desc):
        with sqlite3.connect(self.db_path) as conn:
            conn.cursor().execute("INSERT INTO bets (user_id, value, odd, description) VALUES (?, ?, ?, ?)", (uid, val, odd, desc))
            conn.commit()

    def get_pending(self, uid):
        with sqlite3.connect(self.db_path) as conn:
            return conn.cursor().execute("SELECT id, value, odd, description FROM bets WHERE user_id = ? AND status = 'pending'", (uid,)).fetchall()

    def resolve_bet(self, bet_id, status): # status: 'green' or 'red'
        with sqlite3.connect(self.db_path) as conn:
            conn.cursor().execute("UPDATE bets SET status = ? WHERE id = ?", (status, bet_id))
            conn.commit()

    def get_stats(self, uid):
        with sqlite3.connect(self.db_path) as conn:
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

# --- SERVIDOR ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot Apostas PRO Online ⚽"
def run_flask(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
def keep_alive(): 
    while True: 
        try: requests.get("http://127.0.0.1:10000"); logging.info("Ping")
        except: pass
        time.sleep(600)

# --- LÓGICA ODDS ---
SOCCER_LEAGUES = ['soccer_brazil_campeonato', 'soccer_epl', 'soccer_spain_la_liga', 'soccer_uefa_champs_league']
def get_odds(sport):
    try: return requests.get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds/", params={'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}).json()
    except: return []

async def create_tip_message():
    events = []
    for l in SOCCER_LEAGUES: events.extend(get_odds(l))
    if not events: return "Sem jogos.", None
    
    tips = []
    for e in events[:10]:
        try:
            home = e['home_team']; away = e['away_team']
            for o in e['bookmakers'][0]['markets'][0]['outcomes']:
                if 1.50 <= o['price'] <= 2.20:
                    tips.append(f"⚽ <b>{home} x {away}</b>\n🎯 {o['name']} @ {o['price']}")
                    break
        except: continue
    
    if not tips: return "Sem oportunidades.", None
    return "🏆 <b>DICAS DO DIA</b>\n\n" + "\n\n".join(random.sample(tips, min(5, len(tips)))), None

# --- FLUXOS DO BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📝 Registrar Aposta", callback_data='add_bet')],
        [InlineKeyboardButton("✅ Resolver Pendentes", callback_data='resolve_bet')],
        [InlineKeyboardButton("📈 Meu Relatório", callback_data='my_stats')],
        [InlineKeyboardButton("🎲 Gerar Tips (Canal)", callback_data='gen_tips')]
    ]
    await update.message.reply_text("⚽ <b>GESTOR DE BANCA PRO</b>\nEscolha:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return MENU

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    
    if q.data == 'gen_tips':
        await q.edit_message_text("⏳ Analisando...")
        txt, _ = await create_tip_message()
        await context.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await q.edit_message_text("✅ Postado no canal!")
        return MENU

    if q.data == 'add_bet':
        await q.edit_message_text("💰 Digite o valor da aposta (ex: 50.00):")
        return ADD_VALOR
    
    if q.data == 'my_stats':
        inv, ret, g, r = db.get_stats(uid)
        profit = ret - inv
        roi = (profit / inv * 100) if inv > 0 else 0
        msg = f"📊 <b>SEU DESEMPENHO</b>\n\n💵 Investido: R$ {inv:.2f}\n💰 Retorno: R$ {ret:.2f}\n\n✅ Greens: {g}\n❌ Reds: {r}\n\n📈 <b>LUCRO: R$ {profit:.2f}</b> (ROI {roi:.1f}%)"
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]), parse_mode=ParseMode.HTML)
        return MENU

    if q.data == 'resolve_bet':
        pending = db.get_pending(uid)
        if not pending: await q.edit_message_text("Nada pendente.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]])); return MENU
        
        kb = []
        for pid, val, odd, desc in pending:
            kb.append([InlineKeyboardButton(f"{desc} (R${val})", callback_data=f"res_{pid}")])
        kb.append([InlineKeyboardButton("Voltar", callback_data='back')])
        await q.edit_message_text("Qual aposta finalizou?", reply_markup=InlineKeyboardMarkup(kb))
        return MENU
    
    if q.data == 'back':
        await start(q.message, context); return MENU
    
    if q.data.startswith('res_'):
        pid = q.data.split('_')[1]
        context.user_data['res_id'] = pid
        kb = [[InlineKeyboardButton("✅ GREEN (Ganhou)", callback_data='set_green'), InlineKeyboardButton("❌ RED (Perdeu)", callback_data='set_red')]]
        await q.edit_message_text("Qual foi o resultado?", reply_markup=InlineKeyboardMarkup(kb))
        return MENU

    if q.data.startswith('set_'):
        status = q.data.split('_')[1] # green or red
        pid = context.user_data['res_id']
        db.resolve_bet(pid, status)
        await q.edit_message_text(f"Aposta marcada como {status.upper()}!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Menu", callback_data='back')]]))
        return MENU

# --- WIZARD ADD BET ---
async def receive_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['bet_val'] = float(update.message.text.replace(',', '.')); await update.message.reply_text("🔢 Qual a ODD (ex: 1.80)?"); return ADD_ODD
    except: await update.message.reply_text("Valor inválido."); return ADD_VALOR

async def receive_odd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['bet_odd'] = float(update.message.text.replace(',', '.')); await update.message.reply_text("📝 Descrição (ex: Flamengo Vence):"); return ADD_DESC
    except: await update.message.reply_text("Odd inválida."); return ADD_ODD

async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    db.add_bet(uid, context.user_data['bet_val'], context.user_data['bet_odd'], update.message.text)
    await update.message.reply_text("✅ Aposta Registrada! Boa sorte.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Menu", callback_data='back')]]))
    return MENU

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [CallbackQueryHandler(menu_handler)],
            ADD_VALOR: [MessageHandler(filters.TEXT, receive_valor)],
            ADD_ODD: [MessageHandler(filters.TEXT, receive_odd)],
            ADD_DESC: [MessageHandler(filters.TEXT, receive_desc)]
        },
        fallbacks=[CommandHandler("start", start)]
    )
    app.add_handler(conv)
    app.run_polling()
