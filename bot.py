import os
import asyncio
import logging
import httpx
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

TIMEZONE = timedelta(hours=-3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ALLIN")

log.info("🦁 BOT ALL IN SUPREMO ONLINE")

# =========================
# UTIL
# =========================

def hoje_str():
    return (datetime.now(timezone.utc) + TIMEZONE).strftime("%Y-%m-%d")

# =========================
# API FOOTBALL
# =========================

async def buscar_jogos_api_football():
    if not API_FOOTBALL_KEY:
        log.warning("⚠️ API_FOOTBALL_KEY não configurada")
        return []

    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"date": hoje_str()}

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(url, headers=headers, params=params)
            data = r.json()
    except Exception as e:
        log.error(f"❌ API FOOTBALL ERRO: {e}")
        return []

    jogos = []
    for item in data.get("response", []):
        jogos.append({
            "home": item["teams"]["home"]["name"],
            "away": item["teams"]["away"]["name"],
            "time": item["fixture"]["date"]
        })

    log.info(f"⚽ API FOOTBALL retornou {len(jogos)} jogos")
    return jogos

# =========================
# THE ODDS API
# =========================

async def buscar_jogos_odds():
    if not THE_ODDS_API_KEY:
        log.warning("⚠️ THE_ODDS_API_KEY não configurada")
        return []

    url = "https://api.the-odds-api.com/v4/sports/soccer/odds"
    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": "us,uk,eu",
        "markets": "h2h"
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(url, params=params)
            data = r.json()
    except Exception as e:
        log.error(f"❌ ODDS API ERRO: {e}")
        return []

    jogos = []
    hoje = hoje_str()

    for event in data:
        if "commence_time" not in event:
            continue

        date_event = event["commence_time"][:10]
        if date_event == hoje:
            jogos.append({
                "home": event.get("home_team", "Casa"),
                "away": event.get("away_team", "Visitante"),
                "time": event["commence_time"]
            })

    log.info(f"🎯 ODDS API retornou {len(jogos)} jogos")
    return jogos

# =========================
# CONSOLIDA JOGOS
# =========================

async def buscar_jogos_hoje():
    jogos = []

    try:
        jogos += await buscar_jogos_api_football()
    except Exception as e:
        log.error(f"Erro API Football geral: {e}")

    try:
        jogos += await buscar_jogos_odds()
    except Exception as e:
        log.error(f"Erro Odds geral: {e}")

    vistos = set()
    unicos = []

    for j in jogos:
        chave = (j["home"], j["away"])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(j)

    log.info(f"✅ TOTAL jogos únicos hoje: {len(unicos)}")
    return unicos

# =========================
# HANDLERS TELEGRAM
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦁 ALL IN SUPREMO ONLINE\n\n"
        "/jogos — Jogos de hoje\n"
        "/status — Status das APIs"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📡 STATUS APIs\n\n"
    msg += f"API FOOTBALL: {'✅ OK' if API_FOOTBALL_KEY else '❌ SEM KEY'}\n"
    msg += f"THE ODDS API: {'✅ OK' if THE_ODDS_API_KEY else '❌ SEM KEY'}\n"
    await update.message.reply_text(msg)

async def jogos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Buscando jogos de hoje...")

    jogos = await buscar_jogos_hoje()

    if not jogos:
        await update.message.reply_text("❌ Nenhum jogo encontrado hoje.")
        return

    msg = "⚽ JOGOS DE HOJE\n\n"

    for j in jogos[:30]:
        hora = j["time"][11:16]
        msg += f"• {j['home']} x {j['away']} — {hora}\n"

    await update.message.reply_text(msg)

# =========================
# MAIN — PTB V20 SAFE
# =========================

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jogos", jogos))
    app.add_handler(CommandHandler("status", status))

    log.info("🤖 Iniciando bot...")

    await app.initialize()
    await app.start()

    log.info("✅ Polling iniciado")

    await app.bot.initialize()

    await asyncio.Event().wait()

# =========================
# SAFE START PYTHON 3.13
# =========================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())