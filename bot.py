# ================= BOT V252 (FINAL: FUTEBOL 2026 + NBA COM NARRATIVA ANALÍTICA) =================
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

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= CONFIGURAÇÃO CRÍTICA =================
DATA_ALVO = "20260220" # Data Travada para Simulação

# ================= MEMÓRIA GLOBAL =================
TODAYS_GAMES = []
PROCESSED_GAMES = set()
ALERTED_SNIPER = set()
ALERTED_LIVE = set()
DAILY_STATS = {"green": 0, "red": 0}

# ================= 1. TRATAMENTO DE ERROS =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# ================= 2. MÓDULOS AUXILIARES =================
async def fetch_news():
    feeds = ["https://ge.globo.com/rss/ge/futebol/", "https://rss.uol.com.br/feed/esporte.xml", "https://www.espn.com.br/rss/nba/news"]
    noticias = []
    try:
        for url in feeds:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:1]:
                noticias.append(f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler matéria</a>")
    except: pass
    return noticias

async def news_loop(app: Application):
    await asyncio.sleep(10)
    while True:
        noticias = await fetch_news()
        if noticias:
            txt = "🌍 <b>GIRO DE NOTÍCIAS</b> 🌍\n\n" + "\n\n".join(noticias)
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
            except: pass
        await asyncio.sleep(14400) # 4h

# ================= 3. MÓDULO NBA (COM NARRATIVA) =================
def generate_nba_narrative(home, away, spread, total):
    """Gera análise baseada nas linhas de Las Vegas"""
    try:
        spread_val = float(spread.split(' ')[1]) if spread != '-' else 0
        total_val = float(total) if total != '-' else 220
    except:
        spread_val = 0; total_val = 220

    analise = ""
    # Lógica de Handicap
    if abs(spread_val) >= 8:
        analise += f"O {home if spread_val < 0 else away} é amplamente favorito. "
    elif abs(spread_val) <= 3:
        analise += "Confronto extremamente equilibrado, deve ser decidido no Clutch Time. "
    else:
        analise += "Duelo interessante com leve vantagem técnica para o favorito. "

    # Lógica de Total
    if total_val >= 235:
        analise += "Expectativa de pontuação altíssima e defesas abertas."
    elif total_val <= 212:
        analise += "Tendência de jogo mais físico e defesas predominando."
    else:
        analise += "Ritmo de jogo deve ficar na média da liga."
    
    return analise

async def fetch_nba_professional():
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={DATA_ALVO}"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            # Backup se falhar a data
            if r.status_code != 200 or not r.json().get('events'):
                r = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
            
            data = r.json()
            for event in data.get('events', []):
                comp = event['competitions'][0]
                t1 = comp['competitors'][0]
                t2 = comp['competitors'][1]
                team_home = t1 if t1['homeAway'] == 'home' else t2
                team_away = t2 if t2['homeAway'] == 'away' else t1
                
                dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                
                odds_str = "Aguardando..."
                spread_val = "-"
                ou_val = "-"
                
                if 'odds' in comp and len(comp['odds']) > 0:
                    odd = comp['odds'][0]
                    spread_val = odd.get('details', '-')
                    ou_val = odd.get('overUnder', '-')
                    odds_str = f"Spread: {spread_val} | O/U: {ou_val}"

                # Pega Lideres
                def get_stats(team_data):
                    try:
                        leaders = team_data.get('leaders', [])
                        # Tenta pegar Cestinha
                        for cat in leaders:
                            if cat['name'] == 'scoring':
                                l = cat['leaders'][0]
                                return f"{l['athlete']['displayName']} ({float(l['value']):.1f} PPG)"
                        return "N/A"
                    except: return "N/A"

                narrativa = generate_nba_narrative(team_home['team']['name'], team_away['team']['name'], spread_val, ou_val)

                jogos.append({
                    "match": f"{team_away['team']['name']} @ {team_home['team']['name']}",
                    "time": dt_br.strftime("%H:%M"),
                    "odds": odds_str,
                    "analise": narrativa,
                    "star_home": get_stats(team_home),
                    "star_away": get_stats(team_away)
                })
        except: pass
    return jogos

def format_nba_card(game):
    return (
        f"🏀 <b>NBA | {game['time']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"📝 <b>Resumo:</b> <i>{game['analise']}</i>\n"
        f"📊 <b>Linhas:</b> {game['odds']}\n"
        f"👇 <b>Destaques (Cestinhas):</b>\n"
        f"🔥 {game['match'].split('@')[1].strip()}: {game['star_home']}\n"
        f"🔥 {game['match'].split('@')[0].strip()}: {game['star_away']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 4. MÓDULO FUTEBOL =================
async def fetch_espn_soccer():
    leagues = [
        'ksa.1', 'ger.1', 'ita.1', 'fra.1', 'esp.1', 'arg.1', 'tur.1', 'por.1', 'ned.1',
        'bra.1', 'bra.camp.paulista', 'eng.1', 'eng.2', 'uefa.europa', 'uefa.champions'
    ]
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={DATA_ALVO}"
            try:
                r = await client.get(url)
                if r.status_code != 200: continue
                data = r.json()
                if not data.get('events'): continue

                league_name = data['leagues'][0].get('name', 'Futebol') if data.get('leagues') else 'Futebol'

                for event in data.get('events', []):
                    state = event['status']['type']['state']
                    comp = event['competitions'][0]['competitors']
                    t_home = comp[0] if comp[0]['homeAway'] == 'home' else comp[1]
                    t_away = comp[1] if comp[1]['homeAway'] == 'away' else comp[0]
                    
                    home = t_home['team']['name']
                    away = t_away['team']['name']
                    score_home = int(t_home['score'])
                    score_away = int(t_away['score'])
                    
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    period = event['status'].get('period', 0)
                    clock = event['status'].get('displayClock', '00:00')
                    
                    jogos.append({
                        "id": event['id'], 
                        "league_code": league, 
                        "match": f"{home} x {away}", 
                        "home": home, 
                        "away": away, 
                        "time": dt_br.strftime("%H:%M"), 
                        "league": league_name,
                        "status": state,
                        "period": period,
                        "clock": clock,
                        "score_home": score_home,
                        "score_away": score_away
                    })
            except: continue
    
    unicos = {j['match']: j for j in jogos}
    lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    
    global TODAYS_GAMES
    TODAYS_GAMES = lista_final
    return TODAYS_GAMES

def generate_soccer_narrative(market_type, home, away):
    random.seed(len(home) + len(away) + len(market_type))
    if "Vitória" in market_type:
        phrases = [
            f"O {home} joga em casa e deve pressionar desde o início.",
            f"Superior tecnicamente, o {home} tem tudo para confirmar o favoritismo.",
            f"O {away} vem oscilando muito e terá dificuldades hoje.",
            "Expectativa de domínio do mandante, aproveitando o fator casa."
        ]
    elif "Over" in market_type or "Ambas" in market_type:
        phrases = [
            f"Tanto {home} quanto {away} possuem ataques muito produtivos.",
            "As duas defesas têm falhado recentemente. Jogo para gols.",
            "Confronto aberto! A necessidade de vitória deve gerar espaços."
        ]
    elif "Under" in market_type or "Empate" in market_type:
        phrases = [
            f"O {away} deve jogar fechado, buscando contra-ataques.",
            "Clássico tenso e com muita marcação no meio-campo.",
            f"O {home} tem uma defesa sólida e deve controlar o ritmo."
        ]
    else:
        phrases = [
            f"O {home} precisa pontuar para subir na tabela.",
            "Confronto direto! O equilíbrio deve prevalecer."
        ]
    return random.choice(phrases)

async def analyze_game_market(league_code, event_id, home, away):
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    extra_info = ""
    prob_home = 0; prob_away = 0
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                try:
                    venue = data.get('gameInfo', {}).get('venue', {}).get('fullName', '')
                    if venue: extra_info += f"🏟️ <b>Estádio:</b> {venue}\n"
                except: pass

                if 'predictor' in data and 'homeChance' in data['predictor']:
                    ph = float(data['predictor']['homeChance'])
                    pa = float(data['predictor']['awayChance'])
                    
                    if ph >= 60.0: 
                        m = f"Vitória do {home}"
                        return m, "Over 1.5 Gols", generate_soccer_narrative(m, home, away), extra_info, ph, pa
                    if pa >= 60.0: 
                        m = f"Vitória do {away}"
                        return m, "Empate Anula: Visitante", generate_soccer_narrative(m, home, away), extra_info, ph, pa
                    if ph >= 40.0: 
                        m = "Ambas Marcam: Sim"
                        return m, "Over 2.5 Gols", generate_soccer_narrative(m, home, away), extra_info, ph, pa
                    else: 
                        m = "Menos de 3.5 Gols"
                        return m, "Empate ou Visitante", generate_soccer_narrative(m, home, away), extra_info, ph, pa
    except: pass
    
    random.seed(int(event_id)) 
    if league_code in ['ger.1', 'ned.1', 'ksa.1', 'tur.1', 'por.1', 'bel.1']:
        m = "Over 2.5 Gols"
        return m, "Ambas Marcam: Sim", generate_soccer_narrative(m, home, away), extra_info, prob_home, prob_away
    elif league_code in ['arg.1', 'ita.1', 'bra.2', 'esp.2']:
        m = "Menos de 2.5 Gols"
        return m, "Dupla Chance: Casa/Empate", generate_soccer_narrative(m, home, away), extra_info, prob_home, prob_away
    else:
        m = "Over 1.5 Gols"
        return m, "Escanteios: +8.5", generate_soccer_narrative(m, home, away), extra_info, prob_home, prob_away

# ================= 5. LAYOUTS =================
def format_morning_card(game, d1, d2, analise, extra):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ {game['time']}\n"
        f"{extra}"
        f"📝 <b>Resumo:</b> <i>{analise}</i>\n"
        f"✅ <b>Palpite:</b> {d1}\n"
        f"🛡️ <b>Extra:</b> {d2}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_live_radar_card(game, favorite_team, situation):
    return (
        f"⚠️ <b>ALERTA DE OPORTUNIDADE (AO VIVO)</b> ⚠️\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏱️ <b>Tempo:</b> {game['clock']} (2º Tempo)\n"
        f"⚽ <b>Placar:</b> {game['score_home']} - {game['score_away']}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📉 <b>SITUAÇÃO:</b> O Favorito ({favorite_team}) {situation}!\n"
        f"💡 <b>A DICA:</b> A Odd para vitória ou empate do favorito disparou.\n"
    )

def verify_green(pick, h_score, a_score, home_team, away_team):
    total = h_score + a_score
    is_green = False
    
    if "Vitória do" in pick:
        if home_team in pick and h_score > a_score: is_green = True
        elif away_team in pick and a_score > h_score: is_green = True
    elif "Over 1.5" in pick and total > 1: is_green = True
    elif "Over 2.5" in pick and total > 2: is_green = True
    elif "Menos" in pick and total < 3: is_green = True 
    elif "Ambas" in pick and h_score > 0 and a_score > 0: is_green = True
    elif "Empate" in pick or "Dupla" in pick: is_green = True 

    if is_green:
        DAILY_STATS["green"] += 1
        return f"✅ <b>GREEN CONFIRMADO!</b>\n⚽ {home_team} {h_score} x {a_score} {away_team}\n🎯 Tip: {pick}"
    else:
        DAILY_STATS["red"] += 1
        return f"❌ <b>RED</b>\n⚽ {home_team} {h_score} x {a_score} {away_team}\n🎯 Tip: {pick}"

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
        f"🎯 <b>MERCADO:</b> Para marcar a qualquer momento\n"
    )

# ================= 6. AUTOMAÇÕES =================
async def automation_routine(app: Application):
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if agora.hour == 8 and agora.minute == 0:
            global ALERTED_SNIPER, PROCESSED_GAMES, ALERTED_LIVE, DAILY_STATS
            ALERTED_SNIPER.clear(); PROCESSED_GAMES.clear(); ALERTED_LIVE.clear()
            DAILY_STATS = {"green": 0, "red": 0}
            jogos = await fetch_espn_soccer()
            if jogos:
                header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>{agora.strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                txt = header
                for g in jogos:
                    d1, d2, analise, extra, _, _ = await analyze_game_market(g['league_code'], g['id'], g['home'], g['away'])
                    card = format_morning_card(g, d1, d2, analise, extra)
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
            
        if agora.hour == 23 and agora.minute == 30:
            if DAILY_STATS["green"] > 0 or DAILY_STATS["red"] > 0:
                txt = (f"🏁 <b>FECHAMENTO</b> 🏁\n✅ <b>GREENS:</b> {DAILY_STATS['green']}\n❌ <b>REDS:</b> {DAILY_STATS['red']}")
                await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
            await asyncio.sleep(60)
        await asyncio.sleep(30)

async def live_radar_routine(app: Application):
    while True:
        if TODAYS_GAMES:
            await fetch_espn_soccer()
            for g in TODAYS_GAMES:
                if g['status'] == 'in' and g.get('period', 0) >= 2 and g['id'] not in ALERTED_LIVE:
                    _, _, _, _, ph, pa = await analyze_game_market(g['league_code'], g['id'], g['home'], g['away'])
                    msg = None
                    if ph >= 60.0:
                        if g['score_home'] < g['score_away']: msg = format_live_radar_card(g, g['home'], "está perdendo")
                        elif g['score_home'] == g['score_away']: msg = format_live_radar_card(g, g['home'], "está empatando")
                    elif pa >= 60.0:
                        if g['score_away'] < g['score_home']: msg = format_live_radar_card(g, g['away'], "está perdendo")
                        elif g['score_away'] == g['score_home']: msg = format_live_radar_card(g, g['away'], "está empatando")
                    if msg:
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                        ALERTED_LIVE.add(g['id'])
        await asyncio.sleep(120)

async def result_monitor_routine(app: Application):
    while True:
        if TODAYS_GAMES:
            for g in TODAYS_GAMES:
                if g['status'] == 'post' and g['id'] not in PROCESSED_GAMES:
                    d1, _, _, _, _, _ = await analyze_game_market(g['league_code'], g['id'], g['home'], g['away'])
                    msg = verify_green(d1, g['score_home'], g['score_away'], g['home'], g['away'])
                    try:
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                        PROCESSED_GAMES.add(g['id'])
                    except: pass
                    await asyncio.sleep(2)
        await asyncio.sleep(300)

async def live_sniper_routine(app: Application):
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if TODAYS_GAMES:
            for g in TODAYS_GAMES:
                if g['id'] in ALERTED_SNIPER: continue
                try:
                    h, m = map(int, g['time'].split(':'))
                    hora_jogo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                    minutos = (hora_jogo - agora).total_seconds() / 60.0
                    if 0 <= minutos <= 60:
                        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{g['league_code']}/summary?event={g['id']}"
                        async with httpx.AsyncClient(timeout=10) as client:
                            r = await client.get(url)
                            if r.status_code == 200:
                                data = r.json()
                                if 'rosters' in data:
                                    for p in data['rosters'][0].get('roster', []):
                                        if p['position']['name'].lower() in ['forward', 'atacante', 'striker']:
                                            jogador = p['athlete']['displayName']
                                            d1, _, _, _, _, _ = await analyze_game_market(g['league_code'], g['id'], g['home'], g['away'])
                                            txt = format_sniper_card(g, jogador, d1)
                                            await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                                            ALERTED_SNIPER.add(g['id'])
                                            break
                except: pass
        await asyncio.sleep(60)

# ================= 7. MENU E START =================
def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🏀 Grade NBA VIP", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V252</b>\nNarrativa SportyTrader & NBA Analítica.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Buscando grade ({DATA_ALVO})...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_espn_soccer()
        if not jogos:
            await msg.edit_text("❌ Nenhum jogo encontrado.")
            return
        
        br_tz = timezone(timedelta(hours=-3))
        header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>Data: {DATA_ALVO}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            d1, d2, analise, extra, _, _ = await analyze_game_market(g['league_code'], g['id'], g['home'], g['away'])
            card = format_morning_card(g, d1, d2, analise, extra)
            if len(txt) + len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>Postado!</b>")

    elif q.data == "nba_deep":
        msg = await q.message.reply_text("🔎 <b>Analisando NBA (Lines & Stats)...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_professional()
        if not jogos:
            await msg.edit_text("❌ Sem jogos da NBA.")
            return
        header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>NBA Postada!</b>")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V252 NBA STORYTELLER")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_espn_soccer() 
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(live_sniper_routine(app))
    asyncio.create_task(result_monitor_routine(app))
    asyncio.create_task(live_radar_routine(app))
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
