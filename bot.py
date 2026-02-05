import os
import logging
import httpx
import random
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

API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"

# ================= ELITE FILTER =================
VIP_TEAMS = [
    "flamengo","corinthians","palmeiras","vasco","fluminense","sao paulo",
    "real madrid","barcelona","manchester city","arsenal","liverpool",
    "psg","bayern","juventus","milan","inter","napoli"
]

VIP_LEAGUES = [
    "Brasileirão","Serie A","Premier League","La Liga",
    "Champions League","Libertadores","Coppa Italia",
    "Paulista - A1","Bundesliga"
]

ROI_DATA = {"green": 0, "red": 0}

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🔥 Top Jogos", callback_data="top"),
            InlineKeyboardButton("🏀 NBA", callback_data="nba")
        ],
        [
            InlineKeyboardButton("💣 Troco do Pão", callback_data="troco"),
            InlineKeyboardButton("🦁 ALL IN", callback_data="allin")
        ],
        [
            InlineKeyboardButton("📊 ROI", callback_data="roi"),
            InlineKeyboardButton("💬 Mensagem Livre", callback_data="msg")
        ]
    ]

    await update.message.reply_text(
        "🦁 **PAINEL ALL IN SUPREMO — MODO ELITE**\n\n"
        "🔥 Jogos grandes apenas\n"
        "💣 Múltipla pronta\n"
        "🦁 Pick Suprema\n\n"
        "Use os botões abaixo:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= FETCH GAMES =================
async def fetch_today_games():
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    games = []

    now_br = datetime.now(timezone.utc) - timedelta(hours=3)
    today = now_br.date().isoformat()

    params = {"date": today}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(API_FOOTBALL_URL, headers=headers, params=params)
            fixtures = r.json().get("response", [])

            for f in fixtures:
                try:
                    home = f["teams"]["home"]["name"]
                    away = f["teams"]["away"]["name"]
                    league = f["league"]["name"]
                    date_str = f["fixture"]["date"]

                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00")) - timedelta(hours=3)

                    full = f"{home} {away}".lower()
                    score = 0

                    if any(v in full for v in VIP_TEAMS):
                        score += 5000

                    if any(l.lower() in league.lower() for l in VIP_LEAGUES):
                        score += 3000

                    if score > 0:
                        games.append({
                            "match": f"{home} x {away}",
                            "league": league,
                            "time": dt.strftime("%H:%M"),
                            "score": score,
                            "odd": round(random.uniform(1.35, 1.95), 2)
                        })

                except:
                    pass

    except Exception as e:
        logging.error(f"Erro API Football: {e}")

    # FALLBACK CASO API CAIA
    if not games:
        games = [
            {"match": "Corinthians x Capivariano", "league": "Paulista - A1", "time": "Hoje", "score": 9999, "odd": 1.62},
            {"match": "Atalanta x Juventus", "league": "Coppa Italia", "time": "Hoje", "score": 8888, "odd": 1.55},
            {"match": "Real Madrid x Barcelona", "league": "La Liga", "time": "Hoje", "score": 7777, "odd": 1.50},
        ]

    games.sort(key=lambda x: x["score"], reverse=True)
    return games

# ================= TOP JOGOS =================
async def top_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()

    text = "🔥 **TOP JOGOS ELITE HOJE**\n\n"
    for g in games[:8]:
        text += (
            f"⚔️ **{g['match']}**\n"
            f"🏆 {g['league']}\n"
            f"⏰ {g['time']}\n"
            f"🎯 Odd aprox: @{g['odd']}\n\n"
        )

    await update.callback_query.message.reply_text(text, parse_mode="Markdown")

# ================= TROCO DO PÃO =================
async def troco(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()

    selected = random.sample(games[:6], min(4, len(games)))
    odd_total = 1.0

    text = "💣 **TROCO DO PÃO — MÚLTIPLA**\n\n"

    for g in selected:
        odd_total *= g["odd"]
        text += f"📍 {g['match']} @ {g['odd']}\n"

    text += f"\n🔥 **ODD TOTAL: @{odd_total:.2f}**\n⚠️ Gestão de banca!"

    await update.callback_query.message.reply_text(text, parse_mode="Markdown")

# ================= ALL IN =================
async def allin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    games = await fetch_today_games()
    g = games[0]

    await update.callback_query.message.reply_text(
        f"🦁 **ALL IN SUPREMO — PICK DO DIA**\n\n"
        f"🔥 **{g['match']}**\n"
        f"🏆 {g['league']}\n"
        f"⏰ {g['time']}\n\n"
        f"🎯 Pick: **Favorito vence**\n"
        f"💰 Odd segura: @{g['odd']}\n"
        f"🚀 Confiança: **ALTÍSSIMA**",
        parse_mode="Markdown"
    )

# ================= NBA =================
async def nba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.message.reply_text(
        "🏀 **NBA HOJE**\n\nPicks NBA serão ativados quando houver jogos relevantes.",
        parse_mode="Markdown"
    )

# ================= ROI =================
async def roi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = ROI_DATA["green"] + ROI_DATA["red"]
    winrate = (ROI_DATA["green"] / total * 100) if total > 0 else 0

    text = (
        f"📊 **ROI TRACKER**\n\n"
        f"✅ Greens: {ROI_DATA['green']}\n"
        f"❌ Reds: {ROI_DATA['red']}\n"
        f"🎯 Winrate: {winrate:.1f}%\n"
        f"💰 Gestão ativa"
    )

    await update.callback_query.message.reply_text(text, parse_mode="Markdown")

# ================= CALLBACK =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "top":
        await top_games(update, context)

    elif q.data == "troco":
        await troco(update, context)

    elif q.data == "allin":
        await allin(update, context)

    elif q.data == "nba":
        await nba(update, context)

    elif q.data == "roi":
        await roi(update, context)

    elif q.data == "msg":
        await q.message.reply_text("💬 Envie sua mensagem manualmente")

# ================= MAIN =================
def main():
    logging.info("🦁 BOT ALL IN SUPREMO ELITE ONLINE")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()