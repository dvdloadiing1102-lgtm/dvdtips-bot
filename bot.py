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

# --- LIGAS ---
SOCCER_LEAGUES = [
    'soccer_brazil_campeonato', 'soccer_epl', 'soccer_spain_la_liga', 
    'soccer_italy_serie_a', 'soccer_germany_bundesliga', 'soccer_uefa_champs_league',
    'soccer_france_ligue_one', 'soccer_portugal_primeira_liga'
]
BASKETBALL_LEAGUES = ['basketball_nba']
MAJOR_LEAGUES = SOCCER_LEAGUES + BASKETBALL_LEAGUES

# --- SERVIDOR FALSO (RENDER) ---
app = Flask(__name__)
@app.route('/')
def home(): return "🤖 Bot DVD TIPS - Sistema Online!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- FUNÇÕES AUXILIARES ---

def get_brazil_time():
    """Retorna data e hora atuais no Brasil"""
    return datetime.datetime.now(pytz.timezone('America/Sao_Paulo'))

def format_time(iso_date):
    """Converte horário da API (UTC) para Brasil (HH:MM)"""
    try:
        dt_utc = datetime.datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        dt_br = dt_utc.astimezone(pytz.timezone('America/Sao_Paulo'))
        return dt_br.strftime('%H:%M')
    except:
        return "??:??"

def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h,totals', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else []
    except: return []

def process_bets(odds_data):
    bets = []
    now_br = get_brazil_time()
    limit_time = now_br + datetime.timedelta(hours=24)
    
    for event in odds_data:
        # Filtro de Data e Hora
        try:
            event_time_utc = datetime.datetime.strptime(event['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
            event_time_br = event_time_utc.astimezone(pytz.timezone('America/Sao_Paulo'))
            
            # Se já passou ou é muito longe, ignora
            if event_time_br < now_br or event_time_br > limit_time: continue
            
            hora_jogo = event_time_br.strftime('%H:%M')
        except: continue

        if not event.get('bookmakers'): continue
        
        match_name = f"{event['home_team']} x {event['away_team']}"
        sport = event['sport_key']
        markets = event['bookmakers'][0]['markets']
        
        for market in markets:
            # VENCEDOR
            if market['key'] == 'h2h':
                for outcome in market['outcomes']:
                    odd = outcome['price']
                    if 1.25 <= odd <= 2.50:
                        if odd <= 1.50: cat = "🧱 TIJOLINHO (Seguro)"
                        elif odd <= 1.90: cat = "🧠 BET INTELIGENTE"
                        else: cat = "🔥 OUSADIA (Valor)"
                        
                        bets.append({
                            'match': match_name,
                            'time': hora_jogo,
                            'selection': f"Vencer: {outcome['name']}",
                            'odd': odd,
                            'category': cat,
                            'sport': "🏀 BASQUETE" if 'basketball' in sport else "⚽ FUTEBOL"
                        })
            
            # OVER GOLS/PONTOS
            elif market['key'] == 'totals':
                for outcome in market['outcomes']:
                    if "Over" in outcome['name'] and 1.50 <= outcome['price'] <= 2.10:
                        lbl = "Pontos" if 'basketball' in sport else "Gols"
                        bets.append({
                            'match': match_name,
                            'time': hora_jogo,
                            'selection': f"Mais de {outcome['point']} {lbl}",
                            'odd': outcome['price'],
                            'category': "📊 ESTATÍSTICA (Over)",
                            'sport': "🏀 BASQUETE" if 'basketball' in sport else "⚽ FUTEBOL"
                        })
    return bets

# --- COMANDO /POSTAR (PRINCIPAL) ---
async def create_tip_message():
    all_events = []
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    
    valid_bets = process_bets(all_events)
    
    if not valid_bets:
        return f"⚠️ <b>Status:</b> Sem jogos confiáveis nas próximas 24h.", None

    random.shuffle(valid_bets)
    selected_tips = valid_bets[:15]
    selected_tips.sort(key=lambda x: x['time']) # Ordena por horário (do mais cedo pro mais tarde)

    header = (
        "🏆 <b>DVD TIPS - LISTA DO DIA</b> 🏆\n"
        f"📅 <b>DATA: {get_brazil_time().strftime('%d/%m/%Y')}</b>\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )
    
    body = ""
    for i, bet in enumerate(selected_tips, 1):
        body += (
            f"{i}️⃣ <b>{bet['category']}</b>\n"
            f"⏰ {bet['time']} | 🏟️ {bet['match']}\n"
            f"🎯 {bet['selection']} | <b>ODD: {bet['odd']:.2f}</b>\n"
            "──────────────────\n"
        )

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📲 APOSTAR AGORA", url="https://www.bet365.com")]])
    return header + body + "\n⚠️ <i>Odds sujeitas a alteração.</i>", markup

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ <b>Analisando horários e odds...</b>", parse_mode='HTML')
    try:
        text, markup = await create_tip_message()
        if markup:
            await context.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
            await msg.edit_text("✅ <b>Enviado!</b>", parse_mode='HTML')
        else: await msg.edit_text(text, parse_mode='HTML')
    except Exception as e: await msg.edit_text(f"Erro: {e}")

# --- NOVA FUNÇÃO: /ZEBRA ---
async def buscar_zebra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦓 <b>Buscando oportunidades de risco alto...</b>", parse_mode='HTML')
    all_events = []
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    
    zebras = []
    now_br = get_brazil_time()
    
    for event in all_events:
        if not event.get('bookmakers'): continue
        try:
            # Filtro de data simples
            dt = datetime.datetime.strptime(event['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
            if dt.astimezone(pytz.timezone('America/Sao_Paulo')).date() != now_br.date(): continue
        except: continue

        match = f"{event['home_team']} x {event['away_team']}"
        for outcome in event['bookmakers'][0]['markets'][0]['outcomes']:
            # Pega odds entre 3.00 e 7.00
            if 3.00 <= outcome['price'] <= 7.00:
                zebras.append(f"🦓 <b>{match}</b>\n🎯 {outcome['name']} | ODD: <b>{outcome['price']}</b>")
    
    if zebras:
        random.shuffle(zebras)
        msg = "🦁 <b>ZEBRAS DO DIA (ODD 3.00+)</b> 🦁\n\n" + "\n──────────────────\n".join(zebras[:5])
        await update.message.reply_text(msg, parse_mode='HTML')
    else:
        await update.message.reply_text("❌ Nenhuma zebra boa hoje.")

# --- NOVA FUNÇÃO: /CALC ---
async def calcular_lucro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) < 2:
            await update.message.reply_text("❌ Use: `/calc 1.80 50`", parse_mode='Markdown')
            return
        odd = float(context.args[0].replace(',', '.'))
        valor = float(context.args[1].replace(',', '.'))
        retorno = odd * valor
        lucro = retorno - valor
        await update.message.reply_text(
            f"🧮 <b>CALCULADORA</b>\n💵 Aposta: R$ {valor:.2f} | Odd: {odd}\n\n💰 Retorno: <b>R$ {retorno:.2f}</b>\n✅ Lucro: <b>R$ {lucro:.2f}</b>", 
            parse_mode='HTML'
        )
    except: await update.message.reply_text("❌ Erro. Use apenas números.")

# --- NOVA FUNÇÃO: /ALAVANCAGEM ---
async def simular_alavancagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    banca = 50.00 # Valor base
    odd = 1.50    # Odd base
    msg = f"🚀 <b>SIMULAÇÃO DE ALAVANCAGEM (3 NÍVEIS)</b>\nInício: R$ {banca:.2f} | Odd Média: {odd:.2f}\n\n"
    
    atual = banca
    for i in range(1, 4):
        novo = atual * odd
        lucro = novo - atual
        msg += f"✅ Nível {i}: Apostou R$ {atual:.2f} ➡ <b>R$ {novo:.2f}</b>\n"
        atual = novo
        
    msg += f"\n💰 <b>Resultado Final: R$ {atual:.2f}</b>"
    await update.message.reply_text(msg, parse_mode='HTML')

# --- CONFIGURAÇÃO INICIAL E AGENDAMENTO ---
async def auto_post(app):
    text, markup = await create_tip_message()
    if markup: await app.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)

async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_post, 'cron', hour=11, minute=0, timezone=pytz.timezone('America/Sao_Paulo'), args=[application])
    scheduler.start()

async def start(update: Update, context: Context
