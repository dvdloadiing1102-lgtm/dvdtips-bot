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
    'soccer_france_ligue_one'
]
BASKETBALL_LEAGUES = ['basketball_nba']
MAJOR_LEAGUES = SOCCER_LEAGUES + BASKETBALL_LEAGUES

# --- SERVIDOR FALSO (MANTÉM O BOT VIVO NO RENDER) ---
app = Flask(__name__)
@app.route('/')
def home(): return "🤖 Bot DVD TIPS - Jackpot Ativo!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- FUNÇÕES ---

def get_brazil_time():
    return datetime.datetime.now(pytz.timezone('America/Sao_Paulo'))

def get_odds(sport_key):
    # Solicitamos H2H, Totals e Spreads (Handicaps)
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h,totals,spreads', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else []
    except: return []

def process_bets(odds_data):
    bets = []
    now_br = get_brazil_time()
    limit_time = now_br + datetime.timedelta(hours=24)
    
    for event in odds_data:
        # Filtro de Data/Hora
        try:
            event_utc = datetime.datetime.strptime(event['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
            event_br = event_utc.astimezone(pytz.timezone('America/Sao_Paulo'))
            if event_br < now_br or event_br > limit_time: continue
            hora = event_br.strftime('%H:%M')
        except: continue

        if not event.get('bookmakers'): continue
        
        match = f"{event['home_team']} x {event['away_team']}"
        sport = event['sport_key']
        markets = event['bookmakers'][0]['markets']
        
        for market in markets:
            # VENCEDOR
            if market['key'] == 'h2h':
                for o in market['outcomes']:
                    if 1.25 <= o['price'] <= 2.30:
                        cat = "🧱 TIJOLINHO" if o['price'] <= 1.50 else "🧠 VALOR"
                        bets.append({'match': match, 'time': hora, 'selection': f"Vence: {o['name']}", 'odd': o['price'], 'cat': cat, 'sport': sport})
            
            # OVER GOLS/PONTOS
            elif market['key'] == 'totals':
                for o in market['outcomes']:
                    if "Over" in o['name'] and 1.50 <= o['price'] <= 2.00:
                        lbl = "Pontos" if 'basketball' in sport else "Gols"
                        bets.append({'match': match, 'time': hora, 'selection': f"Mais de {o['point']} {lbl}", 'odd': o['price'], 'cat': "📊 ESTATÍSTICA", 'sport': sport})
            
            # HANDICAP (NOVIDADE)
            elif market['key'] == 'spreads':
                for o in market['outcomes']:
                    if 1.80 <= o['price'] <= 2.10:
                        sinal = "+" if o['point'] > 0 else ""
                        bets.append({'match': match, 'time': hora, 'selection': f"Handicap {o['name']} {sinal}{o['point']}", 'odd': o['price'], 'cat': "⚖️ HANDICAP", 'sport': sport})
    return bets

async def create_tip_message():
    all_events = []
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    
    valid_bets = process_bets(all_events)
    
    if not valid_bets: return "⚠️ <b>Aviso:</b> Sem jogos bons nas próximas 24h.", None

    # Embaralha
    random.shuffle(valid_bets)
    
    # 1. Separa lista principal (Top 12)
    main_list = valid_bets[:12]
    main_list.sort(key=lambda x: x['time'])

    # 2. Lógica do Jackpot Real (Tenta montar acumulada entre 20x e 35x)
    jackpot_list = []
    jackpot_total_odd = 1.0
    
    # Mistura de novo para tentar pegar jogos diferentes da lista principal
    pool_jackpot = valid_bets[:] 
    random.shuffle(pool_jackpot)
    
    for bet in pool_jackpot:
        # Evita repetir jogo se possível, mas prioriza bater a odd
        if jackpot_total_odd * bet['odd'] > 35.0: continue # Passou do limite, pula
        
        jackpot_list.append(bet)
        jackpot_total_odd *= bet['odd']
        
        if 25.0 <= jackpot_total_odd <= 35.0:
            break # Atingiu a meta!
    
    # Se não conseguiu bater 20x, limpa (para não mandar acumulada fraca)
    if jackpot_total_odd < 15.0:
        jackpot_list = []

    # --- MONTAGEM DO TEXTO ---
    header = (
        "🏆 <b>DVD TIPS - ELITE PRO</b> 🏆\n"
        f"📅 <b>{get_brazil_time().strftime('%d/%m/%Y')}</b> | 📍 Mercados Reais\n"
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    )
    
    body = ""
    for i, bet in enumerate(main_list, 1):
        body += (
            f"{i}️⃣ <b>{bet['cat']}</b>\n"
            f"⏰ {bet['time']} | 🏟️ {bet['match']}\n"
            f"🎯 {bet['selection']} | <b>ODD: {bet['odd']:.2f}</b>\n"
            "──────────────────\n"
        )

    # --- SEÇÃO JACKPOT ---
    jackpot_text = ""
    if jackpot_list:
        jackpot_text += "\n🚀 <b>JACKPOT SUPREMO (ALTO RISCO)</b> 🚀\n"
        jackpot_text += f"📈 <b>ODD TOTAL: {jackpot_total_odd:.2f}</b>\n\n"
        for bet in jackpot_list:
            jackpot_text += f"• {bet['match']}\n  └ 🎯 {bet['selection']} (@{bet['odd']:.2f})\n"
        jackpot_text += "\n💰 <i>Stake recomendada: 0.25% da banca (Troco de pão)</i>\n"

    footer = "\n⚠️ <i>Faça sua análise. Odds sujeitas a alteração.</i>"
    
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📲 APOSTAR AGORA", url="https://www.bet365.com")]])
    
    return header + body + jackpot_text + footer, markup

# --- COMANDOS EXTRAS ---
async def buscar_zebra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🦓 Buscando zebras...")
    all_events = []
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    zebras = []
    for e in all_events:
        if not e.get('bookmakers'): continue
        match = f"{e['home_team']} x {e['away_team']}"
        for o in e['bookmakers'][0]['markets'][0]['outcomes']:
            if 3.20 <= o['price'] <= 6.00:
                zebras.append(f"🦓 <b>{match}</b>\n🎯 {o['name']} (ODD {o['price']})")
    
    if zebras:
        random.shuffle(zebras)
        await msg.edit_text("🦁 <b>ZEBRAS DO DIA</b> 🦁\n\n" + "\n\n".join(zebras[:4]), parse_mode='HTML')
    else: await msg.edit_text("❌ Sem zebras hoje.")

async def calcular_lucro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        odd = float(context.args[0].replace(',', '.'))
        val = float(context.args[1].replace(',', '.'))
        await update.message.reply_text(f"💵 Aposta: {val} x Odd {odd}\n💰 Retorno: <b>{odd*val:.2f}</b>", parse_mode='HTML')
    except: await update.message.reply_text("Use: /calc 2.00 50")

async def simular_alavancagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    banca = 100.00
    msg = "🚀 <b>ALAVANCAGEM 3 NÍVEIS (Odd 1.50)</b>\n"
    for i in range(1,4):
        lucro = banca * 0.50
        banca += lucro
        msg += f"Nível {i}: Ganhou {lucro:.2f} ➡ Banca: {banca:.2f}\n"
    await update.message.reply_text(msg, parse_mode='HTML')

# --- CONFIGURAÇÃO ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 <b>Bot Online!</b>\n/postar\n/zebra\n/calc", parse_mode='HTML')

async def postar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ <b>Processando Jackpot e Tips...</b>", parse_mode='HTML')
    try:
        text, markup = await create_tip_message()
        if markup:
            await context.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
            await msg.edit_text("✅ Enviado!")
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
    application.add_handler(CommandHandler('zebra', buscar_zebra))
    application.add_handler(CommandHandler('calc', calcular_lucro))
    application.add_handler(CommandHandler('alavancagem', simular_alavancagem))
    application.run_polling()
