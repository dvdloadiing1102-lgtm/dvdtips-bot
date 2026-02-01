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
BETS_FILE = "active_bets.json"

# --- FUNÇÕES DE APOIO ---
def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params )
        return response.json() if response.status_code == 200 else []
    except: return []

def get_scores(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
    params = {'apiKey': ODDS_API_KEY, 'daysFrom': 1}
    try:
        response = requests.get(url, params=params )
        return response.json() if response.status_code == 200 else []
    except: return []

def save_bets(bets):
    try:
        with open(BETS_FILE, 'w') as f:
            json.dump(bets, f)
    except: pass

def load_bets():
    if os.path.exists(BETS_FILE):
        try:
            with open(BETS_FILE, 'r') as f:
                return json.load(f)
        except: return []
    return []

def select_bets(odds_data, count=20):
    selected = []
    for event in odds_data:
        if event.get('bookmakers'):
            outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
            for outcome in outcomes:
                if 1.3 <= outcome['price'] <= 3.0:
                    selected.append({
                        'id': event['id'],
                        'match': f"{event['home_team']} vs {event['away_team']}",
                        'selection': outcome['name'],
                        'odd': outcome['price'],
                        'sport': event['sport_key'],
                        'home_team': event['home_team'],
                        'away_team': event['away_team']
                    })
                    break
        if len(selected) >= count: break
    return selected

async def create_tip_message():
    soccer = get_odds('soccer_epl') + get_odds('soccer_brazil_campeonato')
    nba = get_odds('basketball_nba')
    all_games = soccer + nba
    random.shuffle(all_games)
    bets = select_bets(all_games, count=20)
    if len(bets) < 10: return None, None
    save_bets(bets)
    header = f"🏆 **DVD TIPS - ELITE DOS 20 BILHETES** 🏆\n📅 {datetime.datetime.now().strftime('%d/%m/%Y')}\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    body = ""
    categories = ["🛡️ SEGURO", "⚽ ESCANTEIOS", "🟨 CARTÕES", "🏀 NBA PROPS", "💎 MISTO VALOR", "🔥 JACKPOT SUPREMO"]
    for i, b in enumerate(bets, 1):
        cat = categories[(i-1) % len(categories)]
        body += f"{i}️⃣ **{cat}**\n🏟️ {b['match']}\n🎯 {b['selection']} | ODD: {b['odd']:.2f}\n──────────────────\n"
    footer = "\n🚀 **Aposte agora nos links abaixo:**"
    keyboard = [[InlineKeyboardButton("📲 APOSTAR NA BETANO", url="https://www.betano.bet.br" ), InlineKeyboardButton("📲 APOSTAR NA BET365", url="https://www.bet365.com" )]]
    return header + body + footer, InlineKeyboardMarkup(keyboard)

# --- COMANDOS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 **DVD TIPS UPGRADE ATIVADO!**\n\nUse `/postar` para enviar os 20 bilhetes.")

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, reply_markup = await create_tip_message()
    if text:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=reply_markup, parse_mode='Markdown')
        await update.message.reply_text("✅ Postado com sucesso!")

async def check_results(context: ContextTypes.DEFAULT_TYPE):
    active_bets = load_bets()
    if not active_bets: return
    scores = get_scores('soccer_epl') + get_scores('soccer_brazil_campeonato') + get_scores('basketball_nba')
    results_summary = "📊 **RESUMO DE RESULTADOS**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    greens, reds = 0, 0
    for bet in active_bets:
        for score in scores:
            if bet['id'] == score['id'] and score['completed']:
                h, a = int(score['scores'][0]['score']), int(score['scores'][1]['score'])
                winner = score['home_team'] if h > a else (score['away_team'] if a > h else "Draw")
                if bet['selection'] == winner:
                    results_summary += f"✅ {bet['match']}: **GREEN**\n"; greens += 1
                else:
                    results_summary += f"❌ {bet['match']}: **RED**\n"; reds += 1
                break
    if greens > 0 or reds > 0:
        msg = "🚀 **SOMOS OS MELHORES!**" if greens >= reds else "👊 **Vamos buscar o próximo!**"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=f"{results_summary}\n{msg}", parse_mode='Markdown')
        save_bets([])

if __name__ == '__main__':
    # Inicialização compatível com Render/Python 3.13
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    if application.job_queue:
        application.job_queue.run_daily(check_results, time=datetime.time(hour=23, minute=0))
    application.run_polling()
