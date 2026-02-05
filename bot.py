import os
import asyncio
import httpx
import feedparser
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
NEWS_FEED = os.getenv("NEWS_FEED")

# Jogos de exemplo (em produção você pode puxar de API real)
FUTEBOL_JOGOS = [
    {"match": "Corinthians x Palmeiras", "odd": 1.62, "tipo": "Favorito vence"},
    {"match": "Atalanta x Juventus", "odd": 1.55, "tipo": "Favorito vence"},
    {"match": "Real Madrid x Barcelona", "odd": 1.5, "tipo": "Favorito vence"},
    {"match": "Manchester City x Arsenal", "odd": 1.65, "tipo": "Favorito vence"},
]

NBA_JOGOS = [
    {"match": "Lakers x Warriors", "odd": 1.72, "tipo": "Favorito vence"},
    {"match": "Bucks x Heat", "odd": 1.68, "tipo": "Favorito vence"},
]

MUITA_ODD_JOGOS = FUTEBOL_JOGOS[:5] + NBA_JOGOS[:2]  # Múltipla 20 odds
TOTAL_ODD = 1.62*1.55*1.5*1.65*1.72*1.68  # só exemplo

# --- Funções ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("All In Supremo", callback_data="all_in")],
        [InlineKeyboardButton("Múltipla 20 Odd", callback_data="multi_odd"),
         InlineKeyboardButton("Notícias Futebol", callback_data="news")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Escolha uma opção:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "top_jogos":
        text = "🔥 TOP JOGOS HOJE\n"
        for j in FUTEBOL_JOGOS:
            text += f"{j['match']} - Odd: @{j['odd']}\n"
        await query.message.reply_text(text + "\n✅ POSTAR NO CANAL")
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)

    elif data == "nba_hoje":
        text = "🏀 NBA HOJE\n"
        for j in NBA_JOGOS:
            text += f"{j['match']} - Odd: @{j['odd']}\n"
        await query.message.reply_text(text + "\n✅ POSTAR NO CANAL")
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)

    elif data == "troco_pao":
        text = "💣 TROCO DO PÃO — MÚLTIPLA\n"
        for j in FUTEBOL_JOGOS[:3]:
            text += f"{j['match']} @ {j['odd']}\n"
        await query.message.reply_text(text + "\n✅ POSTAR NO CANAL")
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)

    elif data == "all_in":
        text = "🦁 ALL IN SUPREMO — PICK DO DIA\n"
        j = FUTEBOL_JOGOS[0]
        text += f"{j['match']} - {j['tipo']} @ {j['odd']}\nConfiança: ALTÍSSIMA"
        await query.message.reply_text(text + "\n✅ POSTAR NO CANAL")
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)

    elif data == "multi_odd":
        text = "🎯 MÚLTIPLA 20 ODD\n"
        for j in MUITA_ODD_JOGOS:
            text += f"{j['match']} @ {j['odd']}\n"
        text += f"🔥 TOTAL ODD: @{TOTAL_ODD:.2f}"
        await query.message.reply_text(text + "\n✅ POSTAR NO CANAL")
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)

    elif data == "news":
        news_text = "⚽ NOTÍCIAS DE FUTEBOL HOJE\n"
        feed = feedparser.parse(NEWS_FEED)
        for entry in feed.entries[:5]:
            news_text += f"{entry.title} — {entry.link}\n"
        await query.message.reply_text(news_text + "\n✅ POSTAR NO CANAL")
        await context.bot.send_message(chat_id=CHANNEL_ID, text=news_text)

# --- Main ---
async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())