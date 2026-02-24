# ================= BOT V288 (DUAL ENGINE: ELITE PADRÃO + BRASIL VIA SCOREPANEL) =================
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

# ================= 🛡️ CACHE DE TABELA =================
LIVE_STANDINGS = {}

# ================= 📊 BACKUP DE RANKING =================
REAL_STANDINGS_BACKUP = {
    "Arsenal": 1, "Manchester City": 2, "Liverpool": 3, "Aston Villa": 4,
    "Real Madrid": 1, "Barcelona": 2, "Atletico Madrid": 3,
    "Bayern Munich": 1, "Bayer Leverkusen": 2, "Inter Milan": 1, "Juventus": 2,
    "Palmeiras": 1, "Flamengo": 2, "Atletico Mineiro": 3, "Botafogo": 4, "Fortaleza": 5,
    "River Plate": 1, "Boca Juniors": 2, "Racing Club": 3,
    "Al Hilal": 1, "Al Nassr": 2, "Al Ittihad": 3
}

# ================= CONFIGURAÇÃO DATA (API REAL / DISPLAY 2026) =================
def get_current_date_data():
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    
    # Se for madrugada, usa data de ontem para pegar jogos da noite
    if agora.hour < 5: 
        data_api = agora - timedelta(days=1)
    else: 
        data_api = agora
    
    # Data para exibição (Simulação 2026)
    try: data_display = data_api.replace(year=2026)
    except: data_display = data_api + timedelta(days=365)
    
    return data_api.strftime("%Y%m%d"), data_display.strftime("%d/%m/%Y")

# ================= MEMÓRIA =================
TODAYS_GAMES = []
TODAYS_NBA = []
PROCESSED_GAMES = set()
ALERTED_SNIPER = set()
ALERTED_LIVE = set()
DAILY_STATS = {"green": 0, "red": 0}

# ================= 1. TRATAMENTO DE ERROS =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# ================= 2. NEWS =================
async def fetch_news():
    feeds = ["https://ge.globo.com/rss/ge/futebol/", "https://rss.uol.com.br/feed/esporte.xml"]
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
    await fetch_league_standings()
    while True:
        noticias = await fetch_news()
        if noticias:
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text="🌍 <b>GIRO DE NOTÍCIAS</b> 🌍\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass
        await asyncio.sleep(14400) 
        await fetch_league_standings()

# ================= 3. BUSCA TABELA =================
async def fetch_league_standings():
    leagues = {
        'eng.1': 'Premier League', 'esp.1': 'La Liga', 'ger.1': 'Bundesliga',
        'ita.1': 'Serie A', 'fra.1': 'Ligue 1', 'bra.1': 'Brasileirão',
        'arg.1': 'Argentino', 'ksa.1': 'Saudi Pro League'
    }
    global LIVE_STANDINGS
    async with httpx.AsyncClient(timeout=20) as client:
        for code, name in leagues.items():
            url = f"https://site.api.espn.com/apis/v2/sports/soccer/{code}/standings"
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    temp_map = {}
                    if 'children' in data:
                        for group in data['children']:
                            for entry in group.get('standings', {}).get('entries', []):
                                team = entry['team']['displayName']
                                rank = 10
                                for stat in entry.get('stats', []):
                                    if stat.get('name') == 'rank':
                                        rank = int(stat.get('value', 10)); break
                                temp_map[team] = rank
                    if temp_map: LIVE_STANDINGS[code] = temp_map
            except: pass

# ================= 4. NBA =================
def generate_nba_narrative(home, away, spread, total):
    try: spread_val = float(spread.split(' ')[1]) if spread != '-' and ' ' in spread else 0
    except: spread_val = 0
    analise = ""
    if abs(spread_val) >= 9: analise += f"O {home if spread_val < 0 else away} é amplamente favorito. "
    else: analise += "Confronto equilibrado. "
    return analise

async def fetch_nba_professional():
    api_date = get_current_date_data()[0]
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
                state = event['status']['type']['state']
                comp = event['competitions'][0]
                t1 = comp['competitors'][0]; t2 = comp['competitors'][1]
                team_home = t1 if t1['homeAway'] == 'home' else t2
                team_away = t2 if t2['homeAway'] == 'away' else t1
                s_home = int(team_home['score']); s_away = int(team_away['score'])
                dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                
                odds_str = "Aguardando..."; spread_val = "-"; ou_val = "-"
                if 'odds' in comp and len(comp['odds']) > 0:
                    odd = comp['odds'][0]
                    spread_val = odd.get('details', '-'); ou_val = odd.get('overUnder', '-')
                    odds_str = f"Spread: {spread_val} | O/U: {ou_val}"

                try: spread_num = float(spread_val.split(' ')[1]) if ' ' in spread_val else 0
                except: spread_num = 0
                if spread_num <= -5: pick = f"Vitória do {team_home['team']['name']}"
                elif spread_num >= 5: pick = f"Vitória do {team_away['team']['name']}"
                else: pick = f"Vitória do {team_home['team']['name']}"

                analise = generate_nba_narrative(team_home['team']['name'], team_away['team']['name'], spread_val, ou_val)
                jogos.append({
                    "match": f"{team_away['team']['name']} @ {team_home['team']['name']}",
                    "home": team_home['team']['name'], "away": team_away['team']['name'],
                    "time": dt_br.strftime("%H:%M"), "odds": odds_str, "analise": analise,
                    "pick": pick, "status": state, "score_home": s_home, "score_away": s_away
                })
        except: pass
    global TODAYS_NBA; TODAYS_NBA = jogos
    return jogos

def format_nba_card(game):
    return (
        f"🏀 <b>NBA | {game['time']}</b>\n⚔️ <b>{game['match']}</b>\n"
        f"📝 <b>Resumo:</b> <i>{game['analise']}</i>\n✅ <b>Palpite:</b> {game['pick']}\n"
        f"📊 <b>Linhas:</b> {game['odds']}\n━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 5. FUTEBOL HÍBRIDO (ELITE + UNIVERSAL BRASIL) =================
async def get_match_details(league_code, event_id):
    if not league_code or league_code == "universal": league_code = "bra.1"
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    details = {"referee": None, "stadium": None, "stats": {"home_shots": 0, "away_shots": 0}}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if 'gameInfo' in data:
                    gi = data['gameInfo']
                    if 'venue' in gi: details['stadium'] = gi['venue'].get('fullName')
                    if 'officials' in gi:
                        for off in gi['officials']:
                            if off.get('position', {}).get('name') == 'Referee':
                                details['referee'] = off.get('displayName'); break
                if 'boxscore' in data and 'teams' in data['boxscore']:
                    for team_stat in data['boxscore']['teams']:
                        is_home = team_stat['team']['id'] == data['header']['competitions'][0]['competitors'][0]['id']
                        stats_list = team_stat.get('statistics', [])
                        shots = 0
                        for s in stats_list:
                            if s['name'] == 'totalShots': shots = int(s['displayValue'])
                        if is_home: details['stats']['home_shots'] = shots
                        else: details['stats']['away_shots'] = shots
    except: pass
    return details

async def fetch_espn_soccer():
    api_date = get_current_date_data()[0]
    
    # MOTOR 1: ELITE GLOBAL (Busca Direta por Liga)
    # Essas ligas funcionam bem na API padrão.
    elite_leagues = [
        'uefa.champions', 'conmebol.libertadores', 'conmebol.sudamericana',
        'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1', 'ksa.1',
        'arg.1', 'por.1', 'ned.1', 'tur.1'
    ]
    
    # MOTOR 2: BRASIL & COPAS (Busca Universal/Scorepanel)
    # A "Outra API" que varre tudo e filtra por nome.
    BRAZIL_TARGETS = ["Copa do Brasil", "Brasil", "Nordeste", "Estadual", "Potiguar", "Catarinense", "Paulista", "Carioca"]
    # Palavras-chave para BLOQUEAR (Lixo)
    BAD_FILTER = ["Sub-20", "Sub-19", "Women", "Feminino", "Amador", "2.", "Segunda"]

    jogos = []
    ids_processados = set()
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=30) as client:
        
        # --- EXECUÇÃO MOTOR 1 (ELITE) ---
        for league in elite_leagues:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={api_date}"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        if event['id'] not in ids_processados:
                            process_event(event, jogos, ids_processados, br_tz, league)
            except: pass

        # --- EXECUÇÃO MOTOR 2 (BRASIL UNIVERSAL) ---
        try:
            # Esta é a "Outra API" (Scorepanel)
            url_all = f"https://site.api.espn.com/apis/site/v2/sports/soccer/scorepanel?dates={api_date}"
            r = await client.get(url_all)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    league_info = event.get('competitions', [{}])[0].get('league', {})
                    league_name = league_info.get('name', 'Futebol')
                    league_slug = league_info.get('slug', 'universal')
                    
                    # FILTRO INTELIGENTE: Pega se for "Copa do Brasil" ou "Nordeste", ignora se for "Sub-20"
                    is_brazil_target = any(k in league_name for k in BRAZIL_TARGETS)
                    is_garbage = any(b in league_name for b in BAD_FILTER)
                    
                    if is_brazil_target and not is_garbage:
                        if event['id'] not in ids_processados:
                            # Força o slug para 'bra.1' para o detalhamento tentar achar stats depois
                            process_event(event, jogos, ids_processados, br_tz, "bra.copa_do_brasil")
        except: pass

    jogos.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = jogos
    return TODAYS_GAMES

def process_event(event, jogos, ids_processados, br_tz, league_slug):
    try:
        league_data = event.get('competitions', [{}])[0].get('league', {})
        league_name = league_data.get('name', 'Futebol')
        
        comp = event['competitions'][0]['competitors']
        t_home = comp[0] if comp[0]['homeAway'] == 'home' else comp[1]
        t_away = comp[1] if comp[1]['homeAway'] == 'away' else comp[0]
        
        jogos.append({
            "id": event['id'], "league_code": league_slug, 
            "match": f"{t_home['team']['name']} x {t_away['team']['name']}",
            "home": t_home['team']['name'], "away": t_away['team']['name'], 
            "time": datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz).strftime("%H:%M"),
            "league": league_name, "status": event['status']['type']['state'],
            "period": event['status'].get('period', 0), "clock": event['status'].get('displayClock', '00:00'),
            "score_home": int(t_home['score']), "score_away": int(t_away['score'])
        })
        ids_processados.add(event['id'])
    except: pass

def calculate_dynamic_odd(probability, league_code, is_super_fav):
    if probability <= 0: return 2.00
    fair_odd = 100 / probability
    multiplier = 1.0
    if ('arg' in str(league_code) or 'bra' in str(league_code)) and not is_super_fav:
        multiplier = 1.30 # Valoriza a odd em ligas difíceis
    elif 'ger' in str(league_code) or 'ksa' in str(league_code):
        multiplier = 0.95
    final_odd = (fair_odd + random.uniform(0.02, 0.08)) * multiplier
    if final_odd < 1.05: final_odd = 1.05
    return round(final_odd, 2)

def get_market_analysis(league_code, event_id, home, away):
    random.seed(int(event_id))
    standings = LIVE_STANDINGS.get(league_code, {})
    rank_home = standings.get(home, REAL_STANDINGS_BACKUP.get(home, 10))
    rank_away = standings.get(away, REAL_STANDINGS_BACKUP.get(away, 10))
    
    # Se for Copa, força ranking para times conhecidos
    if 'copa' in str(league_code).lower() or 'champions' in str(league_code) or 'libertadores' in str(league_code):
        if home in REAL_STANDINGS_BACKUP: rank_home = 1
        if away in REAL_STANDINGS_BACKUP: rank_away = 1

    SUPER_FAVORITOS = ["Al Hilal", "Al Nassr", "Al Ittihad", "Real Madrid", "Manchester City", "Bayern Munich", "Bayer Leverkusen", "Flamengo", "Palmeiras", "River Plate", "Fortaleza"]
    
    if "Liverpool" in home and 'eng' not in str(league_code) and 'uefa' not in str(league_code): pass 
    elif "Liverpool" in away and 'eng' not in str(league_code) and 'uefa' not in str(league_code): pass
    else: SUPER_FAVORITOS.append("Liverpool")

    is_home_super = any(s in home for s in SUPER_FAVORITOS)
    is_away_super = any(s in away for s in SUPER_FAVORITOS)

    if is_away_super and not is_home_super: ph = 15; pa = 80
    elif is_home_super and not is_away_super: ph = 85; pa = 10
    else:
        diff = rank_away - rank_home
        base_prob = 50 + (diff * 2.5) + 5
        ph = min(max(int(base_prob), 20), 92)
        pa = 100 - ph - random.randint(5, 10)

    confidence = max(ph, pa)
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    narrativa = "Duelo eliminatório." if 'copa' in str(league_code).lower() else "Confronto equilibrado."
    if rank_home < rank_away: narrativa = f"O {home} é favorito."
    elif rank_away < rank_home: narrativa = f"O {away} busca o resultado."

    strategy_icon = "🎯"; strategy_name = "Análise Tática"; extra_pick = "Over 1.5 Gols"

    if pa >= 70:
        strategy_icon = "🔥"; strategy_name = "Favorito Visitante"; extra_pick = f"Vitória do {away}"
        narrativa = f"O {away} é superior tecnicamente."
    elif ph >= 75:
        strategy_icon = "🛡️"; strategy_name = "A Muralha"; extra_pick = f"Vitória do {home}"
        narrativa = f"O {home} deve dominar."
    elif 'copa' in str(league_code).lower():
        strategy_icon = "🏆"; strategy_name = "Copeiro"; extra_pick = "Ambas Marcam: Não"
    elif 'eng' in str(league_code) and confidence < 60:
        strategy_icon = "🚩"; strategy_name = "Rei dos Cantos"; extra_pick = "Over 9.5 Escanteios"
    elif 'bra' in str(league_code) and abs(ph-pa) < 15:
        strategy_icon = "🟨"; strategy_name = "O Açougueiro"; extra_pick = "Over 5.5 Cartões"
    elif pa >= 40 and pa <= 55 and rank_away < rank_home and not is_home_super:
        strategy_icon = "🦓"; strategy_name = "Caçador de Zebras"; extra_pick = f"Handicap +1.0: {away}"

    is_any_super = is_home_super or is_away_super
    if ph >= 55: main_pick = f"Vitória do {home}"; safe_odd = calculate_dynamic_odd(ph, str(league_code), is_any_super)
    elif pa >= 55: main_pick = f"Vitória do {away}"; safe_odd = calculate_dynamic_odd(pa, str(league_code), is_any_super)
    else: main_pick = "Empate ou Visitante" if pa > ph else "Empate ou Casa"; safe_odd = calculate_dynamic_odd(60, str(league_code), False)

    return main_pick, extra_pick, narrativa, f"{conf_bar} {confidence}%", safe_odd, strategy_icon, strategy_name

# ================= 6. MÚLTIPLA TURBINADA =================
async def generate_daily_ticket(app):
    if not TODAYS_GAMES: return
    candidates = []
    for g in TODAYS_GAMES:
        main_pick, _, _, _, odd, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
        if 1.30 <= odd <= 1.95: candidates.append({'match': g['match'], 'pick': main_pick, 'odd': odd})
    
    random.shuffle(candidates)
    ticket = []; total_odd = 1.0
    for c in candidates:
        if total_odd < 12.0: ticket.append(c); total_odd *= c['odd']
        else: break
    
    if len(ticket) >= 3:
        msg = "🎫 <b>BILHETE DE OURO (ODD 10+)</b> 🎫\n<i>Jogos Selecionados 🚀</i>\n➖➖➖➖➖➖➖➖➖➖\n"
        for i, c in enumerate(ticket, 1): msg += f"{i}️⃣ <b>{c['match']}</b>\n🎯 {c['pick']} (Odd: {c['odd']:.2f})\n\n"
        msg += f"🔥 <b>ODD TOTAL: {total_odd:.2f}</b>\n💰 <i>Gestão: 0.5% da Banca</i>"
        try: await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
        except: pass

# ================= 7. LAYOUTS =================
def format_morning_card(game, d1, d2, analise, conf, icon, strat_name, details):
    extra_info = ""
    if details['stadium']: extra_info += f"🏟️ {details['stadium']}\n"
    if details['referee']: extra_info += f"👮 Juiz: {details['referee']}\n"
    
    return (
        f"🏆 <b>{game['league']}</b>\n⚔️ <b>{game['match']}</b>\n⏰ {game['time']}\n{extra_info}"
        f"🧠 <b>Estratégia:</b> {icon} {strat_name}\n📝 <b>Análise:</b> <i>{analise}</i>\n"
        f"✅ <b>Palpite:</b> {d1}\n🛡️ <b>Extra:</b> {d2}\n📊 <b>Confiança:</b> {conf}\n━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_live_radar_card(game, favorite_team, situation, stats=None):
    stats_txt = ""
    if stats and stats['home_shots'] > 0:
        stats_txt = f"\n📊 <b>Stats:</b> 🔫 {stats['home_shots']}x{stats['away_shots']} (Chutes)\n"
    return (
        f"⚠️ <b>ALERTA DE OPORTUNIDADE (AO VIVO)</b> ⚠️\n➖➖➖➖➖➖➖➖➖➖\n⚔️ <b>{game['match']}</b>\n"
        f"⏱️ <b>Tempo:</b> {game['clock']}\n⚽ <b>Placar:</b> {game['score_home']} - {game['score_away']}{stats_txt}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n📉 <b>SITUAÇÃO:</b> O Favorito ({favorite_team}) {situation}!\n"
    )

def format_pressure_alert(game, team_pressure, shots):
    return (
        f"🚨 <b>ALERTA DE PRESSÃO ABSURDA</b> 🚨\n➖➖➖➖➖➖➖➖➖➖\n⚔️ <b>{game['match']}</b>\n"
        f"⏱️ <b>Tempo:</b> {game['clock']}\n⚽ <b>Placar:</b> {game['score_home']} - {game['score_away']}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n🔥 <b>O {team_pressure} está amassando!</b>\n"
        f"🔫 <b>{shots} Finalizações</b> até agora!\n💡 <b>Dica:</b> Gol iminente (Over Limite).\n"
    )

def verify_green(pick, h_score, a_score, home, away):
    total = h_score + a_score; is_green = False
    is_nba = total > 20
    if is_nba:
        if "Vitória do" in pick:
            if home in pick and h_score > a_score: is_green = True
            elif away in pick and a_score > h_score: is_green = True
        elif "Over" in pick:
            try: val = float(pick.split(' ')[1]); is_green = total > val
            except: pass
    else:
        if "Vitória do" in pick:
            if home in pick and h_score > a_score: is_green = True
            elif away in pick and a_score > h_score: is_green = True
        elif "Over 1.5" in pick and total > 1: is_green = True
        elif "Menos" in pick and total < 3: is_green = True 
        elif "Ambas" in pick and h_score > 0 and a_score > 0: is_green = True
        elif "Empate ou Visitante" in pick: is_green = (a_score >= h_score)
        elif "Empate ou Casa" in pick: is_green = (h_score >= a_score)
        elif "Empate" in pick: is_green = (h_score == a_score)

    if is_green:
        DAILY_STATS["green"] += 1
        icon = "🏀" if is_nba else "⚽"
        return f"✅ <b>GREEN CONFIRMADO!</b>\n{icon} {home} {h_score} x {a_score} {away}\n🎯 Tip: {pick}"
    else:
        DAILY_STATS["red"] += 1
        icon = "🏀" if is_nba else "⚽"
        return f"❌ <b>RED</b>\n{icon} {home} {h_score} x {a_score} {away}\n🎯 Tip: {pick}"

def format_sniper_card(game, prob, odd):
    return (
        f"🚨 <b>ALERTA DE CONFIANÇA</b> 🚨\n➖➖➖➖➖➖➖➖➖➖\n🏆 <b>{game['league']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n⏰ <b>Começa em breve!</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        f"💎 <b>ALGORITMO DETECTOU:</b>\n📈 <b>Probabilidade Alta:</b> {prob}%\n🎯 <b>Odd Calculada:</b> {odd}\n"
    )

# ================= 8. AUTOMAÇÕES =================
async def automation_routine(app: Application):
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if agora.hour == 8 and agora.minute == 0:
            global ALERTED_SNIPER, PROCESSED_GAMES, ALERTED_LIVE, DAILY_STATS
            ALERTED_SNIPER.clear(); PROCESSED_GAMES.clear(); ALERTED_LIVE.clear()
            DAILY_STATS = {"green": 0, "red": 0}
            jogos = await fetch_espn_soccer()
            data_fmt = get_current_date_data()[1]
            if jogos:
                header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>Data: {data_fmt}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
                txt = header
                for g in jogos:
                    details = await get_match_details(g['league_code'], g['id'])
                    d1, d2, analise, conf, _, icon, sname = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
                    card = format_morning_card(g, d1, d2, analise, conf, icon, sname, details)
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
                data_fmt = get_current_date_data()[1]
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
                if g['status'] == 'in' and g['id'] not in ALERTED_LIVE:
                    details = await get_match_details(g['league_code'], g['id'])
                    s = details['stats']
                    if s['home_shots'] >= 12 and g['score_home'] <= g['score_away']:
                        msg = format_pressure_alert(g, g['home'], s['home_shots'])
                        await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                        ALERTED_LIVE.add(g['id'])
                        continue
                    if g.get('period', 0) >= 2:
                        _, _, _, _, ph, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
                        msg = None
                        if ph >= 60.0 and g['score_home'] <= g['score_away']:
                            msg = format_live_radar_card(g, g['home'], "está tropeçando", s)
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
        if TODAYS_NBA:
            await fetch_nba_professional()
            for g in TODAYS_NBA:
                if g['status'] == 'post' and g['id'] not in PROCESSED_GAMES:
                    msg = verify_green(g['pick'], g['score_home'], g['score_away'], g['home'], g['away'])
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
                        _, _, _, _, odd, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
                        if odd < 1.45:
                            prob_calc = int(100/odd)
                            txt = format_sniper_card(g, prob_calc, odd)
                            await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                            ALERTED_SNIPER.add(g['id'])
                except: pass
        await asyncio.sleep(60)

# ================= 9. MENU =================
def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🎫 Bilhete Ouro (Odd 10+)", callback_data="daily_ticket")],
        [InlineKeyboardButton("🏀 Grade NBA VIP", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V287</b>\nDual Engine: Elite + Arrastão Brasil.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    data_fmt = get_current_date_data()[1]
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Buscando grade DUPLA ({data_fmt})...</b>", parse_mode=ParseMode.HTML)
        await fetch_league_standings()
        jogos = await fetch_espn_soccer()
        if not jogos: await msg.edit_text("❌ Grade vazia."); return
        
        header = f"🦁 <b>DVD TIPS | FUTEBOL</b> 🦁\n📅 <b>{data_fmt}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos:
            details = await get_match_details(g['league_code'], g['id'])
            d1, d2, analise, conf, _, icon, sname = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
            card = format_morning_card(g, d1, d2, analise, conf, icon, sname, details)
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
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V287 DUAL ENGINE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_league_standings() 
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
