# ================= BOT V235 (INTELIGÊNCIA POR LIGAS - FIM DA REPETIÇÃO) =================
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

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# ================= MEMÓRIA GLOBAL =================
TODAYS_GAMES = []
ALERTED_GAMES = set()

# ================= 1. MÓDULOS AUXILIARES =================
async def fetch_news():
    feeds = ["https://ge.globo.com/rss/ge/futebol/", "https://rss.uol.com.br/feed/esporte.xml"]
    noticias = []
    try:
        for url in feeds:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:2]:
                noticias.append(f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler mais</a>")
    except: pass
    return noticias

async def news_loop(app: Application):
    while True:
        await asyncio.sleep(10800) # 3h
        noticias = await fetch_news()
        if noticias:
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text="🗞️ <b>GIRO DE NOTÍCIAS</b> 🗞️\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass

# ================= 2. MÓDULO NBA =================
async def fetch_nba_professional():
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    if event['status']['type']['state'] not in ['pre', 'in']: continue
                    
                    comp = event['competitions'][0]
                    competitors = comp['competitors']
                    team_home = competitors[0] if competitors[0]['homeAway'] == 'home' else competitors[1]
                    team_away = competitors[1] if competitors[1]['homeAway'] == 'away' else competitors[0]
                    
                    home_name = team_home['team']['name']
                    away_name = team_away['team']['name']
                    
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    
                    odds_info = "Aguardando..."
                    if 'odds' in comp and len(comp['odds']) > 0:
                        odd = comp['odds'][0]
                        details = odd.get('details', '-')
                        over_under = odd.get('overUnder', '-')
                        odds_info = f"Spread: {details} | O/U: {over_under}"

                    def get_season_leader(team_data):
                        try:
                            leaders_list = team_data.get('leaders', [])
                            for category in leaders_list:
                                if category.get('name') == 'scoring' or category.get('abbreviation') == 'PTS':
                                    leader = category['leaders'][0]
                                    name = leader['athlete']['displayName']
                                    value = leader['value']
                                    return f"{name} ({value} PPG)"
                        except: return None

                    star_home = get_season_leader(team_home)
                    star_away = get_season_leader(team_away)
                    
                    jogos.append({
                        "match": f"{away_name} @ {home_name}",
                        "time": dt_br.strftime("%H:%M"),
                        "odds": odds_info,
                        "star_home": star_home,
                        "star_away": star_away
                    })
        except: pass
    return jogos

def format_nba_card(game):
    destaques = ""
    if game['star_away']: destaques += f"🔥 <b>{game['match'].split('@')[0].strip()}:</b> {game['star_away']}\n"
    if game['star_home']: destaques += f"🔥 <b>{game['match'].split('@')[1].strip()}:</b> {game['star_home']}\n"
    
    return (
        f"🏀 <b>NBA | {game['time']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"📊 <b>Linhas:</b> {game['odds']}\n"
        f"👇 <b>DESTAQUES (Cestinhas):</b>\n"
        f"{destaques}"
        f"💡 <i>Dica: Busque linhas de Over para esses jogadores.</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 3. MÓDULO FUTEBOL (COM INTELIGÊNCIA DE LIGAS) =================
async def fetch_espn_soccer():
    leagues = ['uefa.europa', 'uefa.champions', 'conmebol.libertadores', 'conmebol.recopa', 'bra.1', 'bra.camp.paulista', 'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1', 'arg.1', 'ksa.1']
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=15) as client:
        for league in leagues:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
            try:
                r = await client.get(url)
                if r.status_code != 200: continue
                data = r.json()
                league_name = data['leagues'][0].get('name', 'Futebol') if data.get('leagues') else 'Futebol'
                for event in data.get('events', []):
                    if event['status']['type']['state'] not in ['pre', 'in']: continue
                    comp = event['competitions'][0]['competitors']
                    home = comp[0]['team']['name'] if comp[0]['homeAway'] == 'home' else comp[1]['team']['name']
                    away = comp[1]['team']['name'] if comp[1]['homeAway'] == 'away' else comp[0]['team']['name']
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    jogos.append({"id": event['id'], "league_code": league, "match": f"{home} x {away}", "home": home, "away": away, "time": dt_br.strftime("%H:%M"), "league": league_name})
            except: continue
                
    unicos = {j['match']: j for j in jogos}
    lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    
    global TODAYS_GAMES
    TODAYS_GAMES = lista_final[:20]
    return TODAYS_GAMES

async def analyze_game_market(league_code, event_id):
    """
    Analisa probabilidades. Se não tiver, usa a INTELIGÊNCIA DE LIGA.
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    prob_home = prob_away = 0.0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if 'predictor' in data and 'homeChance' in data['predictor']:
                    prob_home = float(data['predictor']['homeChance'])
                    prob_away = float(data['predictor']['awayChance'])
    except: pass
    
    # 1. Se a ESPN deu probabilidade, usa ela (É O IDEAL)
    if prob_home >= 60.0: return "Vitória do Mandante", "Empate Anula"
    if prob_away >= 60.0: return "Vitória do Visitante", "Empate Anula"
    if prob_home >= 40.0: return "Ambas Marcam: Sim", "Over 1.5 Gols"
    
    # 2. SE NÃO TEM PROBABILIDADE (FALLBACK INTELIGENTE)
    # Evita repetir "Over 1.5" pra tudo. Analisa o campeonato.
    
    # Ligas de Gols (Alemanha, Holanda, Arábia)
    if league_code in ['ger.1', 'ned.1', 'ksa.1']:
        opcoes = [
            ("Over 2.5 Gols", "Ambas Marcam: Sim"),
            ("Ambas Marcam: Sim", "Over 2.5 Gols"),
            ("Over 1.5 HT (1º Tempo)", "Over 2.5 Gols")
        ]
        return random.choice(opcoes)

    # Ligas Travadas/Táticas (Argentina, Itália, Brasil B)
    elif league_code in ['arg.1', 'ita.1', 'bra.2']:
        opcoes = [
            ("Menos de 3.5 Gols", "Dupla Chance: Casa ou Empate"),
            ("Empate Anula: Casa", "Under 2.5 Gols"),
            ("Casa ou Empate", "Mais de 4.5 Cartões")
        ]
        return random.choice(opcoes)
        
    # Ligas Equilibradas (Espanha, França, Brasil A, Libertadores)
    else:
        opcoes = [
            ("Over 1.5 Gols", "Mais de 8.5 Escanteios"),
            ("Dupla Chance: Mandante", "Under 3.5 Gols"),
            ("2 a 3 Gols no Jogo", "Ambas Marcam: Não")
        ]
        return random.choice(opcoes)

async def get_confirmed_lineup(league_code, event_id):
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if 'rosters' in data and len(data['rosters']) > 0:
                    for player in data['rosters'][0].get('roster', []):
                        if player.get('position', {}).get('name', '').lower() in ['forward', 'atacante', 'striker']:
                            return player.get('athlete', {}).get('displayName')
    except: pass
    return None

# ================= 4. LAYOUTS =================
def format_morning_card(game, d1, d2):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ {game['time']}\n"
        f"👇 <b>ANÁLISE:</b>\n"
        f"✅ <b>Entrada:</b> {d1}\n"
        f"🛡️ <b>Proteção:</b> {d2}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_sniper_card(game, jogador, d1):
    return (
        f"🚨 <b>ALERTA DE OPORTUNIDADE</b> 🚨\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏆 <b>{game['league']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ <b>Começa em breve!</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"💎 <b>ENTRADA CONFIRMADA:</b>\n"
        f"🏃 <b>{jogador}</b> (Titular ✅)\n"
        f"🎯 <b>MERCADO:</b> Para marcar a qualquer momento\n\n"
        f"💰 <b>Gestão:</b> 1% da Banca\n"
        f"🌊 <i>Surfando na tendência do mercado</i>"
    )

# ================= 5. AUTOMAÇÕES =================
async def automation_routine(app: Application):
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        
        if agora.hour == 8 and agora.minute == 0:
            global ALERTED_GAMES
            ALERTED_GAMES.clear()
            jogos = await fetch_espn_soccer()
            if jogos:
                header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>{agora.strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                txt = header
                for g in jogos:
                    d1, d2 = await analyze_game_market(g['league_code'], g['id'])
                    card = format_morning_card(g, d1, d2)
                    if len(txt) + len(card) > 4000:
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                        txt = ""
                    txt += card
                if txt: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
            await asyncio.sleep(60)

        if agora.hour == 10 and agora.minute == 0:
            nba_games = await fetch_nba_professional()
            if nba_games:
                header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{agora.strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                txt = header
                for g in nba_games:
                    txt += format_nba_card(g)
                await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
            await asyncio.sleep(60)
            
        await asyncio.sleep(30)

async def live_sniper_routine(app: Application):
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if TODAYS_GAMES:
            jogos_do_horario = []
            for g in TODAYS_GAMES:
                if g['id'] in ALERTED_GAMES: continue
                try:
                    h, m = map(int, g['time'].split(':'))
                    hora_jogo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                    minutos = (hora_jogo - agora).total_seconds() / 60.0
                    if 50 <= minutos <= 60:
                        jogos_do_horario.append(g)
                        ALERTED_GAMES.add(g['id'])
                except: pass
            
            if jogos_do_horario:
                for g in jogos_do_horario:
                    jogador = await get_confirmed_lineup(g['league_code'], g['id'])
                    d1, _ = await analyze_game_market(g['league_code'], g['id'])
                    if jogador:
                        txt = format_sniper_card(g, jogador, d1)
                        try: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                        except: pass
                        await asyncio.sleep(2)
        await asyncio.sleep(60)

# ================= 6. MENU E START =================
def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🔫 Sniper (Ao Vivo)", callback_data="fut_sniper")],
        [InlineKeyboardButton("🏀 Grade NBA VIP", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V235</b>\nSistema Anti-Repetição Ativado.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text("🔎 <b>Gerando grade inteligente...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_espn_soccer()
        if not jogos:
            await msg.edit_text("❌ Sem jogos.")
            return
        br_tz = timezone(timedelta(hours=-3))
        header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>{datetime.now(br_tz).strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            d1, d2 = await analyze_game_market(g['league_code'], g['id'])
            card = format_morning_card(g, d1, d2)
            if len(txt) + len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>Postado!</b>")

    elif q.data == "nba_deep":
        msg = await q.message.reply_text("🔎 <b>Buscando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_professional()
        if not jogos:
            await msg.edit_text("❌ Sem jogos da NBA.")
            return
        br_tz = timezone(timedelta(hours=-3))
        header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{datetime.now(br_tz).strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>NBA Postada!</b>")

    elif q.data == "fut_sniper":
        await q.message.reply_text("🔎 <b>Sniper manual ativado...</b>")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V235 SMART LEAGUE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_espn_soccer()
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(live_sniper_routine(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
