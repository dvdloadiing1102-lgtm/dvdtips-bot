# ================= BOT V314 (ULTIMATE: DADOS COMPLETOS + MERCADOS VARIADOS) =================
import os
import logging
import asyncio
import threading
import httpx
import html
import random
import feedparser
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults

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
    "Dortmund", "Benfica", "Porto", "Napoli", "Roma", "Lazio", "Atletico Madrid", "Boca Juniors", "River Plate"
]

TODAYS_GAMES = []
TODAYS_NBA = []
GAME_MEMORY = {}

# --- 2. HELPERS ---

def get_real_server_date():
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz)

def get_display_date():
    return get_real_server_date().strftime("%d/%m/%Y")

def get_api_date_str():
    return get_real_server_date().strftime("%Y%m%d")

def safe_html(text):
    if not text: return ""
    return html.escape(str(text))

# --- 3. ANÁLISE DE MERCADO (AGORA COM CANTOS E CARTÕES) ---

def get_market_analysis(game):
    home = game['home']
    away = game['away']
    league_lower = game['league'].lower()
    
    # Pesos Base
    h_weight = 50; a_weight = 35
    if any(t in home for t in VIP_TEAMS): h_weight += 20
    if any(t in away for t in VIP_TEAMS): a_weight += 15
    
    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    # Fator Aleatório (Simula análise técnica do dia)
    random.seed(len(home) + len(away) + int(datetime.now().day))
    ph += random.randint(-5, 5); pa += random.randint(-5, 5)
    
    confidence = min(max(ph, pa), 95)
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    # LÓGICA DE ESCOLHA DE MERCADO
    pick = ""; icon = ""; extra = ""
    odd = 1.90

    # 1. Favorito Claro (Vitória Seca)
    if ph >= 65:
        pick = f"Vitória do {home}"; odd = round(100/ph + 0.15, 2); icon = "🏠"
        extra = f"Over 1.5 Gols"
    elif pa >= 63:
        pick = f"Vitória do {away}"; odd = round(100/pa + 0.15, 2); icon = "🔥"
        extra = "Empate Anula: Visitante"
        
    # 2. Jogos Equilibrados (Variedade)
    else:
        # INGLATERRA/ALEMANHA -> Gols ou Escanteios
        if any(x in league_lower for x in ['premier', 'bundesliga', 'champions', 'england']):
            dice = random.randint(1, 10)
            if dice > 6:
                pick = "Over 9.5 Escanteios"; icon = "🚩"; extra = "Over 4.5 Cantos HT"
            else:
                pick = "Ambas Marcam: Sim"; icon = "⚽"; extra = "Over 2.5 Gols"
        
        # BRASIL/LATAM/ESPANHA -> Under ou Cartões (Se for clássico)
        elif any(x in league_lower for x in ['brasil', 'carioca', 'paulista', 'libertadores', 'la liga', 'serie a', 'argentina']):
            # É Clássico? (Dois times VIPs)
            is_derby = any(t in home for t in VIP_TEAMS) and any(t in away for t in VIP_TEAMS)
            
            if is_derby:
                pick = "Over 5.5 Cartões"; icon = "🟨"; extra = "Expulsão: Sim (Risco)"
            else:
                pick = "Menos de 2.5 Gols"; icon = "🛡️"; extra = "Empate no 1º Tempo"
        
        # RESTO
        else:
            pick = "Empate"; icon = "⚖️"; extra = "Ambas Marcam: Sim"

    return safe_html(pick), safe_html(extra), conf_bar, odd, icon

def format_card(game):
    pick, extra, conf, odd, icon = get_market_analysis(game)
    tv_str = f"📺 {game['tv']}" if game['tv'] else "📺 Sem transmissão"
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    
    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ <i>{safe_html(game['venue'])}</i>\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"{icon} <b>Palpite: {pick}</b>\n"
        f"🛡️ Extra: {extra}\n"
        f"📊 Probabilidade: {conf} {int(odd*10)}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    return (
        f"🏀 <b>NBA | {game['clock']}</b>\n"
        f"⚔️ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"✅ {safe_html(game['pick'])}\n"
        f"📊 {safe_html(game['odds'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# --- 4. MOTORES DE BUSCA (FULL EXTRACT) ---

async def fetch_espn_data():
    date_str = get_api_date_str()
    logger.info(f"🔍 BUSCANDO DADOS COMPLETOS: {date_str}")
    
    leagues = {
        'bra.1': '🇧🇷 Brasileirão', 'bra.copa_do_brasil': '🏆 Copa do Brasil',
        'bra.camp.paulista': '🇧🇷 Paulistão', 'bra.camp.carioca': '🇧🇷 Carioca',
        'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
        'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'
    }
    
    found_games = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        status_key = event['status']['type']['state']
                        display_clock = event['status']['type']['detail']
                        
                        if status_key == 'pre': status = 'agendado'; display_clock = "Agendado"
                        elif status_key == 'in': status = 'in'
                        else: status = 'post'; display_clock = "Finalizado"
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        sh = int(comp[0]['score']); sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', 'Estádio não inf.')
                        
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                        tv_str = ", ".join(tv_channels) if tv_channels else ""
                        
                        if 'bra' in code:
                            if not (any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)):
                                continue
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        found_games.append({
                            "id": event['id'],
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status,
                            "clock": display_clock,
                            "venue": venue,
                            "tv": tv_str,
                            "score_home": sh, "score_away": sa
                        })
            except Exception: pass

    found_games.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = found_games
    return found_games

async def fetch_nba_professional():
    date_str = get_api_date_str()
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    display_clock = event['status']['type']['detail']
                    
                    broadcasts = comp.get('broadcasts', [])
                    tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                    tv_str = ", ".join(tv_channels) if tv_channels else ""

                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    
                    jogos.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"),
                        "clock": display_clock,
                        "tv": tv_str,
                        "pick": f"Vitória do {t_home['team']['name']}",
                        "odds": f"Spread: {odds}"
                    })
    except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

# --- 5. ROTINAS ---

async def automation_routine(app):
    while True:
        now = datetime.now(timezone(timedelta(hours=-3)))
        
        # 08:00 - FUTEBOL
        if now.hour == 8 and now.minute == 0:
            await fetch_espn_data()
            if TODAYS_GAMES:
                txt = f"🦁 <b>BOM DIA! GRADE VIP | {get_display_date()}</b> 🦁\n\n"
                for g in TODAYS_GAMES:
                    if g['status'] != 'post':
                        card = format_card(g)
                        if len(txt)+len(card) > 4000:
                            try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                            except: pass
                            txt = ""
                        txt += card
                if txt: 
                    try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                    except: pass
            await asyncio.sleep(65)

        # 16:00 - NBA
        elif now.hour == 16 and now.minute == 0:
            await fetch_nba_professional()
            if TODAYS_NBA:
                txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
                for g in TODAYS_NBA:
                    card = format_nba_card(g)
                    if len(txt)+len(card) > 4000:
                        try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                        except: pass
                        txt = ""
                    txt += card
                if txt:
                    try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                    except: pass
            await asyncio.sleep(65)

        await asyncio.sleep(30)

async def live_narrator_routine(app):
    global GAME_MEMORY
    logger.info("🎙️ Narrador Ativo")
    while True:
        await asyncio.sleep(60)
        try:
            current_games = await fetch_espn_data()
            for game in current_games:
                gid = game['id']; status = game['status']
                if gid not in GAME_MEMORY:
                    GAME_MEMORY[gid] = {'h': game['score_home'], 'a': game['score_away'], 'status': status}
                    continue
                old = GAME_MEMORY[gid]
                
                # Gol
                if status == 'in' and (game['score_home'] > old['h'] or game['score_away'] > old['a']):
                    scorer = game['home'] if game['score_home'] > old['h'] else game['away']
                    msg = f"⚽ <b>GOOOOOOL DO {scorer.upper()}!</b>\n\n🏟️ {game['match']}\n⏱️ {game['clock']}\n🔢 Placar: {game['score_home']} - {game['score_away']}\n🏆 {game['league']}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                # Green/Red
                if status == 'post' and old['status'] == 'in':
                    pick, _, _, _, _ = get_market_analysis(game)
                    is_green = False
                    sh = game['score_home']; sa = game['score_away']
                    
                    if "Vitória do" in pick:
                        if game['home'] in pick and sh > sa: is_green = True
                        elif game['away'] in pick and sa > sh: is_green = True
                    elif "Ambas" in pick and sh > 0 and sa > 0: is_green = True
                    elif "Menos" in pick and (sh+sa) < 2.5: is_green = True
                    elif "Over" in pick and (sh+sa) > 2.5: is_green = True
                    elif "Empate" in pick and sh == sa: is_green = True
                    
                    res_icon = "✅ GREEN" if is_green else "❌ RED"
                    msg = f"{res_icon} <b>FINALIZADO</b>\n\n⚽ {game['match']}\n🔢 Placar Final: {sh} - {sa}\n🎯 Tip: {pick}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass

                GAME_MEMORY[gid] = {'h': game['score_home'], 'a': game['score_away'], 'status': status}
        except: pass

async def news_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                msg = f"🌍 <b>GIRO DE NOTÍCIAS</b>\n\n📰 {safe_html(entry.title)}\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass
        await asyncio.sleep(14400)

# --- 6. MENU E EXECUÇÃO ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="ERRO:", exc_info=context.error)

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete Pronto", callback_data="ticket")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(f"🦁 <b>PAINEL V314 (ULTIMATE)</b>\nMercados Variados: ON\nDados Completos: ON", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 Analisando grade completa...")
        await fetch_espn_data()
        
        if not TODAYS_GAMES:
            await msg.edit_text(f"❌ <b>ZERO JOGOS CONFIRMADOS PARA {get_display_date()}.</b>", parse_mode=ParseMode.HTML)
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "nba":
        msg = await q.message.reply_text("🏀 Buscando NBA...")
        jogos = await fetch_nba_professional()
        if not jogos:
            await msg.edit_text(f"❌ Sem NBA hoje.")
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()
        
    elif q.data == "ticket":
        await fetch_espn_data()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        if not cands:
            await q.message.reply_text("❌ Sem jogos.")
            return
        random.shuffle(cands)
        msg = "🎫 <b>BILHETE PRONTO</b> 🎫\n\n"
        total_odd = 1.0
        for g in cands[:3]:
            p, _, _, _, o, _ = get_market_analysis(g)
            total_odd *= o
            msg += f"✅ <b>{g['match']}</b>\n🎯 {p} @ {o}\n\n"
        msg += f"🔥 <b>ODD FINAL: {total_odd:.2f}</b>"
        await c.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V314 ULTIMATE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V314...")
    await fetch_espn_data()
    asyncio.create_task(live_narrator_routine(app))
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
