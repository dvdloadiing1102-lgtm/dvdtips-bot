import logging
import requests
import datetime
import asyncio
import random
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, Application

# Configuração de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4"
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# --- LISTAS DE JOGADORES PARA PROPS ---
NBA_PLAYERS = ["LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo", "Luka Doncic", "Jayson Tatum", "Joel Embiid", "Nikola Jokic", "Anthony Davis", "Kyrie Irving"]
SOCCER_PLAYERS = ["Vinícius Jr", "Mbappé", "Haaland", "Bellingham", "Harry Kane", "Salah", "Lewandowski", "Rodrygo", "De Bruyne", "Lautaro Martínez"]

# --- FUNÇÕES DE APOIO ---
def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params )
        return response.json() if response.status_code == 200 else []
    except: return []

def select_bets(odds_data, count=25):
    selected = []
    for event in odds_data:
        if event.get('bookmakers'):
            outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
            for outcome in outcomes:
                if 1.3 <= outcome['price'] <= 2.5:
                    selected.append({
                        'id': event['id'],
                        'match': f"{event['home_team']} vs {event['away_team']}",
                        'selection': outcome['name'],
                        'odd': outcome['price'],
                        'sport': event['sport_key']
                    })
                    break
        if len(selected) >= count: break
    return selected

async def create_tip_message():
    soccer = get_odds('soccer_epl') + get_odds('soccer_brazil_campeonato')
    nba = get_odds('basketball_nba')
    all_games = soccer + nba
    random.shuffle(all_games)
    
    bets = select_bets(all_games, count=25)
    if len(bets) < 15: return None, None

    header = (
        "🏆 **DVD TIPS - ELITE DOS 20 BILHETES** 🏆\n"
        f"📅 {datetime.datetime.now().strftime('%d/%m/%Y')} | 📍 Futebol & NBA\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )
    
    body = ""
    for i in range(1, 21):
        b = bets[i-1]
        if i <= 3: # SEGURO
            body += f"{i}️⃣ **🛡️ BILHETE SEGURO**\n🏟️ {b['match']}\n🎯 {b['selection']} ML | ODD: {b['odd']:.2f}\n"
        elif 4 <= i <= 7: # ESCANTEIOS
            line = random.choice(["8.5", "9.5", "10.5"])
            body += f"{i}️⃣ **⚽ ESCANTEIOS**\n🏟️ {b['match']}\n🎯 Mais de {line} Cantos | ODD: {random.uniform(1.7, 2.1):.2f}\n"
        elif 8 <= i <= 11: # CARTÕES
            line = random.choice(["3.5", "4.5", "5.5"])
            body += f"{i}️⃣ **🟨 CARTÕES**\n🏟️ {b['match']}\n🎯 Mais de {line} Cartões | ODD: {random.uniform(1.8, 2.3):.2f}\n"
        elif 12 <= i <= 15: # NBA PROPS
            player = random.choice(NBA_PLAYERS)
            prop = random.choice(["Pontos", "Rebotes", "Assistências"])
            val = random.randint(5, 28)
            body += f"{i}️⃣ **🏀 NBA PLAYER PROPS**\n🏟️ {b['match']}\n🎯 {player}: +{val}.5 {prop} | ODD: {random.uniform(1.8, 2.0):.2f}\n"
        elif 16 <= i <= 19: # JOGADORES FUTEBOL
            player = random.choice(SOCCER_PLAYERS)
            body += f"{i}️⃣ **🎯 FINALIZAÇÕES**\n🏟️ {b['match']}\n🎯 {player}: +1.5 Chutes ao Gol | ODD: {random.uniform(1.7, 2.2):.2f}\n"
        elif i == 20: # JACKPOT 25-30
            jackpot_odd = random.uniform(25.0, 30.0)
            body += f"{i}️⃣ **🔥 BILHETE JACKPOT SUPREMO** 🔥\n📈 **ODD TOTAL: {jackpot_odd:.2f}**\n💰 Stake: 0.2u (Lucro Alto)\n"
            for j in range(5):
                body += f"• {bets[j+15]['match']} ({bets[j+15]['selection']})\n"
        
        body += "──────────────────\n"

    footer = "\n🚀 **Aposte agora nos links abaixo:**"
    keyboard = [[InlineKeyboardButton("📲 APOSTAR NA BETANO", url="https://www.betano.bet.br" ), InlineKeyboardButton("📲 APOSTAR NA BET365", url="https://www.bet365.com" )]]
    
    return header + body + footer, InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 **DVD TIPS UPGRADE ATIVADO!**\n\nUse `/postar` para enviar os 20 bilhetes.")

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, reply_markup = await create_tip_message()
    if text:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=reply_markup, parse_mode='Markdown')
        await update.message.reply_text("✅ 20 Bilhetes postados no canal!")

if __name__ == '__main__':
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    application.run_polling()
