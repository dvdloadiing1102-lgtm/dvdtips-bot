import os
import httpx
import random
import logging
import feedparser
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIG =================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
NBA_API_KEY = os.getenv("NBA_API_KEY")

logging.basicConfig(level=logging.INFO)

# ================= POSTAR NO CANAL =================

async def postar_canal(texto):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": texto,
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

# ================= FUTEBOL REAL =================

async def futebol_hoje():
    hoje = datetime.now().strftime("%Y-%m-%d")
    url = f"https://v3.football.api-sports.io/fixtures?date={hoje}"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        data = r.json()

    jogos = []
    for j in data.get("response", []):
        jogos.append({
            "home": j["teams"]["home"]["name"],
            "away": j["teams"]["away"]["name"]
        })
    return jogos

# ================= NBA REAL =================

async def nba_hoje():
    hoje = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api-nba-v1.p.rapidapi.com/games?date={hoje}"
    headers = {
        "X-RapidAPI-Key": NBA_API_KEY,
        "X-RapidAPI-Host": "api-nba-v1.p.rapidapi.com"
    }

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        data = r.json()

    jogos = []
    for g in data.get("response", []):
        jogos.append({
            "home": g["teams"]["home"]["name"],
            "away": g["teams"]["visitors"]["name"]
        })
    return jogos

# ================= PICKS DO DIA =================

async def gerar_picks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fut = await futebol_hoje()
    nba = await nba_hoje()

    fut = fut[:8]
    nba = nba[:3]

    if not fut and not nba:
        await update.callback_query.message.reply_text("⚠️ Nenhum jogo real encontrado hoje.")
        return

    texto = "🔥 *PICKS DO DIA — REAL*\n\n"

    for j in fut:
        texto += f"⚽ {j['home']} x {j['away']} — *Over 1.5*\n"

    for j in nba:
        texto += f"🏀 {j['home']} x {j['away']} — *ML*\n"

    texto += "\n📊 Total: 10 jogos\n📈 Stake: Moderada"

    await postar_canal(texto)
    await update.callback_query.message.reply_text("✅ Picks postadas no canal!")

# ================= MÚLTIPLA ODD 20+ =================

async def multipla_20(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jogos = await futebol_hoje()
    jogos = jogos[:7]

    if not jogos:
        await update.callback_query.message.reply_text("⚠️ Nenhum jogo disponível.")
        return

    texto = "💣 *MÚLTIPLA INSANA — ODD 20+*\n\n"
    odd_total = 1

    for j in jogos:
        odd = round(random.uniform(1.7, 2.3), 2)
        odd_total *= odd
        texto += f"⚽ {j['home']} vence — Odd {odd}\n"

    texto += f"\n🎯 *Odd total:* {round(odd_total, 2)}\n💰 Stake: Baixa"

    await postar_canal(texto)
    await update.callback_query.message.reply_text("💥 Múltipla postada!")

# ================= ALL IN SUPREMO =================

async def all_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jogos = await futebol_hoje()

    if not jogos:
        await update.callback_query.message.reply_text("⚠️ Sem jogos confiáveis hoje.")
        return

    j = random.choice(jogos)

    texto = f"""🔥 *ALL IN SUPREMO*

⚽ {j['home']} x {j['away']}
🎯 Entrada: *Casa vence*
💰 Stake: ALTA
⚠️ Gestão ativa"""

    await postar_canal(texto)
    await update.callback_query.message.reply_text("🔥 ALL IN postado!")

# ================= NOTÍCIAS FUTEBOL =================

async def noticias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    feed = feedparser.parse("https://ge.globo.com/rss/futebol/")
    texto = "📰 *NOTÍCIAS DO FUTEBOL*\n\n"

    for n in feed.entries[:4]:
        texto += f"🔥 {n.title}\n🔗 {n.link}\n\n"

    await postar_canal(texto)
    await update.callback_query.message.reply_text("📰 Notícias postadas!")

# ================= ROI =================

ROI_DATA = {"wins": 0, "loss": 0}

async def roi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = ROI_DATA["wins"] + ROI_DATA["loss"]
    taxa = (ROI_DATA["wins"] / total * 100) if total > 0 else 0

    texto = f"""📊 *ROI DO BOT*

✅ Wins: {ROI_DATA['wins']}
❌ Loss: {ROI_DATA['loss']}
📈 Taxa: {round(taxa,1)}%"""

    await update.callback_query.message.reply_text(texto)

# ================= MENU =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teclado = [
        [InlineKeyboardButton("🔥 PICKS DO DIA", callback_data="picks")],
        [InlineKeyboardButton("💣 MÚLTIPLA ODD 20+", callback_data="multipla")],
        [InlineKeyboardButton("⚽ ALL IN SUPREMO", callback_data="allin")],
        [InlineKeyboardButton("📰 NOTÍCIAS FUTEBOL", callback_data="noticias")],
        [InlineKeyboardButton("📊 ROI", callback_data="roi")]
    ]

    await update.message.reply_text(
        "🤖 *BOT ELITE ATIVO*\nEscolha uma opção:",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown"
    )

# ================= CALLBACK =================

async def botoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "picks":
        await gerar_picks(update, context)

    elif query.data == "multipla":
        await multipla_20(update, context)

    elif query.data == "allin":
        await all_in(update, context)

    elif query.data == "noticias":
        await noticias(update, context)

    elif query.data == "roi":
        await roi(update, context)

# ================= MAIN =================

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(botoes))

    print("🔥 BOT ELITE ONLINE")
    app.run_polling()