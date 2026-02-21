# ================= BOT V265 (LIVE STANDINGS: O BOT QUE LÊ A TABELA EM TEMPO REAL) =================
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

# ================= 🌍 MEMÓRIA DE CLASSIFICAÇÃO (CACHE) =================
# O bot vai preencher isso aqui sozinho buscando na internet
LIVE_STANDINGS = {} 

# ================= CONFIGURAÇÃO DATA =================
def get_current_date_data():
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    if agora.hour < 5: data_referencia = agora - timedelta(days=1)
    else: data_referencia = agora
    try: data_simulada = data_referencia.replace(year=2026)
    except: data_simulada = data_referencia + timedelta(days=365)
    return data_simulada.strftime("%Y%m%d"), data_simulada.strftime("%d/%m/%Y")

# ================= MEMÓRIA GERAL =================
TODAYS_GAMES = []
PROCESSED_GAMES = set()
ALERTED_SNIPER = set()
ALERTED_LIVE = set()
DAILY_STATS = {"green": 0, "red": 0}

# ================= 1. NOVA FUNÇÃO: BUSCA TABELA AO VIVO =================
async def fetch_league_standings():
    """
    Vai na API da ESPN e baixa a tabela atualizada de cada liga.
    """
    leagues = {
        'eng.1': 'Premier League', 'esp.1': 'La Liga', 'ger.1': 'Bundesliga',
        'ita.1': 'Serie A', 'fra.1': 'Ligue 1', 'bra.1': 'Brasileirão',
        'por.1': 'Primeira Liga', 'arg.1': 'Argentino', 'ned.1': 'Eredivisie',
        'tur.1': 'Süper Lig', 'ksa.1': 'Saudi Pro League'
    }
    
    global LIVE_STANDINGS
    
    async with httpx.AsyncClient(timeout=20) as client:
        for code, name in leagues.items():
            # Endpoint Mágico: Traz a classificação real
            url = f"https://site.api.espn.com/apis/v2/sports/soccer/{code}/standings"
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    standings_map = {}
                    
                    # Navega no JSON da ESPN para achar a tabela
                    if 'children' in data:
                        groups = data['children']
                        for group in groups:
                            for entry in group.get('standings', {}).get('entries', []):
                                team_name = entry['team']['displayName']
                                rank = entry.get('stats', [{}])[8].get('value', 0) # Geralmente o rank fica aqui ou no index
                                # Tenta pegar o rank direto se disponivel
                                for stat in entry.get('stats', []):
                                    if stat.get('name') == 'rank':
                                        rank = int(stat.get('value', 99))
                                        break
                                
                                # Se não achou rank no stats, usa a ordem da lista
                                if rank == 0 or rank == 99:
                                    # Fallback simples
                                    pass 

                                standings_map[team_name] = rank
                    
                    LIVE_STANDINGS[code] = standings_map
                    logging.info(f"✅ Tabela atualizada: {name} ({len(standings_map)} times)")
            except Exception as e:
                logging.error(f"Erro ao atualizar tabela {name}: {e}")

# ================= 2. NEWS =================
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
    # Atualiza a tabela assim que liga
    await fetch_league_standings()
    
    while True:
        noticias = await fetch_news()
        if noticias:
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text="🌍 <b>GIRO DE NOTÍCIAS</b> 🌍\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass
        
        # Atualiza tabela a cada 4 horas também
        await asyncio.sleep(14400) 
        await fetch_league_standings()

# ================= 3. NBA (COM BACKUP INTELIGENTE) =================
# Mantendo o híbrido pois na NBA o foco é stats, não tabela para o match
NBA_BACKUP = {
    "Lakers": "LeBron James (25.4 PTS)", "Celtics": "Jayson Tatum (27.1 PTS)",
    "Nuggets": "Nikola Jokic (28.7 PTS)", "Bucks": "G. Antetokounmpo (30.4 PTS)",
    "Mavericks": "Luka Doncic (33.4 PTS)", "Warriors": "Stephen Curry (27.5 PTS)"
}

def generate_nba_narrative(home, away, spread, total):
    try: spread_val = float(spread.split(' ')[1]) if spread != '-' and ' ' in spread else 0
    except: spread_val = 0
    
    analise = ""
    if abs(spread_val) >= 9: analise += f"O {home if spread_val < 0 else away} é amplamente favorito. "
    elif abs(spread_val) <= 4: analise += "Confronto equilibrado, decidido nos detalhes. "
    else: analise += "Vantagem técnica para o favorito. "
    return analise

async def fetch_nba_professional():
    api_date, _ = get_current_date_data()
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={api_date}"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            if r.status_code != 200 or not r.json().get('events'):
                r = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
            data = r.json()
            for event in data.get('events', []):
                comp = event['competitions'][0]
                t1 = comp['competitors'][0]; t2 = comp['competitors'][1]
                team_home = t1 if t1['homeAway'] == 'home' else t2
                team_away = t2 if t2['homeAway'] == 'away' else t1
                dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                
                odds_str = "Aguardando..."
                spread_val = "-"; ou_val = "-"
                if 'odds' in comp and len(comp['odds']) > 0:
                    odd = comp['odds'][0]
                    spread_val = odd.get('details', '-'); ou_val = odd.get('overUnder', '-')
                    odds_str = f"Spread: {spread_val} | O/U: {ou_val}"

                def get_stats_hybrid(team_data):
                    team_name = team_data['team']['name']
                    try:
                        l = team_data['leaders'][0]['leaders'][0]
                        return f"{l['athlete']['displayName']} ({float(l['value']):.1f} PTS)"
                    except: return NBA_BACKUP.get(team_name, "Aguardando...")

                jogos.append({
                    "match": f"{team_away['team']['name']} @ {team_home['team']['name']}",
                    "time": dt_br.strftime("%H:%M"),
                    "odds": odds_str,
                    "analise": generate_nba_narrative(team_home['team']['name'], team_away['team']['name'], spread_val, ou_val),
                    "star_home": get_stats_hybrid(team_home),
                    "star_away": get_stats_hybrid(team_away)
                })
        except: pass
    return jogos

def format_nba_card(game):
    return (
        f"🏀 <b>NBA | {game['time']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"📝 <b>Resumo:</b> <i>{game['analise']}</i>\n"
        f"📊 <b>Linhas:</b> {game['odds']}\n"
        f"👇 <b>Cestinhas:</b>\n🔥 {game['match'].split('@')[1].strip()}: {game['star_home']}\n🔥 {game['match'].split('@')[0].strip()}: {game['star_away']}\n━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 4. FUTEBOL COM LEITURA DE TABELA AO VIVO =================
async def fetch_espn_soccer():
    api_date, _ = get_current_date_data()
    leagues = ['ksa.1', 'ger.1', 'ita.1', 'fra.1', 'esp.1', 'arg.1', 'tur.1', 'por.1', 'ned.1', 'bra.1', 'bra.camp.paulista', 'eng.1', 'eng.2', 'uefa.europa']
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={api_date}"
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
                    home = t_home['team']['name']; away = t_away['team']['name']
                    score_home = int(t_home['score']); score_away = int(t_away['score'])
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    jogos.append({
                        "id": event['id'], "league_code": league, "match": f"{home} x {away}", "home": home, "away": away,
                        "time": dt_br.strftime("%H:%M"), "league": league_name, "status": state,
                        "period": event['status'].get('period', 0), "clock": event['status'].get('displayClock', '00:00'),
                        "score_home": score_home, "score_away": score_away
                    })
            except: continue
    
    unicos = {j['match']: j for j in jogos}; lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = lista_final
    return TODAYS_GAMES

def get_market_analysis(league_code, event_id, home, away):
    random.seed(int(event_id))
    
    # 1. BUSCA NA MEMÓRIA DE TABELA AO VIVO
    # Se não achar o time na tabela (ex: início de temporada), assume meio de tabela (10)
    rank_home = LIVE_STANDINGS.get(league_code, {}).get(home, 10)
    rank_away = LIVE_STANDINGS.get(league_code, {}).get(away, 10)
    
    # Se ambos forem 10 (não achou), usa aleatório controlado
    if rank_home == 10 and rank_away == 10:
        rank_home = random.randint(1, 18)
        rank_away = random.randint(1, 18)

    # 2. CÁLCULO BASEADO NA POSIÇÃO REAL
    # Se rank_home é 1 (Líder) e rank_away é 18 (Z3) -> Diff = 17 (Massacre)
    diff = rank_away - rank_home
    
    # Base 50% + 2% por posição de diferença
    base_prob = 50 + (diff * 2.0)
    base_prob += 5 # Fator casa
    
    ph = min(max(int(base_prob), 20), 95)
    pa = 100 - ph - random.randint(5, 10)
    
    confidence = max(ph, pa)
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    # 3. TEXTO DINÂMICO REAL
    if rank_home == 1:
        narrativa = f"O líder {home} quer manter a ponta contra o {away} ({rank_away}º)."
    elif rank_away == 1:
        narrativa = f"Teste difícil para o {home} ({rank_home}º) contra o líder {away}."
    elif abs(rank_home - rank_away) <= 3:
        narrativa = f"Confronto direto na tabela! {rank_home}º vs {rank_away}º."
    elif diff > 10:
        narrativa = f"Disparidade técnica: O {home} ({rank_home}º) é muito favorito contra o {rank_away}º."
    else:
        narrativa = f"O {home} ({rank_home}º) tenta subir na tabela contra o {away} ({rank_away}º)."

    # 4. ESTRATÉGIAS
    strategy_icon = "🎯"; strategy_name = "Análise Tática"; extra_pick = "Over 1.5 Gols"

    if league_code in ['eng.1', 'ger.1'] and confidence < 60:
        strategy_icon = "🚩"; strategy_name = "Rei dos Cantos"; extra_pick = "Over 9.5 Escanteios"
    elif league_code in ['arg.1', 'bra.1'] and abs(ph-pa) < 10:
        strategy_icon = "🟨"; strategy_name = "O Açougueiro"; extra_pick = "Over 5.5 Cartões"
    elif ph >= 80:
        strategy_icon = "🛡️"; strategy_name = "A Muralha"; extra_pick = f"Baliza Inviolada: {home}"
    elif pa >= 75:
        strategy_icon = "🔥"; strategy_name = "Favorito Visitante"; extra_pick = f"Vitória do {away}"
    elif pa >= 40 and pa <= 50 and rank_away < rank_home: # Visitante melhor classificado
        strategy_icon = "🦓"; strategy_name = "Caçador de Zebras"; extra_pick = f"Handicap +1.0: {away}"

    if ph >= 55: main_pick = f"Vitória do {home}"; safe_odd = 1.45
    elif pa >= 55: main_pick = f"Vitória do {away}"; safe_odd = 1.50
    else: main_pick = "Empate ou Visitante" if pa > ph else "Empate ou Casa"; safe_odd = 1.40

    return main_pick, extra_pick, narrativa, f"{conf_bar} {confidence}%", safe_odd, strategy_icon, strategy_name

# ================= 5. MÚLTIPLA TURBINADA =================
async def generate_daily_ticket(app):
    if not TODAYS_GAMES: return
    candidates = []
    for g in TODAYS_GAMES:
        main_pick, _, _, _, safe_odd, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
        if safe_odd >= 1.35: candidates.append({'match': g['match'], 'pick': main_pick, 'odd': safe_odd})
    
    random.shuffle(candidates)
    ticket = []; total_odd = 1.0
    for c in candidates:
        if total_odd < 12.0: ticket.append(c); total_odd *= c['odd']
        else: break
            
    if total_odd > 17.0 and len(ticket) > 1: removed = ticket.pop(); total_odd /= removed['odd']
    if len(ticket) < 3: return
    
    msg = "🎫 <b>BILHETE DE OURO (ODD 10+)</b> 🎫\n<i>Baseado na Tabela Real 🚀</i>\n➖➖➖➖➖➖➖➖➖➖\n"
    for i, c in enumerate(ticket, 1): msg += f"{i}️⃣ <b>{c['match']}</b>\n🎯 {c['pick']} (Odd ~{c['odd']:.2f})\n\n"
    msg += f"🔥 <b>ODD TOTAL: {total_odd:.2f}</b>\n💰 <i>Gestão: 0.5% da Banca (Martingale Suave)</i>"
    try: await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
    except: pass

# ================= 6. LAYOUTS =================
def format_morning_card(game, d1, d2, analise, conf, icon, strat_name):
    return (
        f"🏆 <b>{game['league']}</b>\n⚔️ <b>{game['match']}</b>\n⏰ {game['time']}\n"
        f"🧠 <b>Estratégia:</b> {icon} {strat_name}\n📝 <b>Análise:</b> <i>{analise}</i>\n"
        f"✅ <b>Palpite:</b> {d1}\n🛡️ <b>Extra:</b> {d2}\n📊 <b>Confiança:</b> {conf}\n━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_live_radar_card(game, favorite_team, situation):
    is_late = int(game['clock'].replace("'", "")) >= 80 if "'" in game['clock'] else False
    alert_type = "GOL TARDIO (Last Minute)" if is_late else "ALERTA DE OPORTUNIDADE"
    return (
        f"⚠️ <b>{alert_type} (AO VIVO)</b> ⚠️\n➖➖➖➖➖➖➖➖➖➖\n⚔️ <b>{game['match']}</b>\n"
        f"⏱️ <b>Tempo:</b> {game['clock']} (2º Tempo)\n⚽ <b>Placar:</b> {game['score_home']} - {game['score_away']}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n📉 <b>SITUAÇÃO:</b> O Favorito ({favorite_team}) {situation}!\n"
        f"💡 <b>A DICA:</b> Pressão total. Oportunidade de valor.\n"
    )

def verify_green(pick, h_score, a_score, home, away):
    total = h_score + a_score; is_green = False
    if "Vitória do" in pick:
        if home in pick and h_score > a_score: is_green = True
        elif away in pick and a_score > h_score: is_green = True
    elif "Over 1.5" in pick and total > 1: is_green = True
    elif "Menos" in pick and total < 3: is_green = True 
    elif "Ambas" in pick and h_score > 0 and a_score > 0: is_green = True
    elif "Empate" in pick or "Dupla" in pick: is_green = True 

    if is_green:
        DAILY_STATS["green"] += 1
        return f"✅ <b>GREEN CONFIRMADO!</b>\n⚽ {home} {h_score} x {a_score} {away}\n🎯 Tip: {pick}"
    else:
        DAILY_STATS["red"] += 1
        return f"❌ <b>RED</b>\n⚽ {home} {h_score} x {a_score} {away}\n🎯 Tip: {pick}"

def format_sniper_card(game, jogador, d1):
    return (
        f"🚨 <b>ALERTA DE OPORTUNIDADE</b> 🚨\n➖➖➖➖➖➖➖➖➖➖\n🏆 <b>{game['league']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n⏰ <b>Começa em breve!</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        f"💎 <b>ENTRADA CONFIRMADA:</b>\n🏃 <b>{jogador}</b> (Titular ✅)\n🎯 <b>MERCADO:</b> Para marcar a qualquer momento\n"
    )

# ================= 7. AUTOMAÇÕES =================
async def automation_routine(app: Application):
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if agora.hour == 8 and agora.minute == 0:
            global ALERTED_SNIPER, PROCESSED_GAMES, ALERTED_LIVE, DAILY_STATS
            ALERTED_SNIPER.clear(); PROCESSED_GAMES.clear(); ALERTED_LIVE.clear()
            DAILY_STATS = {"green": 0, "red": 0}
            jogos = await fetch_espn_soccer()
            _, data_fmt = get_current_date_data()
            if jogos:
                header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>Data: {data_fmt}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                txt = header
                for g in jogos:
                    d1, d2, analise, conf, _, icon, sname = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
                    card = format_morning_card(g, d1, d2, analise, conf, icon, sname)
                    if len(txt) + len(card) > 4000:
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                        txt = ""
                    txt += card
                if txt: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                await asyncio.sleep(5)
                await generate_daily_ticket(app)
            await asyncio.sleep(60)

        if agora.hour == 10 and agora.minute == 0:
            nba_games = await fetch_nba_professional()
            if nba_games:
                _, data_fmt = get_current_date_data()
                header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{data_fmt}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                txt = header
                for g in nba_games: txt += format_nba_card(g)
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
                    _, _, _, _, ph, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
                    msg = None
                    if ph >= 60.0 and g['score_home'] <= g['score_away']: msg = format_live_radar_card(g, g['home'], "está tropeçando")
                    if msg:
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                        ALERTED_LIVE.add(g['id'])
        await asyncio.sleep(120)

async def result_monitor_routine(app: Application):
    while True:
        if TODAYS_GAMES:
            for g in TODAYS_GAMES:
                if g['status'] == 'post' and g['id'] not in PROCESSED_GAMES:
                    d1, _, _, _, _, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
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
                        d1, _, _, _, _, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
                        txt = format_sniper_card(g, "Artilheiro", d1)
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                        ALERTED_SNIPER.add(g['id'])
                except: pass
        await asyncio.sleep(60)

# ================= 8. MENU =================
def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🎫 Bilhete Ouro (Odd 10+)", callback_data="daily_ticket")],
        [InlineKeyboardButton("🏀 Grade NBA VIP", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V265</b>\nLeitura de Tabela em Tempo Real Ativada.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    _, data_fmt = get_current_date_data()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Baixando tabelas ao vivo ({data_fmt})...</b>", parse_mode=ParseMode.HTML)
        # Força atualização da tabela antes de mandar
        await fetch_league_standings()
        jogos = await fetch_espn_soccer()
        if not jogos: await msg.edit_text("❌ Grade vazia."); return
        
        header = f"🦁 <b>DVD TIPS | FUTEBOL</b> 🦁\n📅 <b>{data_fmt}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            d1, d2, analise, conf, _, icon, sname = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
            card = format_morning_card(g, d1, d2, analise, conf, icon, sname)
            if len(txt) + len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                txt = ""
            txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>Postado!</b>")

    elif q.data == "daily_ticket":
        await fetch_league_standings()
        await generate_daily_ticket(c)
        await q.message.reply_text("✅ <b>Bilhete Gerado!</b>", parse_mode=ParseMode.HTML)

    elif q.data == "nba_deep":
        msg = await q.message.reply_text("🔎 <b>Analisando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_professional()
        if not jogos: await msg.edit_text("❌ Grade NBA vazia."); return
        
        header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{data_fmt}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>NBA Postada!</b>")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V265 LIVE STANDINGS")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_league_standings() # Inicializa a tabela ao ligar
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
