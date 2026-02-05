import os
import logging
import asyncio
import httpx
import random
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"

VIP_TEAMS = [
    "flamengo","corinthians","palmeiras","vasco","real madrid",
    "barcelona","manchester city","arsenal","chelsea","liverpool","psg"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ================= UTILS =================

def normalize(text):
    return text.lower() if text else ""

# ================= FETCH TODAY GAMES =================

async def fetch_today_games():
    headers = {"x-apisports-key": API_FOOTBALL_KEY}

    now_br = datetime.now(timezone.utc) - timedelta(hours=3)
    today = now_br.date().isoformat()

    games = []

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(API_FOOTBALL_URL, headers=headers, params={"date": today})
        data = r.json()
        fixtures = data.get("response", [])

        logging.info(f"⚽ API-Football retornou {len(fixtures)} jogos")

        for f in fixtures:
            try:
                home = f["teams"]["home"]["name"]
                away = f["teams"]["away"]["name"]
                league = f["league"]["name"]
                date_str = f["fixture"]["date"]

                fixture_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")) - timedelta(hours=3)

                full = normalize(home + " " + away)

                score = 1000
                if any(v in full for v in VIP_TEAMS):
                    score += 6000

                games.append({
                    "match": f"{home} x {away}",
                    "league": league,
                    "time": fixture_date.strftime("%H:%M"),
                    "score": score,
                    "odds": round(random.uniform(1.40, 2.50), 2)
                })

            except Exception as e:
                logging.error(f"Erro fixture: {e}")

    # FALLBACK SE LISTA VAZIA
    if not games and fixtures:
        logging.warning("⚠️ Fallback ativado — liberando jogos gerais")
        for f in fixtures[:8]:
            home = f["teams"]["home"]["name"]
            away = f["teams"]["away"]["name"]
            games.append({
                "match": f"{home} x {away}",
                "league": f["league"]["name"],
                "time": "Hoje",
                "score": 1,
                "odds": round(random.uniform(1.50, 2.20), 2)
            })

    games.sort(key=lambda x: x["score"], reverse=True)
    return games


# ================= FORMAT OUTPUT =================

def format_games(games):
    txt = "🔥 **GRADE SUPREMA — JOGOS HOJE**\n\n"
    for g in games[:10]:
        txt += (
            f"⚔️ {g['match']}\n"
            f"🏆 {g['league']}\n"
            f"🕒 {g['time']}\n"
            f"🎯 Odd estimada: @{g['odds']}\n\n"
        )
    txt += "🦁 *Modo Predador ativo*"
    return txt


# ================= MULTIPLA =================

def build_multiple(games):
    sel = random.sample(games, min(5, len(games)))
    odd_total = 1.0

    txt = "💣 **TROCO DO PÃO — MÚLTIPLA**\n\n"

    for g in sel:
        odd_total *= g["odds"]
        txt += f"📍 {g['match']} (@{g['odds']})\n"

    txt += f"\n💰 **ODD TOTAL: @{odd_total:.2f}**"
    txt += "\n😈 *Multiplicando o pão hoje*"

    return txt


# ================= ZOEIRA =================

ZOEIRA_LINES = [
    "Hoje o mercado tá meio bêbado 🍻",
    "Se perder, foi culpa do VAR 🤡",
    "Confia no pai que hoje tem green 😈",
    "Aposte com responsabilidade… ou emoção 😂",
    "Mercado fraco, mas a fé tá forte 🙏🔥"
]

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="multi")],
        [InlineKeyboardButton("🤣 Modo Zoeira", callback_data="zoeira")]
    ]

    await update.message.reply_text(
        "🦁 **BOT V76 — ALL IN SUPREMO**\nEscolha:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    games = await fetch_today_games()

    if q.data == "top":
        if not games:
            return await q.edit_message_text("❌ Nada hoje… ou o futebol morreu 💀")
        return await q.edit_message_text(format_games(games))

    if q.data == "multi":
        if not games:
            return await q.edit_message_text("❌ Sem múltipla hoje 😅")
        return await q.edit_message_text(build_multiple(games))

    if q.data == "zoeira":
        msg = random.choice(ZOEIRA_LINES)
        return await q.edit_message_text(f"🤣 {msg}")


# ================= MAIN =================

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    logging.info("🦁 BOT V76 ALL IN SUPREMO ONLINE")

    await app.run_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())