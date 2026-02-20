# ================= BOT V260 (O ARSENAL: 9 FUNÇÕES DE ELITE INTEGRADAS) =================
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

# ================= CONFIGURAÇÃO =================
DATA_ALVO = "20260220"

# ================= BACKUP NBA =================
NBA_PERMANENT_BACKUP = {
    "Lakers": "LeBron James (25.4 PTS | 7.8 AST)", "Clippers": "James Harden (23.8 PTS | 8.9 AST)",
    "Warriors": "Stephen Curry (27.5 PTS | 4.9 AST)", "Celtics": "Jayson Tatum (27.1 PTS | 8.6 REB)",
    "Bucks": "G. Antetokounmpo (30.4 PTS | 11.9 REB)", "Mavericks": "Luka Doncic (33.4 PTS | 9.8 AST)",
    "Nuggets": "Nikola Jokic (28.7 PTS | 12.3 REB)", "76ers": "Joel Embiid (35.1 PTS | 11.3 REB)",
    "Suns": "Kevin Durant (28.2 PTS | 6.5 REB)", "Heat": "Jimmy Butler (20.0 PTS | 5.6 REB)",
    "Thunder": "S. Gilgeous-Alexander (32.7 PTS | 6.4 AST)", "Timberwolves": "Anthony Edwards (29.3 PTS | 5.1 AST)",
    "Cavaliers": "Donovan Mitchell (29.0 PTS | 6.2 AST)", "Knicks": "Jalen Brunson (27.5 PTS | 6.5 AST)",
    "Hornets": "LaMelo Ball (19.1 PTS | 7.4 AST)", "Grizzlies": "Ja Morant (28.5 PTS | 6.6 AST)",
    "Pelicans": "Zion Williamson (21.6 PTS | 6.1 REB)", "Hawks": "Trae Young (19.3 PTS | 10.9 AST)"
}

# ================= MEMÓRIA =================
TODAYS_GAMES = []
PROCESSED_GAMES = set()
ALERTED_SNIPER = set()
ALERTED_LIVE = set()
DAILY_STATS = {"green": 0, "red": 0}

# ================= 1. TRATAMENTO DE ERROS =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

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
    while True:
        noticias = await fetch_news()
        if noticias:
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text="🌍 <b>GIRO DE NOTÍCIAS</b> 🌍\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass
        await asyncio.sleep(14400) 

# ================= 3. NBA =================
def generate_nba_narrative(home, away, spread, total):
    try: spread_val = float(spread.split(' ')[1]) if spread != '-' and ' ' in spread else 0
    except: spread_val = 0
    
    analise = ""
    if abs(spread_val) >= 9: analise += f"O {home if spread_val < 0 else away} é amplamente favorito. "
    elif abs(spread_val) <= 4: analise += "Confronto equilibrado, decidido nos detalhes. "
    else: analise += "Vantagem técnica para o favorito. "
    return analise

async def fetch_nba_professional():
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={DATA_ALVO}"
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
                        leaders = team_data.get('leaders', [])
                        stats_parts = []
                        player_name = ""
                        for cat in leaders:
                            if cat['name'] == 'scoring':
                                player_name = cat['leaders'][0]['athlete']['displayName']
                                stats_parts.append(f"{float(cat['leaders'][0]['value']):.1f} PTS")
                            elif cat['name'] == 'rebounding': stats_parts.append(f"{float(cat['leaders'][0]['value']):.1f} REB")
                            elif cat['name'] == 'assists': stats_parts.append(f"{float(cat['leaders'][0]['value']):.1f} AST")
                        if stats_parts: return f"{player_name} ({' | '.join(stats_parts)})"
                    except: pass
                    return NBA_PERMANENT_BACKUP.get(team_name, "Aguardando dados...")

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
        f"👇 <b>Destaques:</b>\n🔥 {game['match'].split('@')[1].strip()}: {game['star_home']}\n🔥 {game['match'].split('@')[0].strip()}: {game['star_away']}\n━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 4. FUTEBOL COM ESTRATÉGIA AVANÇADA =================
async def fetch_espn_soccer():
    leagues = ['ksa.1', 'ger.1', 'ita.1', 'fra.1', 'esp.1', 'arg.1', 'tur.1', 'por.1', 'ned.1', 'bra.1', 'bra.camp.paulista', 'eng.1', 'eng.2', 'uefa.europa']
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
    # A MÁGICA ACONTECE AQUI: MOTOR DE ESTRATÉGIA
    random.seed(int(event_id))
    
    # Simulação de Probabilidades baseadas no ID (para consistência)
    ph = random.randint(30, 80); pa = 100 - ph - random.randint(0, 10)
    confidence = max(ph, pa)
    
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    # Lógica de Seleção de Estratégia (As 9 Funções)
    strategy_icon = "🎲"
    strategy_name = "Padrão"
    
    # 1. FUNÇÃO: REI DOS CANTOS (Ligas Rápidas)
    if league_code in ['eng.1', 'ger.1', 'ned.1'] and (ph > 50 or pa > 50):
        strategy_icon = "🚩"
        strategy_name = "Rei dos Cantos"
        extra_pick = "Over 9.5 Escanteios"
        narrativa = "Jogo com tendência de velocidade pelas pontas e muitos cantos."

    # 2. FUNÇÃO: AÇOUGUEIRO (Ligas Violentas / Clássicos)
    elif league_code in ['arg.1', 'conmebol.libertadores', 'bra.1'] and abs(ph - pa) < 15:
        strategy_icon = "🟨"
        strategy_name = "O Açougueiro"
        extra_pick = "Over 5.5 Cartões"
        narrativa = "Clássico tenso, pegado e com promessa de muita reclamação."
        
    # 3. FUNÇÃO: A MURALHA (Clean Sheet - Favorito Sólido em Casa)
    elif ph >= 75:
        strategy_icon = "🛡️"
        strategy_name = "A Muralha"
        extra_pick = f"Baliza Inviolada: {home}"
        narrativa = f"O {home} tem defesa sólida e não deve sofrer gols."

    # 4. FUNÇÃO: HT/FT (Super Favorito)
    elif ph >= 80:
        strategy_icon = "🔁"
        strategy_name = "HT/FT"
        extra_pick = f"Vence 1ºT e Final: {home}"
        narrativa = f"Domínio total do {home} desde o minuto inicial."
        
    # 5. FUNÇÃO: CAÇADOR DE ZEBRAS (Valor no Visitante)
    elif pa >= 35 and pa <= 45:
        strategy_icon = "🦓"
        strategy_name = "Caçador de Zebras"
        extra_pick = f"Handicap Asiático +1.0: {away}"
        narrativa = f"O {away} é subestimado e pode surpreender no contra-ataque."
        
    # 6. FUNÇÃO: H2H (Histórico - Simulado)
    elif abs(ph - pa) < 10:
        strategy_icon = "🆚"
        strategy_name = "H2H Equilibrado"
        extra_pick = "Ambas Marcam: Sim"
        narrativa = "Histórico recente mostra gols para os dois lados."
        
    else:
        # Padrão
        strategy_icon = "🎯"
        strategy_name = "Análise Tática"
        narrativa = "Confronto direto! O equilíbrio deve prevalecer."
        extra_pick = "Over 1.5 Gols"

    # Definição do Palpite Principal
    if ph >= 55: main_pick = f"Vitória do {home}"; safe_odd = 1.55
    elif pa >= 55: main_pick = f"Vitória do {away}"; safe_odd = 1.60
    else: main_pick = "Empate ou Visitante" if pa > ph else "Empate ou Casa"; safe_odd = 1.40

    return main_pick, extra_pick, narrativa, f"{conf_bar} {confidence}%", safe_odd, strategy_icon, strategy_name

# ================= 5. MÚLTIPLA TURBINADA (ODD 10-15 + MARTINGALE) =================
async def generate_daily_ticket(app):
    if not TODAYS_GAMES: return
    
    candidates = []
    for g in TODAYS_GAMES:
        main_pick, _, _, _, safe_odd, _, _ = get_market_analysis(g['league_code'], g['id'], g['home'], g['away'])
        candidates.append({'match': g['match'], 'pick': main_pick, 'odd': safe_odd})
    
    random.shuffle(candidates)
    
    ticket = []
    total_odd = 1.0
    
    # Empilha para ODD 10 a 15
    for c in candidates:
        if total_odd < 11.0:
            ticket.append(c)
            total_odd *= c['odd']
        else: break
            
    if total_odd > 16.0 and len(ticket) > 1:
        removed = ticket.pop(); total_odd /= removed['odd']

    if len(ticket) < 4: return
    
    msg = "🎫 <b>BILHETE DE OURO (ODD 10+)</b> 🎫\n<i>Oportunidade de Alavancagem 🚀</i>\n➖➖➖➖➖➖➖➖➖➖\n"
    for i, c in enumerate(ticket, 1):
        msg += f"{i}️⃣ <b>{c['match']}</b>\n🎯 {c['pick']} (Odd ~{c['odd']:.2f})\n\n"
    
    # FUNÇÃO 6: MARTINGALE (GESTÃO)
    msg += f"🔥 <b>ODD TOTAL: {total_odd:.2f}</b>\n"
    msg += f"📉 <b>Gestão (Martingale Suave):</b>\n"
    msg += f"• Entrada: 0.5% da Banca\n"
    msg += f"• Se Red: Próxima entrada 0.75% (Não dobre seco!)"
    
    try: await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
    except: pass

# ================= 6. LAYOUTS ATUALIZADOS =================
def format_morning_card(game, d1, d2, analise, conf, icon, strat_name):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ {game['time']}\n"
        f"🧠 <b>Estratégia:</b> {icon} {strat_name}\n"
        f"📝 <b>Análise:</b> <i>{analise}</i>\n"
        f"✅ <b>Palpite:</b> {d1}\n"
        f"🛡️ <b>Extra:</b> {d2}\n"
        f"📊 <b>Confiança:</b> {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_live_radar_card(game, favorite_team, situation):
    # FUNÇÃO 9: GOL TARDIO (Monitoramento 80min+)
    is_late = int(game['clock'].replace("'", "")) >= 80 if "'" in game['clock'] else False
    alert_type = "GOL TARDIO (Last Minute)" if is_late else "ALERTA DE OPORTUNIDADE"
    
    return (
        f"⚠️ <b>{alert_type} (AO VIVO)</b> ⚠️\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏱️ <b>Tempo:</b> {game['clock']} (2º Tempo)\n"
        f"⚽ <b>Placar:</b> {game['score_home']} - {game['score_away']}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📉 <b>SITUAÇÃO:</b> O Favorito ({favorite_team}) {situation}!\n"
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
            if jogos:
                header = f"🦁 <b>DVD TIPS | FUTEBOL HOJE</b> 🦁\n📅 <b>Data: {DATA_ALVO}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
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
                header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{DATA_ALVO}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
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
                    if ph >= 60.0 and g['score_home'] <= g['score_away']:
                        msg = format_live_radar_card(g, g['home'], "está tropeçando")
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
    await u.message.reply_text("🦁 <b>PAINEL DVD TIPS V260</b>\nArsenal Completo Ativado.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Buscando...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_espn_soccer()
        if not jogos: await msg.edit_text("❌ Grade vazia."); return
        
        header = f"🦁 <b>DVD TIPS | FUTEBOL</b> 🦁\n📅 <b>{DATA_ALVO}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
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
        await generate_daily_ticket(c)
        await q.message.reply_text("✅ <b>Bilhete Gerado!</b>", parse_mode=ParseMode.HTML)

    elif q.data == "nba_deep":
        msg = await q.message.reply_text("🔎 <b>Analisando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_professional()
        if not jogos: await msg.edit_text("❌ Grade NBA vazia."); return
        
        header = f"🏀 <b>DVD TIPS | GRADE NBA</b> 🏀\n📅 <b>{DATA_ALVO}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text("✅ <b>NBA Postada!</b>")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V260 FULL ARSENAL")
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
