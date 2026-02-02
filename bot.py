import logging
import requests
import datetime
import random
import os
import threading
import time
import sqlite3
import pytz
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

# --- CONFIGURAÇÃO ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8197536655:AAHtSBxCgIQpkKj2TQq1cFGRHMoe9McjK_4")
ODDS_API_KEY = "e8d200f52a843404bc434738f4433550"
CHANNEL_ID = "@dvdtips1"

# Estados
(MENU, ADD_VALOR, ADD_ODD, ADD_DESC) = range(4)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- BANCO DE DADOS ---
class BetDatabase:
    def __init__(self, db_path="bets.db"):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.get_connection() as conn:
            conn.cursor().execute("""CREATE TABLE IF NOT EXISTS bets (id INTEGER PRIMARY KEY, user_id INTEGER, value REAL, odd REAL, description TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit()

    def add_bet(self, uid, val, odd, desc):
        with self.get_connection() as conn:
            conn.cursor().execute("INSERT INTO bets (user_id, value, odd, description) VALUES (?, ?, ?, ?)", (uid, val, odd, desc))
            conn.commit()

    def get_pending(self, uid):
        with self.get_connection() as conn:
            return conn.cursor().execute("SELECT id, value, odd, description FROM bets WHERE user_id = ? AND status = 'pending'", (uid,)).fetchall()

    def resolve_bet(self, bet_id, status):
        with self.get_connection() as conn:
            conn.cursor().execute("UPDATE bets SET status = ? WHERE id = ?", (status, bet_id))
            conn.commit()

    def get_stats(self, uid):
        with self.get_connection() as conn:
            rows = conn.cursor().execute("SELECT status, value, odd FROM bets WHERE user_id = ? AND status != 'pending'", (uid,)).fetchall()
        inv, ret, g, r = 0, 0, 0, 0
        for status, val, odd in rows:
            inv += val
            if status == 'green':
                ret += val * odd
                g += 1
            else:
                r += 1
        return inv, ret, g, r

db = BetDatabase()

# --- SERVIDOR FLASK ---
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Bot Apostas V4 Online ⚽"
def run_flask(): flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
def keep_alive(): 
    while True: 
        try: requests.get("http://127.0.0.1:10000")
        except: pass
        time.sleep(600)

# --- LÓGICA DE ODDS (ATUALIZADA) ---
# Adicionei mais ligas para evitar "Zero Jogos"
SOCCER_LEAGUES = [
    'soccer_epl', 'soccer_uefa_champs_league', 'soccer_uefa_europa_league', 
    'soccer_spain_la_liga', 'soccer_italy_serie_a', 'soccer_germany_bundesliga',
    'soccer_france_ligue_one', 'soccer_england_efl_cup', 'soccer_england_championship',
    'soccer_netherlands_eredivisie', 'soccer_portugal_primeira_liga'
]
BASKETBALL_LEAGUES = ['basketball_nba']

def get_odds(sport):
    try: 
        resp = requests.get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds/", params={'apiKey': ODDS_API_KEY, 'regions': 'eu', 'markets': 'h2h', 'oddsFormat': 'decimal'})
        return resp.json() if resp.status_code == 200 else []
    except: return []

async def create_tip_message():
    all_events = []
    # Busca em Futebol e NBA
    for l in SOCCER_LEAGUES + BASKETBALL_LEAGUES:
        data = get_odds(l)
        if isinstance(data, list): all_events.extend(data)
    
    if not all_events: return "⚠️ <b>Sem jogos disponíveis na API agora.</b>\n(Tente mais tarde)", None

    # Filtragem Rigorosa de Data (Próximas 24h apenas)
    valid_bets = []
    now_br = datetime.datetime.now(pytz.timezone('America/Sao_Paulo'))
    limit_time = now_br + datetime.timedelta(hours=24)

    for e in all_events:
        try:
            # Converte data UTC da API para Brasil
            start_utc = datetime.datetime.strptime(e['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
            start_br = start_utc.astimezone(pytz.timezone('America/Sao_Paulo'))
            
            # Pula jogos passados ou muito longe
            if start_br < now_br or start_br > limit_time: continue
            
            home = e['home_team']
            away = e['away_team']
            sport = e['sport_key']
            
            if not e['bookmakers']: continue
            outcomes = e['bookmakers'][0]['markets'][0]['outcomes']
            
            # Lógica de Valor (Odds entre 1.40 e 2.30)
            for o in outcomes:
                if 1.40 <= o['price'] <= 2.30:
                    cat = "🧱 SEGURANÇA" if o['price'] < 1.60 else "🧠 VALOR"
                    emoji = "🏀" if "basketball" in sport else "⚽"
                    valid_bets.append({
                        'match': f"{home} x {away}",
                        'selection': o['name'],
                        'odd': o['price'],
                        'time': start_br.strftime("%H:%M"),
                        'cat': cat,
                        'emoji': emoji
                    })
                    break # Pega só uma aposta por jogo
        except: continue

    if not valid_bets: return "⚠️ <b>Mercado difícil hoje.</b>\nNenhuma aposta de valor encontrada.", None

    # Ordena por horário e embaralha levemente
    valid_bets.sort(key=lambda x: x['time'])
    main_list = valid_bets[:10]

    # --- LÓGICA JACKPOT FLEXÍVEL (A Correção) ---
    jackpot_list = []
    jackpot_odd = 1.0
    
    # Tenta montar o bilhete
    pool = valid_bets[:]
    random.shuffle(pool)
    
    for bet in pool:
        # Se a odd já passou de 35, para.
        if jackpot_odd * bet['odd'] > 35.0: continue
        
        jackpot_list.append(bet)
        jackpot_odd *= bet['odd']
        
        # Se já passou de 10.0, já considera um bom jackpot (antes era exigido 25)
        if jackpot_odd >= 15.0: break
    
    # Se ficou muito mixuruca (menos de 3.0), ignora
    jackpot_text = ""
    if jackpot_odd > 3.0:
        jackpot_text = f"\n🚀 <b>BILHETE PRONTO (ODD {jackpot_odd:.2f})</b> 🚀\n"
        for b in jackpot_list:
            jackpot_text += f"• {b['match']} ➡ {b['selection']} (@{b['odd']:.2f})\n"
    else:
        jackpot_text = "\n⚠️ <i>Mercado com poucos jogos para Jackpot hoje.</i>"

    # Monta Texto Final
    msg = f"🏆 <b>TIPS DO DIA - {now_br.strftime('%d/%m')}</b> 🏆\n\n"
    for b in main_list:
        msg += f"{b['emoji']} <b>{b['time']}</b> | {b['match']}\n🎯 {b['selection']} (@{b['odd']:.2f})\n────────────────\n"
    
    msg += jackpot_text
    
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📲 APOSTAR AGORA", url="https://www.bet365.com")]])
    return msg, markup

# --- FLUXOS DO BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📝 Registrar Aposta", callback_data='add_bet')],
        [InlineKeyboardButton("✅ Resolver Pendentes", callback_data='resolve_bet')],
        [InlineKeyboardButton("📈 Meu Relatório", callback_data='my_stats')],
        [InlineKeyboardButton("🎲 Gerar Tips (Canal)", callback_data='gen_tips')]
    ]
    
    msg = "⚽ <b>GESTOR DE BANCA V4</b>\nSelecione uma opção:"
    if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    return MENU

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    
    if q.data == 'gen_tips':
        await q.edit_message_text("⏳ <b>Analisando Mercado (Ligas Globais)...</b>", parse_mode=ParseMode.HTML)
        try:
            txt, markup = await create_tip_message()
            # Tenta mandar no canal, se falhar manda no privado
            try:
                if markup: await context.bot.send_message(CHANNEL_ID, txt, reply_markup=markup, parse_mode=ParseMode.HTML)
                else: await context.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                await q.edit_message_text("✅ <b>Enviado para o Canal!</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
            except:
                await q.edit_message_text(f"⚠️ Erro ao postar no canal (Bot é admin?).\n\n{txt}", parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception as e:
            await q.edit_message_text(f"Erro técnico: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]))
        return MENU

    if q.data == 'add_bet': await q.edit_message_text("💰 Valor da aposta:"); return ADD_VALOR
    
    if q.data == 'my_stats':
        inv, ret, g, r = db.get_stats(uid)
        profit = ret - inv
        roi = (profit / inv * 100) if inv > 0 else 0
        await q.edit_message_text(f"📊 <b>RELATÓRIO</b>\nInv: R${inv:.2f} | Ret: R${ret:.2f}\n✅ {g} | ❌ {r}\nLucro: <b>R$ {profit:.2f}</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]]), parse_mode=ParseMode.HTML); return MENU

    if q.data == 'resolve_bet':
        pending = db.get_pending(uid)
        if not pending: await q.edit_message_text("Nada pendente.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data='back')]])); return MENU
        kb = [[InlineKeyboardButton(f"{desc} (R${val})", callback_data=f"res_{pid}")] for pid, val, odd, desc in pending]
        kb.append([InlineKeyboardButton("Voltar", callback_data='back')])
        await q.edit_message_text("Qual finalizou?", reply_markup=InlineKeyboardMarkup(kb)); return MENU
    
    if q.data == 'back': await start(q.message, context); return MENU
    
    if q.data.startswith('res_'):
        context.user_data['res_id'] = q.data.split('_')[1]
        await q.edit_message_text("Resultado?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ GREEN", callback_data='set_green'), InlineKeyboardButton("❌ RED", callback_data='set_red')]])); return MENU

    if q.data.startswith('set_'):
        db.resolve_bet(context.user_data['res_id'], q.data.split('_')[1])
        await q.edit_message_text("✅ Salvo!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Menu", callback_data='back')]])); return MENU

# --- WIZARD ---
async def receive_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['bet_val'] = float(update.message.text.replace(',', '.')); await update.message.reply_text("🔢 Odd:"); return ADD_ODD
    except: await update.message.reply_text("Erro. Digite número."); return ADD_VALOR

async def receive_odd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['bet_odd'] = float(update.message.text.replace(',', '.')); await update.message.reply_text("📝 Descrição:"); return ADD_DESC
    except: await update.message.reply_text("Erro."); return ADD_ODD

async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.add_bet(update.effective_user.id, context.user_data['bet_val'], context.user_data['bet_odd'], update.message.text)
    await update.message.reply_text("✅ Registrado!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Menu", callback_data='back')]])); return MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("🚫 Cancelado."); return MENU

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={MENU: [CallbackQueryHandler(menu_handler)], ADD_VALOR: [MessageHandler(filters.TEXT, receive_valor)], ADD_ODD: [MessageHandler(filters.TEXT, receive_odd)], ADD_DESC: [MessageHandler(filters.TEXT, receive_desc)]},
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)]
    ))
    print("Bot Apostas V4 Rodando...")
    app.run_polling(drop_pending_updates=True)
