# ================= BOT V173 NEWS + ANALYSIS =================
import os
import logging
import asyncio
import httpx
import threading
import unicodedata
import random
from datetime import datetime, timezone, timedelta, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import feedparser
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIG =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# ================= IA =================
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

# ================= STAR PLAYERS =================
STAR_PLAYERS = {
    "ARSENAL": "Bukayo Saka",
    "INTER": "Lautaro Martínez",
    "MILAN": "Rafael Leão",
    "REAL MADRID": "Vinícius Júnior",
    "CITY": "Haaland",
    "LIVERPOOL": "Salah",
    "PSG": "Mbappé",
    "BAYERN": "Harry Kane",
    "NAPOLI": "Osimhen",
    "BARCELONA": "Lewandowski"
}

def normalize(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt.upper()) if unicodedata.category(c) != 'Mn')

def get_star(team):
    t = normalize(team)
    for key in STAR_PLAYERS:
        if key in t:
            return STAR_PLAYERS[key]
    return None

# ================= RSS NEWS =================
NEWS_FEEDS = [
    "https://www.espn.com/espn/rss/soccer/news",
    "https://ge.globo.com/rss/ge/futebol/",
    "https://www.goal.com/feeds/en/news"
]

sent_news = set()

async def summarize_news(title):
    if not model:
        return None
    try:
        prompt = f"Resuma em 1 frase curta e diga impacto nas apostas:\n{title}"
        r = await asyncio.to_thread(model.generate_content, prompt)
        return r.text.strip()
    except:
        return None

async def fetch_news():
    noticias = []

    for url in NEWS_FEEDS:
        feed = feedparser.parse(url)

        for entry in feed.entries[:4]:
            if entry.link in sent_news:
                continue

            resumo = await summarize_news(entry.title)

            if resumo:
                texto = f"📰 <b>{entry.title}</b>\n🧠 {resumo}\n🔗 {entry.link}"
            else:
                texto = f"📰 <b>{entry.title}</b>\n🔗 {entry.link}"

            noticias.append(texto)
            sent_news.add(entry.link)

    return noticias[:5]

# ================= BREAKING NEWS DETECTOR =================
KEYWORDS_IMPORTANTES = [
    "injury", "lesion", "out", "fora", "suspenso",
    "transfer", "transferência", "demitido",
    "banido", "crise", "demissão"
]

def is_breaking(text):
    t = text.lower()
    return any(k in t for k in KEYWORDS_IMPORTANTES)

# ================= ODDS =================
async def fetch_games():
    if not ODDS_KEY:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?regions=eu&markets=h2h&apiKey={ODDS_KEY}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        data = r.json()

    jogos = []
    for g in data[:6]:
        jogos.append({
            "home": g["home_team"],
            "away": g["away_team"],
            "match": f"{g['home_team']} x {g['away_team']}"
        })
    return jogos

async def analyze_game(game):
    star = get_star(game["home"]) or get_star(game["away"])
    if star:
        prop = f"🎯 Player Prop: {star} finalizar no alvo"
    else:
        prop = "📊 Tendência ofensiva e chutes ao gol"

    return f"⚔️ {game['match']}\n{prop}\n🥅 Over 1.5 gols tendência\n"

# ================= SERVER =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ONLINE")

def run_server():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("⚽ Jogos", callback_data="games")]]
    await update.message.reply_text("🤖 BOT V173 ONLINE", reply_markup=InlineKeyboardMarkup(kb))

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "games":
        jogos = await fetch_games()
        for g in jogos:
            msg = await analyze_game(g)
            await context.bot.send_message(CHANNEL_ID, msg)

# ================= JOBS =================
async def send_news(context):
    news = await fetch_news()
    if not news:
        return

    breaking = [n for n in news if is_breaking(n)]

    if breaking:
        msg = "🚨 <b>BREAKING NEWS</b>\n\n" + "\n\n".join(breaking)
    else:
        msg = "📰 <b>NOTÍCIAS DO FUTEBOL</b>\n\n" + "\n\n".join(news)

    await context.bot.send_message(
        CHANNEL_ID,
        msg,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def daily_games(context):
    jogos = await fetch_games()
    for g in jogos:
        msg = await analyze_game(g)
        await context.bot.send_message(CHANNEL_ID, msg)

# ================= MAIN =================
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))

    if app.job_queue:
        tz = timezone(timedelta(hours=-3))

        app.job_queue.run_daily(send_news, time=time(9,0,tzinfo=tz))
        app.job_queue.run_daily(send_news, time=time(15,0,tzinfo=tz))
        app.job_queue.run_daily(send_news, time=time(21,0,tzinfo=tz))

        app.job_queue.run_daily(daily_games, time=time(10,0,tzinfo=tz))

    print("BOT V173 rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()