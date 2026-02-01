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

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- CONFIGURAÇÕES ---
TELEGRAM_TOKEN = "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4"
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# --- LIGAS PREMIUM (Filtrei para sair apenas jogos conhecidos) ---
SOCCER_LEAGUES = [
    'soccer_brazil_campeonato', 'soccer_epl', 'soccer_spain_la_liga', 
    'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_uefa_champs_league',
    'soccer_france_ligue_one', 'soccer_portugal_primeira_liga'
]
BASKETBALL_LEAGUES = ['basketball_nba']
MAJOR_LEAGUES = SOCCER_LEAGUES + BASKETBALL_LEAGUES

# --- SERVIDOR FALSO (Para o Render não desligar) ---
app = Flask(__name__)
@app.route('/')
def home(): return "🤖 Bot DVD TIPS - Operando em Modo Realista!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- FUNÇÕES DE INTELIGÊNCIA ---

def get_brazil_time():
    """Retorna data e hora atuais no Brasil"""
    return datetime.datetime.now(pytz.timezone('America/Sao_Paulo'))

def get_odds(sport_key):
    # Pedimos Odds de Vencedor (h2h) e Totais (Over/Under)
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h,totals', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else []
    except: return []

def process_bets(odds_data):
    """Processa e classifica as melhores apostas reais"""
    bets = []
    now_br = get_brazil_time()
    # Aceitamos jogos das próximas 24h
    limit_time = now_br + datetime.timedelta(hours=24)
    
    for event in odds_data:
        # --- FILTRO DE TEMPO ---
        try:
            # Converte horário da API (UTC) para objeto datetime
            event_time_utc = datetime.datetime.strptime(event['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
            event_time_utc = event_time_utc.replace(tzinfo=pytz.utc) # Avisa que é UTC
            
            # Converte para Brasil
            event_time_br = event_time_utc.astimezone(pytz.timezone('America/Sao_Paulo'))
            
            # Se o jogo já passou ou é daqui a mais de 24h, ignora
            if event_time_br < now_br or event_time_br > limit_time:
                continue
        except: continue

        if not event.get('bookmakers'): continue
        
        match_name = f"{event['home_team']} x {event['away_team']}"
        sport = event['sport_key']
        markets = event['bookmakers'][0]['markets']
        
        for market in markets:
            # 1. QUEM VENCE (Moneyline)
            if market['key'] == 'h2h':
                for outcome in market['outcomes']:
                    odd = outcome['price']
                    # Só pegamos odds que fazem sentido (entre 1.25 e 2.50)
                    if 1.25 <= odd <= 2.50:
                        # Classificação de Risco
                        if odd <= 1.50: cat = "🧱 TIJOLINHO (Segurança)"
                        elif odd <= 1.90: cat = "🧠 BET INTELIGENTE"
                        else: cat = "🔥 OUSADIA (Valor)"
                        
                        bets.append({
                            'match': match_name,
                            'selection': f"Vencer: {outcome['name']}",
                            'odd': odd,
                            'category': cat,
                            'sport': "🏀 BASQUETE" if 'basketball' in sport else "⚽ FUTEBOL"
                        })
            
            # 2. OVER GOLS/PONTOS
            elif market['key'] == 'totals':
                for outcome in market['outcomes']:
                    if "Over" in outcome['name'] and 1.50 <= outcome['price'] <= 2.10:
                        lbl = "Pontos" if 'basketball' in sport else "Gols"
                        bets.append({
                            'match': match_name,
                            'selection': f"Mais de {outcome['point']} {lbl}",
                            'odd': outcome['price'],
                            'category': "📊 ESTATÍSTICA (Over)",
                            'sport': "🏀 BASQUETE" if 'basketball' in sport else "⚽ FUTEBOL"
                        })

    return bets

async def create_tip_message():
    all_events = []
    # Busca nas ligas principais
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    
    valid_bets = process_bets(all_events)
    
    if not valid_bets:
        return f"⚠️ <b>Status:</b> A API não retornou jogos confiáveis para as próximas 24h. Tente mais tarde.", None

    # Embaralha e seleciona os melhores (Máximo 15 para não ficar polúido)
    random.shuffle(valid_bets)
    selected_tips = valid_bets[:15]
    
    # Ordena: Futebol primeiro, depois Basquete
    selected_tips.sort(key=lambda x: x['sport'], reverse=True)

    header = (
        "🏆 <b>DVD TIPS - ANÁLISE PROFISSIONAL</b> 🏆\n"
        f"📅 <b>DATA: {get_brazil_time().strftime('%d/%m/%Y')}</b>\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )
    
    body = ""
    acumulada_odd = 1.0
    
    for i, bet in enumerate(selected_tips, 1):
        body += (
            f"{i}️⃣ <b>{bet['category']}</b>\n"
            f"🏟️ {bet['match']}\n"
            f"🎯 {bet['selection']} | <b>ODD: {bet['odd']:.2f}</b>\n"
            "──────────────────\n"
        )
        # Calcula a odd da acumulada (simulação)
        if i <= 5: acumulada_odd *= bet['odd']

    # Se tiver pelo menos 3 jogos, mostra sugestão de tripla/múltipla
    if len(selected_tips) >= 3:
        jackpot_msg = (
            f"\n🚀 <b>SUGESTÃO DE MÚLTIPLA (TOP 5):</b>\n"
            f"📈 <b>ODD TOTAL: {acumulada_odd:.2f}</b>\n"
            "💰 <i>Gestão recomendada: 0.5% da banca</i>\n"
        )
        body += jackpot_msg

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 APOSTAR NA BETANO", url="https://www.betano.bet.br")],
        [InlineKeyboardButton("📲 APOSTAR NA BET365", url="https://www.bet365.com")]
    ])
    
    footer = "\n⚠️ <i>As odds podem variar. Aposte com responsabilidade.</i>"
    
    return header + body + footer, markup

# --- COMANDOS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 <b>Bot Online e Atualizado!</b>\nUse /postar para gerar a lista do dia.", parse_mode='HTML')

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ <b>Analisando mercado...</b>", parse_mode='HTML')
    try:
        text, markup = await create_tip_message()
        if markup:
            await context.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
            await msg.edit_text("✅ <b>Lista enviada para o canal!</b>", parse_mode='HTML')
        else: await msg.edit_text(text, parse_mode='HTML')
    except Exception as e: await msg.edit_text(f"❌ Erro: {e}")

async def auto_post(app):
    text, markup = await create_tip_message()
    if markup: await app.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)

async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    # Agenda para 11:00 da manhã (Horário de Brasília)
    scheduler.add_job(auto_post, 'cron', hour=11, minute=0, timezone=pytz.timezone('America/Sao_Paulo'), args=[application])
    scheduler.start()

if __name__ == '__main__':
    # Inicia o servidor falso (Flask) para o Render
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Inicia o Bot
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('postar', postar_manual))
    
    print("🤖 Bot DVD TIPS Rodando (Modo Pro)...")
    application.run_polling()
