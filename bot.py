import os
import asyncio
import logging
import httpx
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logging.info("🦁 BOT ALL IN SUPREMO INICIANDO")

# =========================
# UTILS
# =========================
def now_br():
    return datetime.now(timezone.utc) - timedelta(hours=3)

# =========================
# API FOOTBALL — JOGOS HOJE
# =========================
async def fetch_api_football():
    url = "https://v3.football.api-sports.io/fixtures"
    today = now_br().date()

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY
    }

    params = {
        "date": today.isoformat()
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers=headers, params=params)
            data = r.json()

        fixtures = data.get("response", [])
        games = []

        for f in fixtures:
            home = f["teams"]["home"]["name"]
            away = f["teams"]["away"]["name"]
            league = f["league"]["name"]
            kickoff = f["fixture"]["date"]

            games.append({
                "home": home,
                "away": away,
                "league": league,
                "kickoff": kickoff
            })

        logging.info(f"API FOOTBALL retornou {len(games)} jogos")
        return games

    except Exception as e:
        logging.error(f"Erro API Football: {e}")
        return []

# =========================
# THE ODDS API — ODDS
# =========================
async def fetch_odds():
    url = "https://api.the-odds-api.com/v4/sports/soccer/odds"

    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h"
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            data = r.json()

        logging.info(f"THE ODDS retornou {len(data)} eventos")
        return data

    except Exception as e:
        logging.error(f"Erro The Odds API: {e}")
        return []

# =========================
# COMANDO START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦁 BOT ALL IN SUPREMO ONLINE\n\n"
        "Comandos:\n"
        "/hoje → Jogos de hoje\n"
        "/allin → Picks SUPREMAS"
    )

# =========================
# /HOJE — LISTA JOGOS
# =========================
async def hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Buscando jogos de hoje...")

    games = await fetch_api_football()

    if not games:
        await update.message.reply_text("❌ Nenhum jogo encontrado hoje")
        return

    msg = "📅 JOGOS DE HOJE:\n\n"
    for g in games[:12]:
        msg += f"⚽ {g['home']} x {g['away']}\n🏆 {g['league']}\n\n"

    await update.message.reply_text(msg)

# =========================
# /ALLIN — PICKS SUPREMAS
# =========================
async def allin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 MODO ALL IN SUPREMO ATIVO")

    games = await fetch_api_football()
    odds_data = await fetch_odds()

    picks = []

    for g in games:
        score = 1000

        big_teams = ["Real Madrid", "Barcelona", "Manchester City", "Bayern", "PSG"]
        if any(t in g["home"] or t in g["away"] for t in big_teams):
            score += 5000

        picks.append({
            "match": f"{g['home']} x {g['away']}",
            "league": g["league"],
            "score": score
        })

    picks = sorted(picks, key=lambda x: x["score"], reverse=True)

    if not picks:
        await update.message.reply_text("❌ Nada bom hoje — mercado fraco")
        return

    msg = "🔥 PICKS ALL IN SUPREMO:\n\n"
    for p in picks[:5]:
        msg += f"💎 {p['match']}\n🏆 {p['league']}\n⭐ Score: {p['score']}\n\n"

    await update.message.reply_text(msg)

# =========================
# MAIN — SAFE LOOP RENDER
# =========================
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hoje", hoje))
    app.add_handler(CommandHandler("allin", allin))

    logging.info("🤖 Bot iniciado — polling ativo")

    await app.run_polling(close_loop=False)

# =========================
# RUN SAFE (SEM LOOP CRASH)
# =========================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())