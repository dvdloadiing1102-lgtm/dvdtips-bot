import logging
import requests
import datetime
import asyncio
import random
import os
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configuração de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- CONFIGURAÇÕES ---
TELEGRAM_TOKEN = "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4"
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# --- LISTAS DE LIGAS ---
SOCCER_LEAGUES = [
    'soccer_epl', 'soccer_brazil_campeonato', 'soccer_spain_la_liga', 
    'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_uefa_champs_league'
]
BASKETBALL_LEAGUES = ['basketball_nba']
MAJOR_LEAGUES = SOCCER_LEAGUES + BASKETBALL_LEAGUES

# --- LISTAS DE JOGADORES ---
NBA_PLAYERS = ["LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo", "Luka Doncic", "Jayson Tatum", "Joel Embiid", "Nikola Jokic"]
SOCCER_PLAYERS = ["Vinícius Jr", "Mbappé", "Haaland", "Bellingham", "Harry Kane", "Salah", "Lewandowski", "Lautaro Martínez"]

# --- FUNÇÕES DE API ---
def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json()
        return []
    except Exception as e:
        logging.error(f"Erro na API: {e}")
        return []

def select_bets(odds_data):
    """Separa bets de Futebol e NBA"""
    soccer_bets = []
    nba_bets = []
    
    for event in odds_data:
        if not event.get('bookmakers'): continue
        
        outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
        selected_outcome = None
        for outcome in outcomes:
            if 1.3 <= outcome['price'] <= 2.8:
                selected_outcome = outcome
                break
        
        if selected_outcome:
            bet = {
                'match': f"{event['home_team']} vs {event['away_team']}",
                'selection': selected_outcome['name'],
                'odd': selected_outcome['price'],
                'sport': event['sport_key']
            }
            if 'basketball' in event['sport_key']:
                nba_bets.append(bet)
            else:
                soccer_bets.append(bet)
    return soccer_bets, nba_bets

async def create_tip_message():
    all_events = []
    for league in MAJOR_LEAGUES:
        all_events.extend(get_odds(league))
    
    soccer_bets, nba_bets = select_bets(all_events)
    
    if (len(soccer_bets) + len(nba_bets)) < 3:
        return "⚠️ <b>Aviso:</b> Poucos jogos encontrados na API hoje. Tente mais tarde!", None

    header = (
        "🏆 <b>DVD TIPS - ELITE DOS 20 BILHETES</b> 🏆\n"
        f"📅 {datetime.datetime.now().strftime('%d/%m/%Y')} | 📍 Futebol & NBA\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )
    
    body = ""
    pool_soccer = soccer_bets * 10 
    pool_nba = nba_bets * 10
    random.shuffle(pool_soccer)
    random.shuffle(pool_nba)

    for i in range(1, 21):
        bet = None
        tip_text = ""
        cat_name = ""
        
        # Logica de Slots
        if 12 <= i <= 15 and len(pool_nba) > 0:
            bet = pool_nba.pop(0)
            player = random.choice(NBA_PLAYERS)
            prop = random.choice(["Pontos", "Rebotes", "Assistências"])
            val = random.randint(15, 28)
            cat_name = "🏀 NBA PLAYER PROPS"
            tip_text = f"🎯 {player}: +{val}.5 {prop}"
        elif len(pool_soccer) > 0:
            bet = pool_soccer.pop(0)
            if i <= 3:
                cat_name = "🛡️ BILHETE SEGURO"
                tip_text = f"🎯 {bet['selection']} ML"
            elif 4 <= i <= 7:
                cat_name = "⚽ ESCANTEIOS"
                line = random.choice(["8.5", "9.5", "10.5"])
                tip_text = f"🎯 Mais de {line} Cantos"
            elif 8 <= i <= 11:
                cat_name = "🟨 CARTÕES"
                line = random.choice(["3.5", "4.5"])
                tip_text = f"🎯 Mais de {line} Cartões"
            elif 16 <= i <= 19:
                cat_name = "🎯 FINALIZAÇÕES"
                player = random.choice(SOCCER_PLAYERS)
                tip_text = f"🎯 {player}: +1.5 Chutes ao Gol"
            else:
                cat_name = "💎 MISTO VALOR"
                tip_text = f"🎯 {bet['selection']}"
        
        if not bet: continue

        body += f"{i}️⃣ <b>{cat_name}</b>\n🏟️ {bet['match']}\n{tip_text} | ODD: {bet['odd']:.2f}\n"
        
        if i == 20:
            jackpot_odd = random.uniform(25.0, 30.0)
            body += f"──────────────────\n🔥 <b>BILHETE JACKPOT SUPREMO</b> 🔥\n📈 <b>ODD TOTAL: {jackpot_odd:.2f}</b>\n💰 Stake: 0.2u (Lucro Alto)\n"
        else:
            body += "──────────────────\n"

    footer = "\n🚀 <b>Aposte agora nos links abaixo:</b>"
    keyboard = [[InlineKeyboardButton("📲 APOSTAR NA BETANO", url="https://www.betano.bet.br")], [InlineKeyboardButton("📲 APOSTAR NA BET365", url="https://www.bet365.com")]]
    
    return header + body + footer, InlineKeyboardMarkup(keyboard)

# --- COMANDOS DO BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 <b>BOT ATIVO!</b>\n\n/postar - Postar manualmente\n/cota - Ver status da API\n/jogo [Time] - Buscar jogo", parse_mode='HTML')

async def verificar_cota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica quanto falta da API gratuita"""
    url = f"https://api.the-odds-api.com/v4/sports/?apiKey={ODDS_API_KEY}"
    try:
        response = requests.get(url)
        headers = response.headers
        remaining = headers.get('x-requests-remaining', 'N/A')
        used = headers.get('x-requests-used', 'N/A')
        await update.message.reply_text(f"📊 <b>STATUS API</b>\n✅ Restante: {remaining}\n❌ Usado: {used}", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"Erro: {e}")

async def buscar_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Busca odds de um time específico"""
    if not context.args:
        await update.message.reply_text("❌ Use: `/jogo Flamengo`", parse_mode='Markdown')
        return

    team_name = " ".join(context.args).lower()
    await update.message.reply_text(f"🔍 Buscando: <b>{team_name}</b>...", parse_mode='HTML')
    
    found = False
    for league in MAJOR_LEAGUES:
        odds = get_odds(league)
        for event in odds:
            home = event['home_team']
            away = event['away_team']
            if team_name in home.lower() or team_name in away.lower():
                try:
                    price = event['bookmakers'][0]['markets'][0]['outcomes'][0]['price']
                    await update.message.reply_text(f"✅ <b>ACHEI!</b>\n🏟️ {home} x {away}\n💰 Odd Principal: {price}", parse_mode='HTML')
                    found = True
                    break
                except: continue
        if found: break
    
    if not found:
        await update.message.reply_text("❌ Nenhum jogo encontrado hoje para esse time.")

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Gerando bilhetes...")
    try:
        text, reply_markup = await create_tip_message()
        if reply_markup:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="✅ Postado!")
        else:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=text, parse_mode='HTML')
    except Exception as e:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"❌ Erro: {e}")

# --- AGENDAMENTO AUTOMÁTICO ---
async def job_postagem_automatica(app: Application):
    """Função que o agendador chama para postar sem comando"""
    logging.info("⏰ Iniciando postagem automática...")
    try:
        text, reply_markup = await create_tip_message()
        if reply_markup:
            await app.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
            logging.info("✅ Postagem automática enviada!")
    except Exception as e:
        logging.error(f"❌ Erro na postagem automática: {e}")

async def iniciar_agendamento(application):
    scheduler = AsyncIOScheduler()
    tz = pytz.timezone('America/Sao_Paulo')
    # Configurado para 11:00 da manhã. Mude hour=11 se quiser outra hora.
    scheduler.add_job(job_postagem_automatica, 'cron', hour=11, minute=0, timezone=tz, args=[application])
    scheduler.start()
    logging.info("⏰ Agendador iniciado para 11:00 (Brasília)")

if __name__ == '__main__':
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Adiciona Comandos
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    application.add_handler(CommandHandler('cota', verificar_cota))
    application.add_handler(CommandHandler('jogo', buscar_jogo))
    
    # Inicia o agendador antes do loop
    loop = asyncio.get_event_loop()
    loop.run_until_complete(iniciar_agendamento(application))
    
    print("🤖 Bot rodando...")
    application.run_polling()
