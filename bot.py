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
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SUAS CHAVES ---
TELEGRAM_TOKEN = "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4"
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# --- LISTAS ---
# Adicionei mais ligas para garantir volume
SOCCER_LEAGUES = [
    'soccer_epl', 'soccer_brazil_campeonato', 'soccer_spain_la_liga', 
    'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_uefa_champs_league',
    'soccer_france_ligue_one', 'soccer_portugal_primeira_liga', 'soccer_netherlands_eredivisie'
]
BASKETBALL_LEAGUES = ['basketball_nba']
MAJOR_LEAGUES = SOCCER_LEAGUES + BASKETBALL_LEAGUES

NBA_PLAYERS = ["LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo", "Luka Doncic", "Jayson Tatum", "Joel Embiid", "Nikola Jokic"]
SOCCER_PLAYERS = ["Vinícius Jr", "Mbappé", "Haaland", "Bellingham", "Harry Kane", "Salah", "Lewandowski", "Lautaro Martínez"]

# --- SERVIDOR FALSO (RENDER) ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot Online e Filtrado! 🚀"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- FUNÇÕES INTELIGENTES ---

def get_today_str():
    """Retorna a data de HOJE no Brasil (formato YYYY-MM-DD)"""
    tz = pytz.timezone('America/Sao_Paulo')
    return datetime.datetime.now(tz).strftime('%Y-%m-%d')

def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else []
    except: return []

def select_bets(odds_data):
    soccer_bets, nba_bets = [], []
    today_br = get_today_str()
    
    for event in odds_data:
        # --- FILTRO RIGOROSO DE DATA ---
        # A API manda data assim: 2023-10-27T19:00:00Z
        try:
            # Converte string ISO para objeto data
            event_date_utc = datetime.datetime.strptime(event['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
            # Ajusta fuso para Brasil (-3h)
            event_date_br = event_date_utc - datetime.timedelta(hours=3)
            # Transforma em string YYYY-MM-DD
            event_date_str = event_date_br.strftime('%Y-%m-%d')
            
            # SE A DATA DO JOGO NÃO FOR HOJE, PULA O JOGO
            if event_date_str != today_br:
                continue
        except:
            continue # Se der erro na data, ignora o jogo por segurança

        if not event.get('bookmakers'): continue
        
        # Seleciona ODD
        outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
        selected = None
        for o in outcomes:
            if 1.25 <= o['price'] <= 2.90: # Ajustei levemente o range
                selected = o
                break
        
        if selected:
            bet = {
                'match': f"{event['home_team']} x {event['away_team']}",
                'selection': selected['name'],
                'odd': selected['price'],
                'sport': event['sport_key']
            }
            if 'basketball' in event['sport_key']: nba_bets.append(bet)
            else: soccer_bets.append(bet)
            
    return soccer_bets, nba_bets

async def create_tip_message():
    all_events = []
    # Busca em todas as ligas
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    
    # Filtra só os de hoje
    soccer, nba = select_bets(all_events)
    
    if (len(soccer) + len(nba)) < 3: return f"⚠️ <b>Aviso:</b> Encontrei poucos jogos confirmados para HOJE ({get_today_str()}). A API pode estar atualizando.", None

    pool_soccer, pool_nba = soccer * 10, nba * 10
    random.shuffle(pool_soccer); random.shuffle(pool_nba)
    
    body = (
        "🏆 <b>DVD TIPS - ELITE DOS 20 BILHETES</b> 🏆\n"
        f"📅 <b>JOGOS DE HOJE: {datetime.datetime.now(pytz.timezone('America/Sao_Paulo')).strftime('%d/%m/%Y')}</b>\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )
    
    for i in range(1, 21):
        bet, cat, text = None, "", ""
        
        if 12 <= i <= 15 and pool_nba:
            bet = pool_nba.pop(0)
            cat = "🏀 NBA PROPS"
            text = f"🎯 {random.choice(NBA_PLAYERS)}: +{random.randint(15,28)}.5 Pontos"
        elif pool_soccer:
            bet = pool_soccer.pop(0)
            if i <= 3: cat, text = "🛡️ BILHETE SEGURO", f"🎯 {bet['selection']} ML"
            elif 4 <= i <= 7: cat, text = "⚽ ESCANTEIOS", f"🎯 +{random.choice(['8.5','9.5'])} Cantos"
            elif 8 <= i <= 11: cat, text = "🟨 CARTÕES", "🎯 +3.5 Cartões"
            elif 16 <= i <= 19: cat, text = "🎯 FINALIZAÇÕES", f"🎯 {random.choice(SOCCER_PLAYERS)}: +1.5 Chutes ao Gol"
            else: cat, text = "💎 MISTO VALOR", f"🎯 {bet['selection']}"
        
        if not bet: continue
        
        body += f"{i}️⃣ <b>{cat}</b>\n🏟️ {bet['match']}\n{text} | ODD: {bet['odd']:.2f}\n"
        
        if i == 20:
            jackpot_odd = random.uniform(25.0, 30.0)
            body += f"──────────────────\n🔥 <b>BILHETE JACKPOT SUPREMO</b> 🔥\n📈 <b>ODD TOTAL: {jackpot_odd:.2f}</b>\n💰 Stake: 0.2u (Lucro Alto)\n"
        else:
            body += "──────────────────\n"

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📲 APOSTAR NA BETANO", url="https://www.betano.bet.br")], [InlineKeyboardButton("📲 APOSTAR NA BET365", url="https://www.bet365.com")]])
    return body + "\n🚀 <b>Aposte agora nos links abaixo:</b>", markup

# --- COMANDOS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 <b>Bot Online!</b> Filtrando jogos de HOJE (Brasil).", parse_mode='HTML')

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(f"⏳ Buscando jogos de hoje ({get_today_str()})...", parse_mode='HTML')
    try:
        text, markup = await create_tip_message()
        if markup:
            await context.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
            await msg.edit_text("✅ Postado!")
        else: await msg.edit_text(text, parse_mode='HTML')
    except Exception as e: await msg.edit_text(f"Erro: {e}")

async def auto_post(app):
    text, markup = await create_tip_message()
    if markup: await app.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)

async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_post, 'cron', hour=11, minute=0, timezone=pytz.timezone('America/Sao_Paulo'), args=[application])
    scheduler.start()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    print("🤖 Bot Rodando com Filtro BR...")
    application.run_polling()
