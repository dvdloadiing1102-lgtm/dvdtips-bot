import os
import logging
import httpx
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ================= LOG =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"

VIP_TEAMS = [
    "flamengo", "corinthians", "real madrid",
    "barcelona", "arsenal", "manchester city",
    "psg", "chelsea", "liverpool"
]

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦁 BOT ALL IN SUPREMO ONLINE\n"
        "Digite /hoje para jogos de hoje\n"
        "Digite /allin para TOP picks"
    )

# ================= FETCH GAMES =================
async def fetch_today_games():
    headers = {"x-apisports-key": API_FOOTBALL_KEY}

    now_br = datetime.now(timezone.utc) - timedelta(hours=3)
    today = now_br.date().isoformat()

    params = {"date": today}
    games = []

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(API_FOOTBALL_URL, headers=headers, params=params)
        data = r.json()
        fixtures = data.get("response", [])

        logging.info(f"⚽ Jogos retornados: {len(fixtures)}")

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
                logging.error(f"Erro jogo: {e}")

    # FAILSAFE: nunca retorna vazio
    if not games and fixtures:
        for f in fixtures[:5]:
            home = f["teams"]["home"]["name"]
            away = f["teams"]["away"]["name"]
            games.append({
                "match": f"{home} x {away}",
                "league": f["league"]["name"],
                "time": "Hoje",
                "score": 1
            })

    games.sort(key=lambda x: x["score"], reverse=True)
    return games

# ================= /HOJE =================
async def hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()

    if not games:
        await update.message.reply_text("❌ Nenhum jogo encontrado hoje.")
        return

    text = "⚽ JOGOS DE HOJE:\n\n"
    for g in games[:10]:
        text += f"🔥 {g['match']} — {g['time']}\n🏆 {g['league']}\n\n"

    await update.message.reply_text(text)

# ================= /ALLIN =================
async def allin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()

    if not games:
        await update.message.reply_text("❌ Nada hoje, mas amanhã amassamos 💀")
        return

    g = games[0]

    await update.message.reply_text(
        f"🦁 ALL IN SUPREMO\n\n"
        f"🔥 {g['match']}\n"
        f"🏆 {g['league']}\n"
        f"⏰ {g['time']}\n\n"
        f"💰 Confiança: ALTA"
    )

# ================= MAIN =================
def main():
    logging.info("🦁 BOT ALL IN SUPREMO ONLINE")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hoje", hoje))
    app.add_handler(CommandHandler("allin", allin))

    # 🔥 SEM asyncio.run — Render Safe
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()