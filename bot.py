import logging
import requests
import datetime
import asyncio
import random
import os
import pytz
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIGURAÇÃO DE LOGS ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- CONFIGURAÇÕES E TOKENS ---
TELEGRAM_TOKEN = "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4"
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# --- LISTAS ---
SOCCER_LEAGUES = ['soccer_epl', 'soccer_brazil_campeonato', 'soccer_spain_la_liga', 'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_uefa_champs_league']
BASKETBALL_LEAGUES = ['basketball_nba']
MAJOR_LEAGUES = SOCCER_LEAGUES + BASKETBALL_LEAGUES
NBA_PLAYERS = ["LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo", "Luka Doncic", "Jayson Tatum", "Joel Embiid", "Nikola Jokic"]
SOCCER_PLAYERS = ["Vinícius Jr", "Mbappé", "Haaland", "Bellingham", "Harry Kane", "Salah", "Lewandowski", "Lautaro Martínez"]

# --- SERVIDOR FALSO PARA O RENDER (MANTÉM O BOT VIVO) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot DVD TIPS Online! 🚀"

def run_flask():
    # Pega a porta que o Render oferece ou usa a 10000 padrão
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- FUNÇÕES DO BOT ---
def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else []
    except: return []

def select_bets(odds_data):
    soccer_bets, nba_bets = [], []
    for event in odds_data:
        if not event.get('bookmakers'): continue
        outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
        selected = None
        for o in outcomes:
            if 1.3 <= o['price'] <= 2.8:
                selected = o
                break
        if selected:
            bet = {'match': f"{event['home_team']} x {event['away_team']}", 'selection': selected['name'], 'odd': selected['price'], 'sport': event['sport_key']}
            if 'basketball' in event['sport_key']: nba_bets.append(bet)
            else: soccer_bets.append(bet)
    return soccer_bets, nba_bets

async def create_tip_message():
    all_events = []
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    soccer, nba = select_bets(all_events)
    
    if (len(soccer) + len(nba)) < 3: return "⚠️ Poucos jogos hoje.", None

    pool_soccer, pool_nba = soccer * 10, nba * 10
    random.shuffle(pool_soccer); random.shuffle(pool_nba)
    
    body = f"🏆 <b>DVD TIPS - ELITE</b> 🏆\n📅 {datetime.datetime.now().strftime('%d/%m/%Y')}\n\n"
    
    for i in range(1, 21):
        bet, cat, text = None, "", ""
        if 12 <= i <= 15 and pool_nba:
            bet = pool_nba.pop(0)
            cat = "🏀 NBA PROPS"
            text = f"🎯 {random.choice(NBA_PLAYERS)}: +{random.randint(15,28)}.5 Pontos"
        elif pool_soccer:
            bet = pool_soccer.pop(0)
            if i <= 3: cat, text = "🛡️ SEGURO", f"🎯 {bet['selection']} ML"
            elif 4 <= i <= 7: cat, text = "⚽ CANTOS", f"🎯 +{random.choice(['8.5','9.5'])} Cantos"
            elif 8 <= i <= 11: cat, text = "🟨 CARTÕES", "🎯 +3.5 Cartões"
            else: cat, text = "💎 VALOR", f"🎯 {bet['selection']}"
        
        if not bet: continue
        body += f"{i}️⃣ <b>{cat}</b>\n🏟️ {bet['match']}\n{text} | ODD: {bet['odd']:.2f}\n──────────────────\n"
        if i == 20: body += f"\n🔥 <b>JACKPOT: {random.uniform(25,30):.2f}</b>\n"

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("APOSTAR AGORA", url="https://www.bet365.com")]])
    return body + "\n🚀 <b>Boa sorte!</b>", markup

# --- COMANDOS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot Online! Use /postar")

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Processando...")
    try:
        text, markup = await create_tip_message()
        if markup:
            await context.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
            await msg.edit_text("✅ Postado!")
        else: await msg.edit_text(text)
    except Exception as e: await msg.edit_text(f"Erro: {e}")

async def auto_post(app):
    text, markup = await create_tip_message()
    if markup: await app.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML')

async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_post, 'cron', hour=11, minute=0, timezone=pytz.timezone('America/Sao_Paulo'), args=[application])
    scheduler.start()

if __name__ == '__main__':
    # 1. Inicia o servidor Web falso em outra thread para o Render não reclamar
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Inicia o Bot
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    
    print("🤖 Bot e Servidor Web rodando...")
    application.run_polling()
