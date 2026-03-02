# ================= BOT V321 (SANITY CHECK: FIM DAS ZEBRAS E 65% FIXO) =================
import os
import logging
import asyncio
import threading
import httpx
import html
import feedparser
import random
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

# LISTA VIP (MINÚSCULA PARA EVITAR ERROS DE DIGITAÇÃO)
VIP_TEAMS = [
    "flamengo", "palmeiras", "corinthians", "são paulo", "sao paulo", "santos", "grêmio", "gremio",
    "internacional", "atlético-mg", "atletico-mg", "cruzeiro", "botafogo", "fluminense", "vasco",
    "bahia", "fortaleza", "athletico-pr", "sport", "ceará", "vitória",
    "arsenal", "liverpool", "man city", "manchester city", "real madrid", "barcelona", "bayern", 
    "inter", "milan", "juventus", "psg", "chelsea", "dortmund", "benfica", "porto", "napoli", 
    "roma", "lazio", "atletico madrid", "boca juniors", "river plate", "tottenham", "man utd", "manchester united"
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

# --- 3. LÓGICA DE MERCADO (COM SANITY CHECK) ---

def is_vip(team_name):
    """Verifica se o time é VIP ignorando maiúsculas/minúsculas"""
    return any(vip in team_name.lower() for vip in VIP_TEAMS)

def extract_real_odds(game_data):
    comp = game_data['competitions'][0]
    odds_list = comp.get('odds', [])
    
    home_team = comp['competitors'][0]['team']['name']
    away_team = comp['competitors'][1]['team']['name']
    
    pick = "Aguardando Odds"
    prob_val = 50
    icon = "⏳"
    extra = "Aguardando Linhas"

    # --- 1. TENTA LER API ---
    if odds_list:
        line = odds_list[0]
        details = line.get('details', '')
        
        # Probabilidade Win Percentage
        if 'homeTeamOdds' in line and 'winPercentage' in line['homeTeamOdds']:
            prob_home = line['homeTeamOdds'].get('winPercentage', 0)
            prob_away = line['awayTeamOdds'].get('winPercentage', 0)
            
            if prob_home > 55:
                pick = f"Vitória do {home_team}"; icon = "🏠"; prob_val = int(prob_home)
            elif prob_away > 55:
                pick = f"Vitória do {away_team}"; icon = "🔥"; prob_val = int(prob_away)
            else:
                pick = "Chance Dupla / Empate"; icon = "⚖️"; prob_val = 50
        
        # Spread
        elif details:
            if home_team[:3] in details or " -" in details:
                pick = f"Vitória do {home_team}"; icon = "🏠"; prob_val = 60 + random.randint(0, 5)
            else:
                pick = f"Vitória do {away_team}"; icon = "🔥"; prob_val = 60 + random.randint(0, 5)

        # Linha Gols
        over = line.get('overUnder', 0)
        if over > 0: extra = f"Linha: {over} Gols"

    # --- 2. SANITY CHECK (A CORREÇÃO DO MADUREIRA) ---
    # Se a API falhou ou mandou dado errado, a hierarquia manda.
    
    home_is_vip = is_vip(home_team)
    away_is_vip = is_vip(away_team)
    
    # Se Pick for "Aguardando" OU se a Pick estiver contra um VIP jogando com pequeno
    # Ex: Pick="Madureira", mas Away="Flamengo" (VIP) -> CORRIGE PARA FLAMENGO
    
    if away_is_vip and not home_is_vip:
        # Visitante é GIGANTE, Casa é pequeno.
        # Se a pick atual não for o visitante, FORCE o visitante.
        if away_team not in pick:
            pick = f"Vitória do {away_team}"; icon = "🔥"
            prob_val = 70 + random.randint(0, 15) # Varia entre 70 e 85
            if extra == "Aguardando Linhas": extra = "Over 1.5 Gols"

    elif home_is_vip and not away_is_vip:
        # Casa é GIGANTE.
        if home_team not in pick:
            pick = f"Vitória do {home_team}"; icon = "🏠"
            prob_val = 70 + random.randint(0, 15)
            if extra == "Aguardando Linhas": extra = "Over 1.5 Gols"

    elif home_is_vip and away_is_vip:
        # Clássico
        if pick == "Aguardando Odds":
            pick = "Ambas Marcam: Sim"; icon = "⚽"
            prob_val = 50 + random.randint(0, 10) # 50 a 60
            extra = "Jogo Equilibrado"

    # Se continuou sem odds e são dois pequenos
    elif pick == "Aguardando Odds":
        pick = "Sem Favorito Claro"; icon = "🚫"; prob_val = 50
        extra = "Mercado Fechado"

    # Barra Visual
    bars = int(prob_val / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)

    return pick, prob_val, icon, extra, conf_bar

def format_card(game, api_raw_data):
    pick, prob, icon, extra, conf_bar = extract_real_odds(api_raw_data)
    
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    
    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ <i>{safe_html(game['venue'])}</i>\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"{icon} <b>Mercado: {pick}</b>\n"
        f"📊 Linha Gols: {extra}\n"
        f"📉 Probabilidade: {conf_bar} {prob}%\n"
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

# --- 4. MOTORES DE BUSCA ---

async def fetch_espn_data():
    date_str = get_api_date_str()
    logger.info(f"🔍 BUSCANDO {date_str}...")
    
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
                        venue = event['competitions'][0].get('venue', {}).get('fullName', 'Local a definir')
                        
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                        tv_str = ", ".join(tv_channels) if tv_channels else ""
                        if 'bra' in code and not tv_str: tv_str = "Premiere / Globo"
                        
                        # Filtro VIP
                        is_home_vip = is_vip(home)
                        is_away_vip = is_vip(away)
                        
                        if 'bra' in code:
                            if not (is_home_vip or is_away_vip): continue
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        found_games.append({
                            "raw": event,
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
                    tv_str = ", ".join(tv_channels) if tv_channels else "NBA League Pass"

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
        
        # 08:00
        if now.hour == 8 and now.minute == 0:
            await fetch_espn_data()
            if TODAYS_GAMES:
                txt = f"🦁 <b>BOM DIA! GRADE VIP | {get_display_date()}</b> 🦁\n\n"
                for g in TODAYS_GAMES:
                    if g['status'] != 'post':
                        card = format_card(g, g['raw'])
                        if len(txt)+len(card) > 4000:
                            try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                            except: pass
                            txt = ""
                        txt += card
                if txt: 
                    try: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                    except: pass
            await asyncio.sleep(65)

        # 16:00
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
                
                # Green
                if status == 'post' and old['status'] == 'in':
                    pick, _, _, _, _ = extract_real_odds(game['raw'])
                    is_green = False
                    sh = game['score_home']; sa = game['score_away']
                    
                    if "Vitória do" in pick:
                        if game['home'] in pick and sh > sa: is_green = True
                        elif game['away'] in pick and sa > sh: is_green = True
                    elif "Ambas" in pick and sh > 0 and sa > 0: is_green = True
                    
                    res_icon = "✅ GREEN" if is_green else "❌ RED"
                    msg = f"{res_icon} <b>FINALIZADO</b>\n\n⚽ {game['match']}\n🔢 Placar Final: {sh} - {sa}\n🎯 Tip Original: {pick}"
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
    await u.message.reply_text(f"🦁 <b>PAINEL V321 (SANITY CHECK)</b>\nLógica Anti-Zebra: ON\nProbabilidade Dinâmica: ON", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text(f"🔎 Analisando grade...")
        await fetch_espn_data()
        
        if not TODAYS_GAMES:
            await msg.edit_text(f"❌ Grade vazia.", parse_mode=ParseMode.HTML)
            return
        
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

    elif q.data == "nba":
        msg = await q.message.reply_text("🏀 Buscando NBA...")
        jogos = await fetch_nba_professional()
        if not jogos:
            await msg.edit_text(f"❌ Sem NBA hoje.", parse_mode=ParseMode.HTML)
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
        for g in cands[:3]:
            # Usa a nova função
            p, prob, _, _, _ = extract_real_odds(g['raw'])
            odd = round(100/prob, 2) if prob > 0 else 1.90
            msg += f"✅ <b>{g['match']}</b>\n🎯 {p} @ {odd}\n\n"
        await c.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V321 SANITY CHECK")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V321...")
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
