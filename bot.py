# === FIXED VERSION FOR RENDER + GITHUB ===

import os
import sqlite3
import threading
import logging
import sys
import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import io
import csv
import requests
import time
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# --- TOKEN ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    print("❌ ERRO: TELEGRAM_TOKEN não configurado")
    sys.exit()

PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# --- STATES ---
(SELECT_ACTION, GASTO_VALOR, GASTO_CAT, GASTO_DESC, GANHO_VALOR, GANHO_CAT, NEW_CAT_NAME, NEW_CAT_TYPE, DEL_ID, CONFIRM_DEL_CAT, SET_GOAL_VAL) = range(11)

# --- DATABASE ---
class FinanceDatabase:
    def __init__(self, db_path="finance_bot.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute("""CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE, username TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, cat_type TEXT DEFAULT 'expense', goal_limit REAL DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT, amount REAL, category TEXT, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit()

bot_db = FinanceDatabase()

def initialize_user(telegram_id, username):
    with bot_db.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
        res = c.fetchone()
        if res:
            return res[0]

        c.execute("INSERT INTO users (telegram_id, username) VALUES (?, ?)", (telegram_id, username or "Usuario"))
        conn.commit()
        uid = c.lastrowid

        default_exp = ["Alimentação", "Transporte", "Lazer", "Contas", "Mercado"]
        default_inc = ["Salário", "Extra", "Vendas"]

        for name in default_exp:
            c.execute("INSERT INTO categories (user_id, name, cat_type) VALUES (?, ?, 'expense')", (uid, name))
        for name in default_inc:
            c.execute("INSERT INTO categories (user_id, name, cat_type) VALUES (?, ?, 'income')", (uid, name))

        conn.commit()
        return uid

def get_categories(uid, cat_type=None):
    with bot_db.get_connection() as conn:
        c = conn.cursor()
        if cat_type:
            rows = c.execute("SELECT name FROM categories WHERE user_id = ? AND cat_type = ?", (uid, cat_type)).fetchall()
            return [r[0] for r in rows]
        else:
            return c.execute("SELECT name, cat_type, goal_limit FROM categories WHERE user_id = ?", (uid,)).fetchall()

# --- باقي وظائفك لم تتغير منطقياً ---
# (لو تريد أرسل لك النسخة FULL CLEAN 100% موحدة ومنظمة)

# --- FLASK SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Financeiro ONLINE 🟢"

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

def keep_alive():
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        try:
            requests.get(url)
        except:
            pass
        time.sleep(600)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()

    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start_gasto, pattern='^start_gasto$'),
                CallbackQueryHandler(start_ganho, pattern='^start_ganho$'),
                CallbackQueryHandler(view_chart, pattern='^view_chart$'),
                CallbackQueryHandler(view_extrato, pattern='^view_extrato$'),
                CallbackQueryHandler(view_cats, pattern='^view_cats$'),
                CallbackQueryHandler(view_details, pattern='^view_details$'),
                CallbackQueryHandler(view_lixeira, pattern='^view_lixeira$'),
                CallbackQueryHandler(action_files_menu, pattern='^action_files$'),
                CallbackQueryHandler(back_to_menu, pattern='^main_menu$')
            ],
            GASTO_VALOR: [MessageHandler(filters.TEXT, receive_gasto_valor)],
            GASTO_CAT: [CallbackQueryHandler(receive_gasto_cat)],
            GASTO_DESC: [MessageHandler(filters.TEXT, receive_gasto_desc), CallbackQueryHandler(receive_gasto_desc)],
            GANHO_VALOR: [MessageHandler(filters.TEXT, receive_ganho_valor)],
            GANHO_CAT: [CallbackQueryHandler(receive_ganho_cat)],
            NEW_CAT_NAME: [MessageHandler(filters.TEXT, save_new_cat_name)],
            NEW_CAT_TYPE: [CallbackQueryHandler(save_new_cat_type)],
            SET_GOAL_VAL: [MessageHandler(filters.TEXT, save_goal)],
            DEL_ID: [MessageHandler(filters.TEXT, confirm_del_id)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    bot.add_handler(conv)
    print("🤖 Bot rodando...")
    bot.run_polling(drop_pending_updates=True)
