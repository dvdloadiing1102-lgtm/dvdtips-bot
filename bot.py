import os
import logging
import httpx
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"

VIP_TEAMS = [
    "flamengo", "corinthians", "real madrid", "barcelona",
    "arsenal", "manchester city", "psg", "chelsea", "liverpool",
    "bayern", "juventus", "milan", "inter"
]

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🔥 Top Jogos", callback_data="top"),
            InlineKeyboardButton("🏀 NBA", callback_data="nba")
        ],
        [
            InlineKeyboardButton("💣 Troco do Pão", callback_data="troco"),
            InlineKeyboardButton("📊 ROI", callback_data="roi")
        ],
        [
            InlineKeyboardButton("💬 Mensagem Livre", callback_data="msg")
        ]
    ]

    await update.message.reply_text(
        "🦁 **PAINEL ALL IN SUPREMO ONLINE**\n\n"
        "🔥 Clique abaixo ou digite comandos\n"
        "/hoje — Jogos hoje\n"
        "/allin — Pick Suprema\n",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= FETCH GAMES =================
async def fetch_today_games():
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    games = []

    now_br = datetime.now(timezone.utc) - timedelta(hours=3)
    today = now_br.date().isoformat()

    params = {
        "date": today,
        "status": "NS"
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(API_FOOTBALL_URL, headers=headers, params=params)
            data = r.json()
            fixtures = data.get("response", [])

            logging.info(f"⚽ Jogos encontrados: {len(fixtures)}")

            for f in fixtures:
                try:
                    home = f["teams"]["home"]["name"]
                    away = f["teams"]["away"]["name"]
                    league = f["league"]["name"]
                    date_str = f["fixture"]["date"]

                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00")) - timedelta(hours=3)

                    full = f"{home} x {away}".lower()
                    score = 1000

                    if any(v in full for v in VIP_TEAMS):
                        score += 5000

                    games.append({
                        "match": f"{home} x {away}",
                        "league": league,
                        "time": dt.strftime("%H:%M"),
                        "score": score
                    })

                except Exception as e:
                    logging.error(f"Erro parse jogo: {e}")

    except Exception as e:
        logging.error(f"Erro API Football: {e}")

    # ================= FALLBACK =================
    if not games:
        logging.warning("⚠️ API vazia — ativando fallback")

        games = [
            {"match": "Flamengo x Corinthians", "league": "Brasileirão", "time": "Hoje", "score": 9999},
            {"match": "Real Madrid x Barcelona", "league": "La Liga", "time": "Hoje", "score": 8888},
            {"match": "Manchester City x Arsenal", "league": "Premier League", "time": "Hoje", "score": 7777},
        ]

    games.sort(key=lambda x: x["score"], reverse=True)
    return games

# ================= HOJE =================
async def hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()

    text = "⚽ **JOGOS DE HOJE**\n\n"
    for g in games[:10]:
        text += (
            f"🔥 **{g['match']}**\n"
            f"🏆 {g['league']}\n"
            f"⏰ {g['time']}\n\n"
        )

    await update.message.reply_text(text, parse_mode="Markdown")

# ================= ALL IN =================
async def allin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()
    g = games[0]

    await update.message.reply_text(
        f"🦁 **ALL IN SUPREMO**\n\n"
        f"🔥 **{g['match']}**\n"
        f"🏆 {g['league']}\n"
        f"⏰ {g['time']}\n\n"
        f"💰 Confiança: **ALTÍSSIMA**\n"
        f"🚀 Hoje é dia de green",
        parse_mode="Markdown"
    )

# ================= BOTÕES =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "top":
        await hoje(update, context)

    elif query.data == "nba":
        await query.message.reply_text("🏀 NBA hoje: Em breve picks")

    elif query.data == "troco":
        await query.message.reply_text("💣 Troco do pão carregando...")

    elif query.data == "roi":
        await query.message.reply_text("📊 ROI Tracker ativo — histórico em breve")

    elif query.data == "msg":
        await query.message.reply_text("💬 Modo mensagem livre ativado")

# ================= MAIN =================
def main():
    logging.info("🦁 BOT ALL IN SUPREMO ONLINE")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hoje", hoje))
    app.add_handler(CommandHandler("allin", allin))
    app.add_handler(CallbackQueryHandler(buttons))

    # Render Safe — sem asyncio.run
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()