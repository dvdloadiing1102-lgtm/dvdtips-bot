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
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application, CallbackQueryHandler
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

# --- SERVIDOR FALSO ---
app = Flask(__name__)
@app.route('/')
def home(): return "🤖 Bot DVD TIPS - Versão Premium Ativa!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- FUNÇÕES API ---
def get_brazil_time():
    return datetime.datetime.now(pytz.timezone('America/Sao_Paulo'))

def get_odds(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h,totals,spreads', 'oddsFormat': 'decimal'}
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else []
    except: return []

# --- LÓGICA DE APOSTAS ---
def process_bets(odds_data):
    bets = []
    now_br = get_brazil_time()
    limit_time = now_br + datetime.timedelta(hours=24)
    
    for event in odds_data:
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
            if market['key'] == 'h2h':
                for o in market['outcomes']:
                    if 1.25 <= o['price'] <= 2.30:
                        cat = "🧱 TIJOLINHO" if o['price'] <= 1.50 else "🧠 VALOR"
                        bets.append({'match': match, 'time': hora, 'selection': f"Vence: {o['name']}", 'odd': o['price'], 'cat': cat})
            elif market['key'] == 'totals':
                for o in market['outcomes']:
                    if "Over" in o['name'] and 1.50 <= o['price'] <= 2.00:
                        lbl = "Pontos" if 'basketball' in sport else "Gols"
                        bets.append({'match': match, 'time': hora, 'selection': f"Mais de {o['point']} {lbl}", 'odd': o['price'], 'cat': "📊 ESTATÍSTICA"})
            elif market['key'] == 'spreads':
                for o in market['outcomes']:
                    if 1.80 <= o['price'] <= 2.10:
                        sinal = "+" if o['point'] > 0 else ""
                        bets.append({'match': match, 'time': hora, 'selection': f"Handicap {o['name']} {sinal}{o['point']}", 'odd': o['price'], 'cat': "⚖️ HANDICAP"})
    return bets

async def create_tip_message():
    all_events = []
    for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
    valid_bets = process_bets(all_events)
    
    if not valid_bets: return "⚠️ <b>Aviso:</b> Sem jogos bons nas próximas 24h.", None

    random.shuffle(valid_bets)
    main_list = valid_bets[:12]
    main_list.sort(key=lambda x: x['time'])

    jackpot_list = []
    jackpot_total = 1.0
    pool = valid_bets[:]
    random.shuffle(pool)
    for b in pool:
        if jackpot_total * b['odd'] > 35.0: continue
        jackpot_list.append(b)
        jackpot_total *= b['odd']
        if 25.0 <= jackpot_total <= 35.0: break
    if jackpot_total < 15.0: jackpot_list = []

    header = f"🏆 <b>DVD TIPS - ELITE PRO</b> 🏆\n📅 <b>{get_brazil_time().strftime('%d/%m/%Y')}</b> | 📍 Mercados Reais\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    body = ""
    for i, b in enumerate(main_list, 1):
        body += f"{i}️⃣ <b>{b['cat']}</b>\n⏰ {b['time']} | 🏟️ {b['match']}\n🎯 {b['selection']} | <b>ODD: {b['odd']:.2f}</b>\n──────────────────\n"

    jackpot_text = ""
    if jackpot_list:
        jackpot_text += f"\n🚀 <b>JACKPOT SUPREMO (ODD {jackpot_total:.2f})</b> 🚀\n"
        for b in jackpot_list: jackpot_text += f"• {b['match']} ➡ {b['selection']} (@{b['odd']:.2f})\n"

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📲 APOSTAR AGORA", url="https://www.bet365.com")]])
    return header + body + jackpot_text, markup

# --- COMANDOS E MENU ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # MENU INTERATIVO (BOTÕES)
    keyboard = [
        [InlineKeyboardButton("📜 Postar Lista no Canal", callback_data='postar')],
        [InlineKeyboardButton("🦓 Caçar Zebras", callback_data='zebra'), InlineKeyboardButton("🎁 Bônus", callback_data='bonus')],
        [InlineKeyboardButton("🧮 Calculadora de Lucro", callback_data='help_calc')],
        [InlineKeyboardButton("🛡️ Calculadora de Cobertura", callback_data='help_hedge')]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = (
        "🤖 <b>PAINEL DVD TIPS v3.0</b>\n\n"
        "Bem-vindo ao sistema de gestão.\n"
        "Selecione uma opção abaixo:"
    )
    if update.message: await update.message.reply_text(msg, reply_markup=markup, parse_mode='HTML')
    else: await update.callback_query.edit_message_text(msg, reply_markup=markup, parse_mode='HTML')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'postar':
        await query.edit_message_text("⏳ <b>Analisando mercados... aguarde.</b>", parse_mode='HTML')
        try:
            text, markup = await create_tip_message()
            if markup:
                await context.bot.send_message(CHANNEL_ID, text, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
                await query.edit_message_text("✅ <b>Lista enviada para o canal!</b>\n/start para voltar.", parse_mode='HTML')
            else: await query.edit_message_text(text, parse_mode='HTML')
        except Exception as e: await query.edit_message_text(f"Erro: {e}")

    elif data == 'zebra':
        await query.edit_message_text("🦓 <b>Buscando zebras (Odds 3.00+)...</b>", parse_mode='HTML')
        # (Lógica simplificada da zebra aqui para não estourar o código)
        all_events = []
        for league in MAJOR_LEAGUES: all_events.extend(get_odds(league))
        zebras = []
        for e in all_events:
            if not e.get('bookmakers'): continue
            match = f"{e['home_team']} x {e['away_team']}"
            for o in e['bookmakers'][0]['markets'][0]['outcomes']:
                if 3.20 <= o['price'] <= 6.50: zebras.append(f"🦓 <b>{match}</b>\n🎯 {o['name']} (@{o['price']})")
        
        if zebras:
            random.shuffle(zebras)
            await context.bot.send_message(query.message.chat.id, "🦁 <b>ZEBRAS DO DIA</b> 🦁\n\n" + "\n\n".join(zebras[:5]), parse_mode='HTML')
            await query.message.delete()
        else: await query.edit_message_text("❌ Nenhuma zebra hoje.\n/start para voltar.")

    elif data == 'bonus':
        msg = (
            "🎁 <b>PARCEIROS E BÔNUS</b> 🎁\n\n"
            "🟢 <b>Bet365:</b> Ganhe até R$200 em créditos.\n"
            "👉 bit.ly/seulink365\n\n"
            "🟠 <b>Betano:</b> Bônus de 100% até R$500.\n"
            "👉 bit.ly/seulinkbetano\n\n"
            "<i>Use nossos links para apoiar o projeto!</i>"
        )
        await query.edit_message_text(msg, parse_mode='HTML')

    elif data == 'help_calc':
        await query.edit_message_text("ℹ️ <b>COMO USAR:</b>\nDigite: `/calc [odd] [valor]`\n\nExemplo: `/calc 1.80 50`\n(Para saber quanto ganha apostando 50 reais na odd 1.80)", parse_mode='Markdown')

    elif data == 'help_hedge':
        msg = (
            "ℹ️ <b>COMO USAR A COBERTURA:</b>\n"
            "Serve para calcular quanto apostar no contra-ataque para não perder dinheiro.\n\n"
            "Digite: `/cobertura [ValorAposta1] [Odd1] [Odd2]`\n\n"
            "Ex: `/cobertura 100 2.00 3.50`\n"
            "(Apostei 100 na Odd 2.00, quanto apostar na Odd 3.50 pra salvar?)"
        )
        await query.edit_message_text(msg, parse_mode='HTML')

# --- CALCULADORAS ---
async def calcular_lucro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        odd = float(context.args[0].replace(',', '.'))
        val = float(context.args[1].replace(',', '.'))
        await update.message.reply_text(f"💵 Aposta: {val} x Odd {odd}\n💰 Retorno: <b>{odd*val:.2f}</b>", parse_mode='HTML')
    except: await update.message.reply_text("Use: /calc 2.00 50")

async def calcular_cobertura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Args: Valor1, Odd1, Odd2
        v1 = float(context.args[0].replace(',', '.'))
        o1 = float(context.args[1].replace(',', '.'))
        o2 = float(context.args[2].replace(',', '.'))
        
        # Fórmula de Hedging (Para Zero Profit)
        # Retorno 1 = v1 * o1
        # Necessário apostar v2 tal que v2 * o2 = v1 + v2 (Breakeven) ou v1*o1 (Profit equal)
        # Vamos calcular para recuperar o investimento total (v1 + v2)
        
        v2 = (v1 * o1) / o2
        investimento_total = v1 + v2
        retorno = v1 * o1 # ou v2 * o2 (são iguais)
        lucro = retorno - investimento_total
        
        msg = (
            f"🛡️ <b>CÁLCULO DE COBERTURA</b>\n\n"
            f"Aposta Principal: R$ {v1:.2f} (@{o1})\n"
            f"Aposta de Cobertura: <b>R$ {v2:.2f}</b> (@{o2})\n\n"
            f"💰 Investimento Total: R$ {investimento_total:.2f}\n"
            f"🔄 Retorno (Qualquer resultado): R$ {retorno:.2f}\n"
            f"📊 Resultado Final: {'🟢 Lucro' if lucro > 0 else '🔴 Prejuízo'} de R$ {lucro:.2f}"
        )
        await update.message.reply_text(msg, parse_mode='HTML')
    except: await update.message.reply_text("❌ Erro. Ex: `/cobertura 100 2.00 3.50`", parse_mode='Markdown')

async def simular_alavancagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    banca = 50.00
    msg = "🚀 <b>ALAVANCAGEM 3 NÍVEIS (Odd 1.50)</b>\n"
    for i in range(1,4):
        lucro = banca * 0.50
        banca += lucro
        msg += f"Nível {i}: Ganhou {lucro:.2f} ➡ Banca: {banca:.2f}\n"
    await update.message.reply_text(msg, parse_mode='HTML')

# --- CONFIGURAÇÃO ---
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
    
    # HANDLERS
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(button_handler)) # Para os botões funcionarem
    
    application.add_handler(CommandHandler('postar', lambda u,c: start(u,c))) # Redireciona para o menu
    application.add_handler(CommandHandler('calc', calcular_lucro))
    application.add_handler(CommandHandler('cobertura', calcular_cobertura))
    application.add_handler(CommandHandler('alavancagem', simular_alavancagem))
    
    print("🤖 Bot DVD TIPS v3.0 (Menu Interativo) Rodando...")
    application.run_polling()
