import os
import httpx
import feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
API_NBA_KEY = os.getenv("API_NBA_KEY")

FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures?next=8"
NBA_URL = "https://api-nba-v1.p.rapidapi.com/games?next=3"

NEWS_FEED = "https://www.espn.com/espn/rss/soccer/news"

# ---------------- START ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("⚽ Jogos Futebol + NBA", callback_data="games")],
        [InlineKeyboardButton("🔥 Múltipla Odd 20", callback_data="multi")],
        [InlineKeyboardButton("📰 Notícias Futebol", callback_data="news")]
    ]
    await update.message.reply_text("Escolha uma opção:", reply_markup=InlineKeyboardMarkup(buttons))

# ---------------- JOGOS ---------------- #

async def get_games():
    games = []

    async with httpx.AsyncClient() as client:
        foot = await client.get(
            FOOTBALL_URL,
            headers={"x-apisports-key": API_FOOTBALL_KEY}
        )

        if foot.status_code == 200:
            for g in foot.json()["response"][:8]:
                home = g["teams"]["home"]["name"]
                away = g["teams"]["away"]["name"]
                games.append(f"⚽ {home} vs {away}")

        nba = await client.get(
            NBA_URL,
            headers={
                "X-RapidAPI-Key": API_NBA_KEY,
                "X-RapidAPI-Host": "api-nba-v1.p.rapidapi.com"
            }
        )

        if nba.status_code == 200:
            for g in nba.json()["response"][:3]:
                home = g["teams"]["home"]["name"]
                away = g["teams"]["visitors"]["name"]
                games.append(f"🏀 {home} vs {away}")

    return games


async def games_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    games = await get_games()
    text = "🎯 **Jogos Reais Hoje:**\n\n" + "\n".join(games)

    buttons = [[InlineKeyboardButton("📢 POSTAR NO CANAL", callback_data="post_games")]]

    context.user_data["games_text"] = text

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

# ---------------- POSTAR JOGOS ---------------- #

async def post_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = context.user_data.get("games_text", "Erro ao gerar jogos")

    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")

    await query.edit_message_text("✅ Jogos postados no canal!")

# ---------------- MÚLTIPLA ---------------- #

async def multi_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    picks = [
        "⚽ Time A vence",
        "⚽ Over 2.5 gols",
        "⚽ Ambas marcam",
        "🏀 Vitória mandante",
        "⚽ Handicap -1",
        "⚽ Over escanteios",
        "⚽ Vitória fora",
        "⚽ Under 3.5",
        "🏀 Over pontos",
        "⚽ Ambas NÃO marcam"
    ]

    text = "🔥 **MÚLTIPLA ODD ~20**\n\n" + "\n".join(picks)

    buttons = [[InlineKeyboardButton("📢 POSTAR NO CANAL", callback_data="post_multi")]]

    context.user_data["multi_text"] = text

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def post_multi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = context.user_data.get("multi_text", "Erro")

    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")

    await query.edit_message_text("✅ Múltipla postada!")

# ---------------- NOTÍCIAS ---------------- #

async def news_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    feed = feedparser.parse(NEWS_FEED)

    news_list = []
    for entry in feed.entries[:5]:
        news_list.append(f"📰 {entry.title}")

    text = "📰 **Notícias Futebol:**\n\n" + "\n".join(news_list)

    buttons = [[InlineKeyboardButton("📢 POSTAR NO CANAL", callback_data="post_news")]]

    context.user_data["news_text"] = text

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def post_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = context.user_data.get("news_text", "Erro")

    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")

    await query.edit_message_text("✅ Notícias postadas!")

# ---------------- MAIN ---------------- #

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(games_button, pattern="games"))
app.add_handler(CallbackQueryHandler(post_games, pattern="post_games"))

app.add_handler(CallbackQueryHandler(multi_button, pattern="multi"))
app.add_handler(CallbackQueryHandler(post_multi, pattern="post_multi"))

app.add_handler(CallbackQueryHandler(news_button, pattern="news"))
app.add_handler(CallbackQueryHandler(post_news, pattern="post_news"))

app.run_polling()