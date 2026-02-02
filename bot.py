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

# ========================= CONFIG =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    print("❌ ERRO: TELEGRAM_TOKEN não configurado")
    sys.exit()

PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# ========================= STATES =========================

(SELECT_ACTION, GASTO_VALOR, GASTO_CAT, GASTO_DESC, GANHO_VALOR, GANHO_CAT, NEW_CAT_NAME, NEW_CAT_TYPE, DEL_ID, CONFIRM_CAT, SET_GOAL_VAL) = range(11)

# ========================= DATABASE =========================

class FinanceDatabase:
    def __init__(self, db_path="finance_bot.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.get_connection() as conn:
            c = conn.cursor()

            c.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                username TEXT
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                name TEXT,
                cat_type TEXT DEFAULT 'expense',
                goal_limit REAL DEFAULT 0
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                type TEXT,
                amount REAL,
                category TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            conn.commit()

bot_db = FinanceDatabase()

# ========================= CORE =========================

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

        exp = ["Alimentação", "Transporte", "Lazer", "Contas", "Mercado"]
        inc = ["Salário", "Extra", "Vendas"]

        for name in exp:
            c.execute("INSERT INTO categories (user_id, name, cat_type) VALUES (?, ?, 'expense')", (uid, name))
        for name in inc:
            c.execute("INSERT INTO categories (user_id, name, cat_type) VALUES (?, ?, 'income')", (uid, name))

        conn.commit()
        return uid


def get_categories(uid, cat_type=None):
    with bot_db.get_connection() as conn:
        c = conn.cursor()
        if cat_type:
            rows = c.execute("SELECT name FROM categories WHERE user_id = ? AND cat_type = ?", (uid, cat_type)).fetchall()
            return [r[0] for r in rows]
        return c.execute("SELECT name, cat_type, goal_limit FROM categories WHERE user_id = ?", (uid,)).fetchall()


def add_transaction(uid, type_, amount, category, desc):
    with bot_db.get_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO transactions (user_id, type, amount, category, description) VALUES (?, ?, ?, ?, ?)",
            (uid, type_, amount, category, desc)
        )
        conn.commit()


def get_summary(uid):
    with bot_db.get_connection() as conn:
        rows = conn.cursor().execute("SELECT type, amount, category FROM transactions WHERE user_id = ?", (uid,)).fetchall()

    summary = {"income": 0, "expense": 0, "cats": {}}

    for t, amount, cat in rows:
        if t == "income":
            summary["income"] += amount
        else:
            summary["expense"] += amount
            summary["cats"][cat] = summary["cats"].get(cat, 0) + amount

    return summary


def generate_chart(uid):
    summary = get_summary(uid)
    cats = summary["cats"]

    if not cats:
        return None

    labels = list(cats.keys())
    sizes = list(cats.values())

    plt.figure(figsize=(6, 6))
    plt.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=140)
    plt.title("Gastos")

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    return buf


def get_detailed_list(uid):
    with bot_db.get_connection() as conn:
        return conn.cursor().execute(
            "SELECT id, type, amount, category, description FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 15",
            (uid,)
        ).fetchall()


def delete_transaction(uid, tid):
    with bot_db.get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tid, uid))
        conn.commit()
        return c.rowcount > 0


def export_csv(uid):
    with bot_db.get_connection() as conn:
        rows = conn.cursor().execute("SELECT type, amount, category, description, created_at FROM transactions WHERE user_id = ?", (uid,)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Tipo", "Valor", "Categoria", "Descricao", "Data"])

    for r in rows:
        writer.writerow(["Entrada" if r[0] == "income" else "Saída", r[1], r[2], r[3], r[4]])

    return output.getvalue()


def export_pdf(uid, filename):
    rows = get_detailed_list(uid)

    doc = SimpleDocTemplate(filename, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    elements.append(Paragraph("Extrato Financeiro", styles["Heading1"]))
    elements.append(Spacer(1, 20))

    data = [["Tipo", "Valor", "Categoria", "Descrição"]]

    for r in rows:
        tipo = "ENTRADA" if r[1] == "income" else "SAÍDA"
        data.append([tipo, f"R$ {r[2]:.2f}", r[3], r[4]])

    table = Table(data)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)
    ]))

    elements.append(table)
    doc.build(elements)


# ========================= UI =========================

def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📉 Novo Gasto", callback_data="start_gasto"),
         InlineKeyboardButton("📈 Novo Ganho", callback_data="start_ganho")],

        [InlineKeyboardButton("📊 Saldo", callback_data="view_extrato"),
         InlineKeyboardButton("🍕 Gráfico", callback_data="view_chart")],

        [InlineKeyboardButton("📋 Detalhes", callback_data="view_details"),
         InlineKeyboardButton("📄 PDF / CSV", callback_data="action_files")],

        [InlineKeyboardButton("🗑️ Lixeira", callback_data="view_lixeira")]
    ]

    return InlineKeyboardMarkup(keyboard)


# ========================= BOT HANDLERS =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    initialize_user(user.id, user.username)

    await update.message.reply_text(
        f"👋 Olá <b>{user.first_name}</b>!\nSeu App Financeiro está ONLINE 🟢",
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

    return SELECT_ACTION


async def start_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("💸 Digite o valor:")
    return GASTO_VALOR


async def receive_gasto_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(",", "."))
        context.user_data["temp_valor"] = val

        uid = initialize_user(update.effective_user.id, update.effective_user.username)
        cats = get_categories(uid, "expense")

        keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in cats]

        await update.message.reply_text("Escolha categoria:", reply_markup=InlineKeyboardMarkup(keyboard))
        return GASTO_CAT

    except:
        await update.message.reply_text("❌ Valor inválido")
        return GASTO_VALOR


async def receive_gasto_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = update.callback_query.data.replace("cat_", "")
    context.user_data["temp_cat"] = cat

    await update.callback_query.edit_message_text("📝 Descrição:")
    return GASTO_DESC


async def receive_gasto_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text if update.message else "Gasto"

    uid = initialize_user(update.effective_user.id, update.effective_user.username)
    val = context.user_data["temp_valor"]
    cat = context.user_data["temp_cat"]

    add_transaction(uid, "expense", val, cat, desc)

    await update.message.reply_text("✅ Gasto salvo!", reply_markup=get_main_menu_keyboard())
    return SELECT_ACTION


async def start_ganho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("💰 Digite o valor:")
    return GANHO_VALOR


async def receive_ganho_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(",", "."))
        context.user_data["temp_valor"] = val

        uid = initialize_user(update.effective_user.id, update.effective_user.username)
        cats = get_categories(uid, "income")

        keyboard = [[InlineKeyboardButton(c, callback_data=f"inc_{c}")] for c in cats]

        await update.message.reply_text("Fonte:", reply_markup=InlineKeyboardMarkup(keyboard))
        return GANHO_CAT

    except:
        await update.message.reply_text("❌ Valor inválido")
        return GANHO_VALOR


async def receive_ganho_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fonte = update.callback_query.data.replace("inc_", "")

    uid = initialize_user(update.effective_user.id, update.effective_user.username)
    add_transaction(uid, "income", context.user_data["temp_valor"], fonte, "Entrada")

    await update.callback_query.edit_message_text("✅ Ganho salvo!", reply_markup=get_main_menu_keyboard())
    return SELECT_ACTION


async def view_extrato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = initialize_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    s = get_summary(uid)

    await update.callback_query.edit_message_text(
        f"📊 RESUMO\n🟢 R$ {s['income']:.2f}\n🔴 R$ {s['expense']:.2f}\n💰 SALDO: R$ {s['income'] - s['expense']:.2f}",
        reply_markup=get_main_menu_keyboard()
    )
    return SELECT_ACTION


async def view_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = initialize_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    buf = generate_chart(uid)

    if buf:
        await update.callback_query.message.reply_photo(buf)
    else:
        await update.callback_query.answer("Sem dados")

    return SELECT_ACTION


async def view_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = initialize_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    items = get_detailed_list(uid)

    if not items:
        await update.callback_query.edit_message_text("📭 Nenhum registro")
        return SELECT_ACTION

    msg = "📋 Últimos lançamentos:\n"

    for i in items:
        icon = "🟢" if i[1] == "income" else "🔴"
        msg += f"{icon} R$ {i[2]:.2f} — {i[3]}\n"

    await update.callback_query.edit_message_text(msg, reply_markup=get_main_menu_keyboard())
    return SELECT_ACTION


async def view_lixeira(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🗑️ Digite ID para apagar:")
    return DEL_ID


async def confirm_del_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = initialize_user(update.effective_user.id, update.effective_user.username)

    try:
        deleted = delete_transaction(uid, int(update.message.text))
        await update.message.reply_text("✅ Apagado!" if deleted else "❌ Não encontrado")
    except:
        await update.message.reply_text("❌ ID inválido")

    await update.message.reply_text("Menu:", reply_markup=get_main_menu_keyboard())
    return SELECT_ACTION


async def action_files_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📄 PDF", callback_data="action_pdf"),
         InlineKeyboardButton("📊 CSV", callback_data="action_csv")],

        [InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]
    ]

    await update.callback_query.edit_message_text("Exportar:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION


async def action_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = initialize_user(update.callback_query.from_user.id, update.callback_query.from_user.username)

    export_pdf(uid, "extrato.pdf")

    with open("extrato.pdf", "rb") as f:
        await update.callback_query.message.reply_document(f)

    return SELECT_ACTION


async def action_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = initialize_user(update.callback_query.from_user.id, update.callback_query.from_user.username)

    data = export_csv(uid)
    await update.callback_query.message.reply_document(io.BytesIO(data.encode()), filename="extrato.csv")

    return SELECT_ACTION


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🏠 Menu Principal", reply_markup=get_main_menu_keyboard())
    return SELECT_ACTION


# ========================= FLASK =========================

app = Flask(__name__)

@app.route("/")
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


# ========================= RUN =========================

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()

    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start_gasto, pattern="^start_gasto$"),
                CallbackQueryHandler(start_ganho, pattern="^start_ganho$"),
                CallbackQueryHandler(view_extrato, pattern="^view_extrato$"),
                CallbackQueryHandler(view_chart, pattern="^view_chart$"),
                CallbackQueryHandler(view_details, pattern="^view_details$"),
                CallbackQueryHandler(view_lixeira, pattern="^view_lixeira$"),
                CallbackQueryHandler(action_files_menu, pattern="^action_files$"),
                CallbackQueryHandler(back_to_menu, pattern="^main_menu$")
            ],

            GASTO_VALOR: [MessageHandler(filters.TEXT, receive_gasto_valor)],
            GASTO_CAT: [CallbackQueryHandler(receive_gasto_cat)],
            GASTO_DESC: [MessageHandler(filters.TEXT, receive_gasto_desc)],

            GANHO_VALOR: [MessageHandler(filters.TEXT, receive_ganho_valor)],
            GANHO_CAT: [CallbackQueryHandler(receive_ganho_cat)],

            DEL_ID: [MessageHandler(filters.TEXT, confirm_del_id)]
        },
        fallbacks=[CommandHandler("start", start)]
    )

    bot.add_handler(conv)

    print("🤖 BOT ONLINE — RENDER READY")
    bot.run_polling(drop_pending_updates=True)
