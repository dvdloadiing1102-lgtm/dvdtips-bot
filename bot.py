import os
import logging
import httpx
import feedparser
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ================= LOG =================
logging.basicConfig(level=logging.INFO)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/soccer/odds"

VIP_TEAMS = [
    "flamengo", "corinthians", "real madrid", "barcelona",
    "arsenal", "manchester city", "psg", "chelsea", "liverpool",
    "bayern", "juventus", "milan", "inter"
]

# ================= FETCH FOOTBALL =================
async def fetch_today_games():
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    games = []

    now_br = datetime.now(timezone.utc) - timedelta(hours=3)
    today = now_br.date().isoformat()

    params = {"date": today}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(API_FOOTBALL_URL, headers=headers, params=params)
        data = r.json()
        fixtures = data.get("response", [])

        for f in fixtures:
            home = f["teams"]["home"]["name"]
            away = f["teams"]["away"]["name"]
            league = f["league"]["name"]
            date_str = f["fixture"]["date"]

            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00")) - timedelta(hours=3)

            full = f"{home} x {away}".lower()
            score = 100

            if any(v in full for v in VIP_TEAMS):
                score += 500

            games.append({
                "match": f"{home} x {away}",
                "league": league,
                "time": dt.strftime("%H:%M"),
                "score": score
            })

    games.sort(key=lambda x: x["score"], reverse=True)
    return games[:10]

# ================= FETCH ODDS =================
async def fetch_odds():
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            ODDS_URL,
            params={"apiKey": THE_ODDS_API_KEY, "regions": "eu"}
        )
        return r.json()

# ================= FETCH NBA =================
async def fetch_nba():
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params={"apiKey": THE_ODDS_API_KEY})
        return r.json()

# ================= FETCH NEWS =================
def fetch_news():
    feed = feedparser.parse("https://www.espn.com/espn/rss/news")
    news = []
    for n in feed.entries[:5]:
        news.append({"title": n.title, "link": n.link})
    return news

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🔥 Top Jogos", callback_data="top"),
            InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba")
        ],
        [
            InlineKeyboardButton("💣 Troco do Pão", callback_data="troco"),
            InlineKeyboardButton("🦁 ALL IN SUPREMO", callback_data="allin")
        ],
        [
            InlineKeyboardButton("📊 ROI", callback_data="roi"),
            InlineKeyboardButton("📰 Notícias", callback_data="news")
        ]
    ]

    await update.message.reply_text(
        "🦁 **PAINEL ALL IN SUPREMO — MODO ELITE**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= POST TO CHANNEL =================
async def post_channel(context, text):
    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="Markdown")

# ================= BUTTON HANDLER =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # TOP JOGOS
    if q.data == "top":
        games = await fetch_today_games()
        msg = "🔥 **TOP JOGOS HOJE**\n\n"

        for g in games:
            msg += f"⚽ {g['match']}\n🏆 {g['league']}\n⏰ {g['time']}\n\n"

        await q.message.reply_text(msg, parse_mode="Markdown")
        await post_channel(context, msg)

    # NBA
    elif q.data == "nba":
        nba = await fetch_nba()
        msg = "🏀 **NBA HOJE**\n\n"

        for g in nba[:3]:
            msg += f"🏀 {g['home_team']} x {g['away_team']}\n\n"

        await q.message.reply_text(msg)
        await post_channel(context, msg)

    # ALL IN
    elif q.data == "allin":
        games = await fetch_today_games()
        g = games[0]

        msg = (
            "🦁 **ALL IN SUPREMO — PICK DO DIA**\n\n"
            f"🔥 {g['match']}\n"
            f"🏆 {g['league']}\n"
            f"🎯 Pick: Favorito vence\n"
            f"💰 Confiança: ALTÍSSIMA\n"
        )

        await q.message.reply_text(msg, parse_mode="Markdown")
        await post_channel(context, msg)

    # TROCO DO PÃO
    elif q.data == "troco":
        games = await fetch_today_games()
        picks = games[:3]

        msg = "💣 **TROCO DO PÃO — MÚLTIPLA**\n\n"
        odd = 1

        for g in picks:
            msg += f"⚽ {g['match']} @1.50\n"
            odd *= 1.5

        msg += f"\n🔥 Odd Total aprox: @{round(odd, 2)}"

        await q.message.reply_text(msg)
        await post_channel(context, msg)

    # ROI
    elif q.data == "roi":
        msg = "📊 ROI Tracker ativo — relatório em breve"
        await q.message.reply_text(msg)

    # NEWS
    elif q.data == "news":
        news = fetch_news()
        msg = "📰 **NOTÍCIAS DO FUTEBOL**\n\n"

        for n in news:
            msg += f"🚨 {n['title']}\n🔗 {n['link']}\n\n"

        await q.message.reply_text(msg)
        await post_channel(context, msg)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    logging.info("🦁 BOT ALL IN SUPREMO ONLINE — MODO ELITE")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()