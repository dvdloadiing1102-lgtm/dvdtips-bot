import os
import asyncio
import logging
import httpx
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
PORT = int(os.getenv("PORT", 10000))

TIMEZONE = timedelta(hours=-3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("ALLIN")

log.info("🦁 BOT ALL IN SUPREMO ONLINE")

# ================= HTTP KEEP ALIVE =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALL IN SUPREMO ONLINE")

    def log_message(self, *args):
        pass

def start_http():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

# ================= UTIL =================
def hoje_str():
    return (datetime.now(timezone.utc) + TIMEZONE).strftime("%Y-%m-%d")

# ================= API FOOTBALL =================
async def buscar_jogos_api_football():
    if not API_FOOTBALL_KEY:
        return []

    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"date": hoje_str()}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=headers, params=params)
            data = r.json()
    except:
        return []

    jogos = []
    for item in data.get("response", []):
        jogos.append({
            "home": item["teams"]["home"]["name"],
            "away": item["teams"]["away"]["name"],
            "time": item["fixture"]["date"]
        })
    return jogos

# ================= ODDS API =================
async def buscar_jogos_odds():
    if not THE_ODDS_API_KEY:
        return []

    url = "https://api.the-odds-api.com/v4/sports/soccer/odds"
    params = {"apiKey": THE_ODDS_API_KEY, "regions": "eu", "markets": "h2h"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params=params)
            data = r.json()
    except:
        return []

    jogos = []
    hoje = hoje_str()

    for ev in data:
        if "commence_time" in ev and ev["commence_time"][:10] == hoje:
            jogos.append({
                "home": ev.get("home_team"),
                "away": ev.get("away_team"),
                "time": ev["commence_time"]
            })

    return jogos

# ================= CONSOLIDA =================
async def buscar_jogos_hoje():
    jogos = []
    jogos += await buscar_jogos_api_football()
    jogos += await buscar_jogos_odds()

    vistos = set()
    final = []

    for j in jogos:
        key = (j["home"], j["away"])
        if key not in vistos:
            vistos.add(key)
            final.append(j)

    return final

# ================= TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 ALL IN SUPREMO ONLINE\n\n/jogos")

async def jogos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Buscando jogos de hoje...")

    jogos = await buscar_jogos_hoje()

    if not jogos:
        await update.message.reply_text("❌ Nenhum jogo hoje.")
        return

    msg = "⚽ JOGOS DE HOJE\n\n"
    for j in jogos[:30]:
        hora = j["time"][11:16]
        msg += f"• {j['home']} x {j['away']} — {hora}\n"

    await update.message.reply_text(msg)

# ================= MAIN =================
async def main():
    # Start HTTP server thread
    threading.Thread(target=start_http, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jogos", jogos))

    log.info("🤖 BOT RODANDO")

    await app.run_polling()

# ================= RUN =================
if __name__ == "__main__":
    asyncio.run(main())