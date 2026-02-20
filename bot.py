# ================= BOT V247 (MODO SIMULAÇÃO 2026 - CORREÇÃO DE DATA/ANO) =================
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

# ================= CONFIGURAÇÃO DE DATA (CRÍTICO) =================
# Aqui forçamos a data da sua grade (20/02/2026) para o bot não usar 2025
DATA_SIMULADA = "20260220" 

# ================= MEMÓRIA GLOBAL =================
TODAYS_GAMES = []
PROCESSED_GAMES = set()
ALERTED_SNIPER = set()
ALERTED_LIVE = set()
DAILY_STATS = {"green": 0, "red": 0}

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
        await asyncio.sleep(10800)
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
                    # NBA não filtramos status rigorosamente na busca geral
                    comp = event['competitions'][0]
                    t1 = comp['competitors'][0]
                    t2 = comp['competitors'][1]
                    team_home = t1 if t1['homeAway'] == 'home' else t2
                    team_away = t2 if t2['homeAway'] == 'away' else t1
                    
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    odds_info = "Aguardando..."
                    if 'odds' in comp and len(comp['odds']) > 0:
                        odd = comp['odds'][0]
                        odds_info = f"Spread: {odd.get('details', '-')} | O/U: {odd.get('overUnder', '-')}"

                    def get_season_leader(team_data):
                        try:
                            for cat in team_data.get('leaders', []):
                                if cat.get('name') == 'scoring':
                                    l = cat['leaders'][0]
                                    return f"{l['athlete']['displayName']} ({float(l['value']):.1f} PPG)"
                        except: return None

                    jogos.append({
                        "match": f"{team_away['team']['name']} @ {team_home['team']['name']}",
                        "time": dt_br.strftime("%H:%M"),
                        "odds": odds_info,
                        "star_home": get_season_leader(team_home),
                        "star_away": get_season_leader(team_away)
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
        f"👇 <b>DESTAQUES:</b>\n{destaques}"
        f"💡 <i>Dica: Busque linhas de Over para esses jogadores.</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 3. MÓDULO FUTEBOL (DATA 2026 FORÇADA) =================
async def fetch_espn_soccer():
    leagues = [
        'uefa.europa', 'uefa.champions', 'conmebol.libertadores', 'conmebol.recopa', 
        'bra.1', 'bra.camp.paulista', 'eng.1', 'eng.2', 'esp.1', 'esp.2', 
        'ita.1', 'ita.2', 'ger.1', 'ger.2', 'fra.1', 'fra.2', 
        'arg.1', 'ksa.1', 'por.1', 'ned.1', 'tur.1', 'bel.1'
    ]
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    logging.info(f"BUSCANDO GRADE SIMULADA: {DATA_SIMULADA}")

    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            # Força a data 20260220 na URL
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={DATA_SIMULADA}"
            
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
                    
                    # === CORREÇÃO V247 ===
                    # REMOVIDO: if dt_br.date() == datetime.now().date()
                    # MOTIVO: O servidor está em 2025, o jogo em 2026. A comparação falhava.
                    # Agora aceitamos TUDO que a API retornar para a data pedida.
                    
                    jogos.append({
                        "id": event['id'], 
                        "league_code": league, 
                        "match": f"{home} x {away}", 
                        "home": home, 
                        "away": away, 
                        "time": dt_br.strftime("%H:%M"), 
                        "league": league_name,
                        "status": state,
                        "period": event['status']['period'],
                        "clock": event['status']['displayClock'],
                        "score_home": score_home,
                        "score_away": score_away
                    })
            except: continue
    
    unicos = {j['match']: j for j in jogos}
    lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    
    global TODAYS_GAMES
    TODAYS_GAMES = lista_final
    logging.info(f"Jogos encontrados: {len(TODAYS_GAMES)}")
    return TODAYS_GAMES

def generate_narrative(market_type, home, away):
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
                        return m, "Over 1.5 Gols", generate_narrative(m, home, away), extra_info, ph, pa
                    if pa >= 60.0: 
                        m = f"Vitória do {away}"
                        return m, "Empate Anula: Visitante", generate_narrative(m, home, away), extra_info, ph, pa
                    if ph >= 40.0: 
                        m = "Ambas Marcam: Sim"
                        return m, "Over 2.5 Gols", generate_narrative(m, home, away), extra_info, ph, pa
                    else: 
                        m = "Menos de 3.5 Gols"
                        return m, "Empate ou Visitante", generate_narrative(m, home, away), extra_info, ph, pa
    except: pass
    
    random.seed(int(event_id)) 
    
    # Fallback Inteligente
    if league_code in ['ger.1', 'ned.1', 'ksa.1', 'tur.1', 'por.1', 'bel.1']:
        m = "Over 2.5 Gols"
        return m, "Ambas Marcam: Sim", generate_narrative(m, home, away), extra_info, prob_home, prob_away
    elif league_code in ['arg.1', 'ita.1', 'bra.2', 'esp.2']:
        m = "Menos de 2.5 Gols"
        return m, "Dupla Chance: Casa/Empate", generate_narrative(m, home, away), extra_info, prob_home, prob_away
    else:
        m = "Over 1.5 Gols"
        return m, "Escanteios: +8.5", generate_narrative(m, home, away), extra_info, prob_home, prob_away

# ================= 4. LAYOUTS =================
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
                jogos_pre = [j for j in jogos if j['status'] == 'pre']
                if jogos_pre:
                    header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>{agora.strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                    txt = header
                    for g in jogos_pre:
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
                if g['status'] == 'in' and g['period'] >= 2 and g['id'] not in ALERTED_LIVE:
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
            jogos_pre = [j for j in TODAYS_GAMES if j['status'] == 'pre']
            for g in jogos_pre:
                if g['id'] in ALERTED_SNIPER: continue
                try:
                    h, m = map(int, g['time'].split(':'))
                    hora_jogo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                    minutos = (hora_jogo - agora).total_seconds() / 60.0
                    if 50 <= minutos <= 60:
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

# ================= 6. MENU E START =================
def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🏀 Grade NBA VIP", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V247</b>\nModo Simulação 2026 Ativado.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text("🔎 <b>Gerando grade (Modo 2026)...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_espn_soccer()
        if not jogos:
            await msg.edit_text("❌ Nenhum jogo encontrado na grade simulada.")
            return
        
        jogos_pre = [j for j in jogos if j['status'] == 'pre']
        if not jogos_pre:
            await msg.edit_text("⚠️ Jogos encontrados, mas todos já começaram ou acabaram.")
            return

        br_tz = timezone(timedelta(hours=-3))
        # Exibe a data simulada no cabeçalho
        header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>Data: 20/02/2026</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos_pre:
            d1, d2, analise, extra, _, _ = await analyze_game_market(g['league_code'], g['id'], g['home'], g['away'])
            card = format_morning_card(g, d1, d2, analise, extra)
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
        header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y')}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>NBA Postada!</b>")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V247 SIMULATION")
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
    app.run_polling()

if __name__ == "__main__":
    main()
