import os
import sqlite3
import logging
import sys
import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import io
import csv
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters, ConversationHandler
)

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# ================= CONFIG =================

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    print("❌ TELEGRAM_TOKEN NÃO CONFIGURADO")
    sys.exit()

logging.basicConfig(level=logging.INFO)

# ================= STATES =================

(SELECT_ACTION, GASTO_VALOR, GASTO_CAT, GASTO_DESC,
 GANHO_VALOR, GANHO_CAT, DEL_ID) = range(7)

# ================= DATABASE =================

class DB:
    def __init__(self, db_path="finance_bot.db"):
        self.db_path = db_path
        self.init_db()

    def conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.conn() as c:
            cur = c.cursor()

            cur.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                username TEXT
            )""")

            cur.execute("""CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                name TEXT,
                type TEXT
            )""")

            cur.execute("""CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                type TEXT,
                amount REAL,
                category TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

            c.commit()

db = DB()

# ================= CORE =================

def init_user(tid, username):
    with db.conn() as c:
        cur = c.cursor()
        cur.execute("SELECT id FROM users WHERE telegram_id=?", (tid,))
        res = cur.fetchone()

        if res:
            return res[0]

        cur.execute("INSERT INTO users VALUES(NULL, ?, ?)", (tid, username))
        uid = cur.lastrowid

        exp = ["Alimentação", "Transporte", "Lazer", "Contas"]
        inc = ["Salário", "Extra"]

        for e in exp:
            cur.execute("INSERT INTO categories VALUES(NULL, ?, ?, 'expense')", (uid, e))

        for i in inc:
            cur.execute("INSERT INTO categories VALUES(NULL, ?, ?, 'income')", (uid, i))

        c.commit()
        return uid


def get_categories(uid, ctype):
    with db.conn() as c:
        rows = c.cursor().execute(
            "SELECT name FROM categories WHERE user_id=? AND type=?",
            (uid, ctype)
        ).fetchall()
        return [r[0] for r in rows]


def add_transaction(uid, t, amount, cat, desc):
    with db.conn() as c:
        c.cursor().execute(
            "INSERT INTO transactions VALUES(NULL, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (uid, t, amount, cat, desc)
        )
        c.commit()


def get_summary(uid):
    with db.conn() as c:
        rows = c.cursor().execute(
            "SELECT type, amount, category FROM transactions WHERE user_id=?",
            (uid,)
        ).fetchall()

    summary = {"income": 0, "expense": 0, "cats": {}}

    for t, amount, cat in rows:
        if t == "income":
            summary["income"] += amount
        else:
            summary["expense"] += amount
            summary["cats"][cat] = summary["cats"].get(cat, 0) + amount

    return summary


def chart(uid):
    summary = get_summary(uid)
    cats = summary["cats"]

    if not cats:
        return None

    plt.figure(figsize=(6, 6))
    plt.pie(cats.values(), labels=cats.keys(), autopct="%1.1f%%")
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    return buf


def get_last(uid):
    with db.conn() as c:
        return c.cursor().execute(
            "SELECT id, type, amount, category FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 15",
            (uid,)
        ).fetchall()


def delete_tx(uid, tid):
    with db.conn() as c:
        cur = c.cursor()
        cur.execute("DELETE FROM transactions WHERE id=? AND user_id=?", (tid, uid))
        c.commit()
        return cur.rowcount > 0


def export_csv(uid):
    with db.conn() as c:
        rows = c.cursor().execute(
            "SELECT type, amount, category, description, created_at FROM transactions WHERE user_id=?",
            (uid,)
        ).fetchall()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Tipo", "Valor", "Categoria", "Descrição", "Data"])

    for r in rows:
        writer.writerow(["Entrada" if r[0] == "income" else "Saída", r[1], r[2], r[3], r[4]])

    return out.getvalue()


def export_pdf(uid, filename="extrato.pdf"):
    rows = get_last(uid)

    doc = SimpleDocTemplate(filename, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("Extrato Financeiro", styles["Heading1"]))
    elements.append(Spacer(1, 20))

    data = [["Tipo", "Valor", "Categoria", "Descrição"]]

    for r in rows:
        t = "ENTRADA" if r[1] == "income" else "SAÍDA"
        data.append([t, f"R$ {r[2]:.2f}", r[3], r[4]])

    table = Table(data)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 1, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey)
    ]))

    elements.append(table)
    doc.build(elements)

# ================= UI =================

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 Novo Gasto", callback_data="gasto"),
         InlineKeyboardButton("📈 Novo Ganho", callback_data="ganho")],

        [InlineKeyboardButton("📊 Saldo", callback_data="saldo"),
         InlineKeyboardButton("🍕 Gráfico", callback_data="grafico")],

        [InlineKeyboardButton("📋 Detalhes", callback_data="detalhes"),
         InlineKeyboardButton("📄 Exportar", callback_data="exportar")],

        [InlineKeyboardButton("🗑️ Lixeira", callback_data="lixeira")]
    ])

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    init_user(user.id, user.username)

    await update.message.reply_text(
        f"👋 Olá {user.first_name}!",
        reply_markup=menu()
    )
    return SELECT_ACTION


async def start_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Digite o valor:")
    return GASTO_VALOR


async def gasto_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(",", "."))
        context.user_data["val"] = val

        uid = init_user(update.effective_user.id, update.effective_user.username)
        cats = get_categories(uid, "expense")

        keyboard = [[InlineKeyboardButton(c, callback_data=f"cat_{c}")] for c in cats]

        await update.message.reply_text("Categoria:", reply_markup=InlineKeyboardMarkup(keyboard))
        return GASTO_CAT

    except:
        await update.message.reply_text("❌ Valor inválido")
        return GASTO_VALOR


async def gasto_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat = update.callback_query.data.replace("cat_", "")
    context.user_data["cat"] = cat
    await update.callback_query.edit_message_text("Descrição:")
    return GASTO_DESC


async def gasto_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text
    uid = init_user(update.effective_user.id, update.effective_user.username)

    add_transaction(uid, "expense", context.user_data["val"], context.user_data["cat"], desc)

    await update.message.reply_text("✅ Gasto salvo", reply_markup=menu())
    return SELECT_ACTION


async def start_ganho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Digite o valor:")
    return GANHO_VALOR


async def ganho_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(",", "."))
        context.user_data["val"] = val

        uid = init_user(update.effective_user.id, update.effective_user.username)
        cats = get_categories(uid, "income")

        keyboard = [[InlineKeyboardButton(c, callback_data=f"inc_{c}")] for c in cats]

        await update.message.reply_text("Fonte:", reply_markup=InlineKeyboardMarkup(keyboard))
        return GANHO_CAT

    except:
        await update.message.reply_text("❌ Valor inválido")
        return GANHO_VALOR


async def ganho_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fonte = update.callback_query.data.replace("inc_", "")
    uid = init_user(update.effective_user.id, update.effective_user.username)

    add_transaction(uid, "income", context.user_data["val"], fonte, "Entrada")

    await update.callback_query.edit_message_text("✅ Ganho salvo", reply_markup=menu())
    return SELECT_ACTION


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = init_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    s = get_summary(uid)

    await update.callback_query.edit_message_text(
        f"📊 RESUMO\n\n🟢 {s['income']:.2f}\n🔴 {s['expense']:.2f}\n💰 {s['income'] - s['expense']:.2f}",
        reply_markup=menu()
    )
    return SELECT_ACTION


async def grafico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = init_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    buf = chart(uid)

    if buf:
        await update.callback_query.message.reply_photo(buf)
    else:
        await update.callback_query.answer("Sem dados")

    return SELECT_ACTION


async def detalhes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = init_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    items = get_last(uid)

    if not items:
        await update.callback_query.edit_message_text("📭 Sem registros", reply_markup=menu())
        return SELECT_ACTION

    msg = "📋 Últimos lançamentos:\n"
    for i in items:
        icon = "🟢" if i[1] == "income" else "🔴"
        msg += f"{icon} ID {i[0]} — R$ {i[2]:.2f} — {i[3]}\n"

    await update.callback_query.edit_message_text(msg, reply_markup=menu())
    return SELECT_ACTION


async def lixeira(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Digite ID para apagar:")
    return DEL_ID


async def delete_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = init_user(update.effective_user.id, update.effective_user.username)

    try:
        ok = delete_tx(uid, int(update.message.text))
        await update.message.reply_text("✅ Apagado" if ok else "❌ Não encontrado")
    except:
        await update.message.reply_text("❌ ID inválido")

    await update.message.reply_text("Menu:", reply_markup=menu())
    return SELECT_ACTION


async def export_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text(
        "Exportar:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 PDF", callback_data="pdf"),
             InlineKeyboardButton("📊 CSV", callback_data="csv")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu")]
        ])
    )
    return SELECT_ACTION


async def export_pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = init_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    export_pdf(uid)

    with open("extrato.pdf", "rb") as f:
        await update.callback_query.message.reply_document(f)

    return SELECT_ACTION


async def export_csv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = init_user(update.callback_query.from_user.id, update.callback_query.from_user.username)
    data = export_csv(uid)

    await update.callback_query.message.reply_document(io.BytesIO(data.encode()), filename="extrato.csv")
    return SELECT_ACTION


async def back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("🏠 Menu", reply_markup=menu())
    return SELECT_ACTION


# ================= RUN =================

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start_gasto, pattern="^gasto$"),
                CallbackQueryHandler(start_ganho, pattern="^ganho$"),
                CallbackQueryHandler(saldo, pattern="^saldo$"),
                CallbackQueryHandler(grafico, pattern="^grafico$"),
                CallbackQueryHandler(detalhes, pattern="^detalhes$"),
                CallbackQueryHandler(lixeira, pattern="^lixeira$"),
                CallbackQueryHandler(export_menu, pattern="^exportar$"),
                CallbackQueryHandler(export_pdf_handler, pattern="^pdf$"),
                CallbackQueryHandler(export_csv_handler, pattern="^csv$"),
                CallbackQueryHandler(back_menu, pattern="^menu$")
            ],

            GASTO_VALOR: [MessageHandler(filters.TEXT, gasto_valor)],
            GASTO_CAT: [CallbackQueryHandler(gasto_cat)],
            GASTO_DESC: [MessageHandler(filters.TEXT, gasto_desc)],

            GANHO_VALOR: [MessageHandler(filters.TEXT, ganho_valor)],
            GANHO_CAT: [CallbackQueryHandler(ganho_cat)],

            DEL_ID: [MessageHandler(filters.TEXT, delete_id)]
        },
        fallbacks=[CommandHandler("start", start)]
    )

    app.add_handler(conv)

    print("🤖 BOT ONLINE — SEM ERRO — RENDER OK")
    app.run_polling(drop_pending_updates=True)
