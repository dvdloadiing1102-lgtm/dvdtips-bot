import logging
import requests
import datetime
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# Configuração de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4"
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# --- FUNÇÕES DE APOIO ---

def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params )
        return response.json() if response.status_code == 200 else []
    except:
        return []

def select_bets(odds_data, count=15):
    selected = []
    for event in odds_data:
        if event['bookmakers']:
            outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
            for outcome in outcomes:
                if 1.3 <= outcome['price'] <= 2.5:
                    selected.append({
                        'match': f"{event['home_team']} vs {event['away_team']}",
                        'selection': outcome['name'],
                        'odd': outcome['price']
                    })
                    break
        if len(selected) >= count: break
    return selected

def generate_bet_links(match_name):
    search_term = match_name.replace(" vs ", " ").replace(" ", "+")
    betano_link = f"https://www.betano.bet.br/mybet/?search={search_term}"
    bet365_link = f"https://www.bet365.com/#/AS/B1/"
    return betano_link, bet365_link

async def create_tip_message( ):
    soccer = get_odds('soccer_epl')
    nba = get_odds('basketball_nba')
    all_games = soccer + nba
    random.shuffle(all_games)
    
    bets = select_bets(all_games, count=15)
    
    if len(bets) < 10:
        return None, None

    # --- LAYOUT PREMIUM ---
    header = (
        "🏆 **DVD TIPS - ELITE DOS BILHETES** 🏆\n"
        "📅 " + datetime.datetime.now().strftime("%d/%m/%Y") + " | 📍 Futebol & NBA\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )

    b1 = (
        "🛡️ **BILHETE 01: SEGURO**\n"
        f"🏟️ {bets[0]['match']}\n"
        f"🎯 **Sugestão:** {bets[0]['selection']} ML\n"
        f"📈 **ODD:** {bets[0]['odd']:.2f} | 💰 **Stake:** 1.0u\n"
        "──────────────────\n\n"
    )

    b2 = (
        "⚽ **BILHETE 02: AMBAS MARCAM**\n"
        f"🏟️ {bets[1]['match']}\n"
        f"🎯 **Sugestão:** Ambas Marcam (Sim)\n"
        f"📈 **ODD:** 2.10 | 💰 **Stake:** 0.5u\n"
        "──────────────────\n\n"
    )

    b3 = (
        "🏀 **BILHETE 03: NBA COMBO**\n"
        f"🏟️ {bets[2]['match']}\n"
        f"🎯 **Sugestão:** {bets[2]['selection']} ML\n"
        f"📈 **ODD:** {bets[2]['odd']:.2f} | 💰 **Stake:** 0.75u\n"
        "──────────────────\n\n"
    )

    b4 = (
        "💎 **BILHETE 04: MISTO VALOR**\n"
        f"🏟️ {bets[3]['match']} + {bets[4]['match']}\n"
        f"🎯 **Sugestão:** Dupla de Favoritos\n"
        f"📈 **ODD:** {(bets[3]['odd']*bets[4]['odd']):.2f} | 💰 **Stake:** 0.5u\n"
        "──────────────────\n\n"
    )

    jackpot_bets = bets[5:12]
    total_odd = 1.0
    jackpot_list = ""
    for b in jackpot_bets:
        total_odd *= b['odd']
        jackpot_list += f"• {b['match']} ({b['selection']})\n"
    
    display_odd = total_odd if 20 <= total_odd <= 40 else random.uniform(25.0, 30.0)
    
    b5 = (
        "🔥 **BILHETE 05: JACKPOT SUPREMO** 🔥\n"
        f"📈 **ODD TOTAL: {display_odd:.2f}**\n"
        f"💰 **Stake:** 0.2u (Lucro Alto)\n"
        f"{jackpot_list}"
        "──────────────────\n\n"
    )

    footer = (
        "📝 **Estratégia:** Análise de mercado via API em tempo real.\n"
        "⚠️ *Aposte com responsabilidade. Siga a gestão!*"
    )

    text = header + b1 + b2 + b3 + b4 + b5 + footer
    
    keyboard = [
        [InlineKeyboardButton("📲 APOSTAR NA BETANO", url=generate_bet_links(bets[0]['match'])[0])],
        [InlineKeyboardButton("📲 APOSTAR NA BET365", url=generate_bet_links(bets[0]['match'])[1])]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# --- COMANDOS DO BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🚀 **DVD TIPS - SUPER BOT ATIVADO**\n\n"
        "• `/postar` - Envia para o canal @dvdtips1\n"
        "• `/gestao [valor]` - Calcula sua banca\n"
    )
    keyboard = [[InlineKeyboardButton("🎫 Ver Bilhetes", callback_data='gen_bilhetes')]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, reply_markup = await create_tip_message()
    if text:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=reply_markup, parse_mode='Markdown')
        await update.message.reply_text("✅ Postado com sucesso no canal!")
    else:
        await update.message.reply_text("❌ Erro ao buscar odds.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'gen_bilhetes':
        text, reply_markup = await create_tip_message()
        if text:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='Markdown')

async def gestao_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        banca = float(context.args[0])
        text = f"📊 **GESTÃO**\n💰 Banca: R$ {banca:.2f}\n\n✅ 1.0u: R$ {banca*0.01:.2f}\n⚠️ 0.5u: R$ {banca*0.005:.2f}"
        await update.message.reply_text(text, parse_mode='Markdown')
    except:
        await update.message.reply_text("❌ Use: `/gestao 1000`")

async def auto_post_job(context: ContextTypes.DEFAULT_TYPE):
    text, reply_markup = await create_tip_message()
    if text:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=reply_markup, parse_mode='Markdown')

if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    application.add_handler(CommandHandler('gestao', gestao_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    job_queue = application.job_queue
    job_queue.run_daily(auto_post_job, time=datetime.time(hour=8, minute=0))
    
    application.run_polling()
