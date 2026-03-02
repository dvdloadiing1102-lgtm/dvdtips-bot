# ================= BOT V306 (MODO SIMULADOR: FORÇA VISUALIZAÇÃO DA GRADE) =================
import os
import logging
import asyncio
import threading
import httpx
import feedparser
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Conflict, NetworkError

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

VIP_TEAMS = [
    "Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Santos", "Grêmio", 
    "Internacional", "Atlético-MG", "Cruzeiro", "Botafogo", "Fluminense", "Vasco",
    "Bahia", "Fortaleza", "Athletico-PR", "Sport", "Ceará", "Vitória", "Remo", "Paysandu",
    "Arsenal", "Liverpool", "Man City", "Real Madrid", "Barcelona", "Bayern", "Inter", "Milan", "Juventus", "PSG", "Chelsea",
    "Dortmund", "Benfica", "Porto", "Napoli", "Roma", "Lazio", "Atletico Madrid"
]

TODAYS_GAMES = []
TODAYS_NBA = []
GAME_MEMORY = {}

# --- 2. HELPERS ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="ERRO:", exc_info=context.error)

def get_magic_api_date():
    """DATA DE 2025 (DATA REAL ONDE OS JOGOS EXISTEM)"""
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    if agora.hour < 5: agora = agora - timedelta(days=1)
    try: data = agora.replace(year=2025)
    except: data = agora.replace(year=2025, day=28)
    return data

def get_visual_date_str():
    """DATA DE 2026 (VISUAL)"""
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    return agora.strftime("%d/%m/%Y")

def get_market_analysis(home, away, league_name):
    h_weight = 50; a_weight = 35
    if any(t in home for t in VIP_TEAMS): h_weight += 20
    if any(t in away for t in VIP_TEAMS): a_weight += 15
    
    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    random.seed(len(home) + len(away) + int(datetime.now().day))
    ph += random.randint(-5, 5); pa += random.randint(-5, 5)
    confidence = min(max(ph, pa), 90)
    
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    if ph >= 60:
        pick = f"Vitória do {home}"; odd = round(100/ph + 0.15, 2)
        narrativa = f"O {home} é favorito em casa."; icon = "🏠"; extra = f"Over 1.5 Gols"
    elif pa >= 58:
        pick = f"Vitória do {away}"; odd = round(100/pa + 0.15, 2)
        narrativa = f"O {away} tem elenco superior."; icon = "🔥"; extra = "Empate Anula: Visitante"
    else:
        odd = 1.85
        league_lower = league_name.lower()
        if any(x in league_lower for x in ['premier', 'bundesliga', 'champions']):
            pick = "Over 2.5 Gols"; narrativa = "Jogo aberto."; icon = "⚽"; extra = "Ambas Marcam: Sim"
        elif any(x in league_lower for x in ['brasil', 'libertadores', 'la liga']):
            pick = "Empate ou Casa"; narrativa = "Jogo truncado."; icon = "🛡️"; extra = "Over 5.5 Cartões"
        else:
            pick = "Ambas Marcam: Sim"; narrativa = "Defesas instáveis."; icon = "🥅"; extra = "Over 1.5 Gols"

    return pick, extra, narrativa, f"{conf_bar} {int(confidence)}%", odd, icon

def format_card(game):
    pick, extra, narrativa, conf, odd, icon = get_market_analysis(game['home'], game['away'], game['league'])
    return (
        f"{game['league']} | ⏰ {game['time']}\n"
        f"⚽ <b>{game['match']}</b>\n"
        f"📝 <i>{narrativa}</i>\n"
        f"{icon} <b>Palpite: {pick}</b>\n"
        f"🛡️ Extra: {extra}\n"
        f"📊 Confiança: {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    return f"🏀 <b>NBA | {game['time']}</b>\n⚔️ <b>{game['match']}</b>\n✅ {game['pick']}\n📊 {game['odds']}\n━━━━━━━━━━━━━━━━━━━━\n"

# --- 3. MOTOR DE BUSCA (ESPN UNIFICADA) ---
async def fetch_espn_data():
    data_api = get_magic_api_date()
    data_str = data_api.strftime('%Y%m%d')
    logger.info(f"🔍 BUSCANDO NA DATA REAL (2025): {data_str}")

    leagues = {
        'bra.1': '🇧🇷 Brasileirão', 'bra.copa_do_brasil': '🏆 Copa do Brasil',
        'bra.camp.paulista': '🇧🇷 Paulistão', 'bra.camp.carioca': '🇧🇷 Carioca',
        'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
        'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'
    }
    
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=25) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={data_str}"
                r = await client.get(url)
                if r.status_code != 200: continue
                data = r.json()
                
                for event in data.get('events', []):
                    # --- CORREÇÃO DO BUG VISUAL ---
                    # Como pegamos dados de 2025, o status real é 'post' (finalizado).
                    # Mas para o SIMULADOR de 2026, forçamos 'agendado' para aparecer na lista.
                    status = 'agendado' 
                    
                    comp = event['competitions'][0]['competitors']
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    home = comp[0]['team']['name']; away = comp[1]['team']['name']

                    # Filtro Estadual (Só times VIP)
                    if 'bra' in code:
                        if not (any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)):
                            continue 
                    
                    jogos.append({
                        "id": event['id'],
                        "match": f"{home} x {away}", "home": home, "away": away,
                        "time": dt.strftime("%H:%M"), "league": name,
                        "status": status, # Agora é sempre agendado visualmente
                        "score_home": int(comp[0]['score']), "score_away": int(comp[1]['score'])
                    })
            except: pass
    
    jogos.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = jogos
    logger.info(f"📊 {len(jogos)} jogos carregados para exibição.")
    return jogos

async def fetch_nba_professional():
    data_api = get_magic_api_date()
    date_str = data_api.strftime("%Y%m%d")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    jogos.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"),
                        "pick": f"Vitória do {t_home['team']['name']}",
                        "odds": f"Spread: {odds}"
                    })
    except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

# --- 4. ROTINAS E MENU ---

async def automation_routine(app):
    while True:
        now = datetime.now(timezone(timedelta(hours=-3)))
        if now.hour == 8 and now.minute == 0:
            await fetch_espn_data()
            if TODAYS_GAMES:
                txt = f"🦁 <b>BOM DIA! GRADE VIP | {get_visual_date_str()}</b> 🦁\n\n"
                for g in TODAYS_GAMES:
                    card = format_card(g) # Sem filtro de status, mostra tudo
                    if len(txt)+len(card) > 4000:
                        await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                    txt += card
                if txt: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
            await asyncio.sleep(65)
        await asyncio.sleep(30)

async def news_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                msg = f"🌍 <b>NOTÍCIA URGENTE</b>\n\n📰 {entry.title}\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass
        await asyncio.sleep(14400)

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete Pronto", callback_data="ticket")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V306 (SIMULADOR)</b>\nModo 2026 Ativo.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "fut":
        msg = await q.message.reply_text("🔎 Carregando grade simulada...")
        await fetch_espn_data()
        if not TODAYS_GAMES:
            await msg.edit_text("❌ Nenhum jogo encontrado na base de dados.")
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_visual_date_str()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            # REMOVIDO O FILTRO 'if status != post' PARA FORÇAR EXIBIÇÃO
            card = format_card(g)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "ticket":
        await fetch_espn_data()
        if not TODAYS_GAMES:
            await q.message.reply_text("❌ Sem dados.")
            return
        cands = TODAYS_GAMES[:] # Copia lista
        random.shuffle(cands)
        msg = "🎫 <b>BILHETE PRONTO</b> 🎫\n\n"
        total_odd = 1.0
        for g in cands[:3]:
            p, _, _, _, o, _ = get_market_analysis(g['home'], g['away'], g['league'])
            total_odd *= o
            msg += f"✅ <b>{g['match']}</b>\n🎯 {p} @ {o}\n\n"
        msg += f"🔥 <b>ODD FINAL: {total_odd:.2f}</b>"
        await c.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        msg = await q.message.reply_text("🏀 Buscando NBA...")
        jogos = await fetch_nba_professional()
        if not jogos:
            await msg.edit_text("❌ Sem jogos da NBA.")
            return
        txt = f"🏀 <b>NBA | {get_visual_date_str()}</b>\n\n"
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V306 SIMULATOR")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V306...")
    await fetch_espn_data()
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
