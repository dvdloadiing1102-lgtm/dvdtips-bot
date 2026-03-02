# ================= BOT V301 (FORTALEZA: LOGS DETALHADOS + PROTEÇÃO TOTAL) =================
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

# --- 1. CONFIGURAÇÃO INICIAL (LÊ PRIMEIRO) ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# Configuração de Logs (Para você ver o erro no Render)
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurações Globais
VIP_TEAMS = [
    "Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Santos", "Grêmio", 
    "Internacional", "Atlético-MG", "Cruzeiro", "Botafogo", "Fluminense", "Vasco",
    "Bahia", "Fortaleza", "Athletico-PR", "Sport", "Ceará", "Vitória", "Remo", "Paysandu",
    "Arsenal", "Liverpool", "Man City", "Real Madrid", "Barcelona", "Bayern", "Inter", "Milan", "Juventus", "PSG", "Chelsea"
]

GAME_MEMORY = {} 
TODAYS_GAMES = []
TODAYS_NBA = []
PROCESSED_GAMES = set()

# --- 2. FUNÇÕES DE SUPORTE (DEFINIDAS NO TOPO) ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura erros sem matar o bot"""
    logger.error(msg="ERRO CAPTURADO PELO BOT:", exc_info=context.error)

def get_api_dates():
    """Data REAL (2025) para API não travar"""
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    # Se for madrugada, volta um dia para pegar jogos da noite anterior
    if agora.hour < 5: return agora - timedelta(days=1)
    return agora

def get_display_date_str():
    """Data VISUAL (2026) para o usuário"""
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    try: data_fake = agora.replace(year=2026)
    except: data_fake = agora + timedelta(days=365)
    return data_fake.strftime("%d/%m/%Y")

def calculate_dynamic_odd(probability):
    if probability <= 0: return 2.00
    fair_odd = 100 / probability
    return round(fair_odd + random.uniform(0.05, 0.12), 2)

def get_market_analysis(home, away, league_name):
    h_weight = 50; a_weight = 35
    if any(t in home for t in VIP_TEAMS): h_weight += 20
    if any(t in away for t in VIP_TEAMS): a_weight += 15
    
    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    # Randomização leve para não ficar estático
    random.seed(len(home) + len(away) + int(datetime.now().day))
    ph += random.randint(-5, 5); pa += random.randint(-5, 5)
    confidence = min(max(ph, pa), 90)
    
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    if ph >= 60:
        pick = f"Vitória do {home}"; odd = calculate_dynamic_odd(ph)
        narrativa = f"O {home} é favorito em casa."; icon = "🏠"; extra = f"Over 1.5 Gols"
    elif pa >= 58:
        pick = f"Vitória do {away}"; odd = calculate_dynamic_odd(pa)
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

# --- 3. MOTORES DE BUSCA (COM LOGS DE DEBUG) ---

async def fetch_ge_data():
    """Motor Brasil (Globo Esporte)"""
    data_real = get_api_dates()
    data_str = data_real.strftime('%Y-%m-%d')
    url = f"https://api.globoesporte.globo.com/tabela/d1/api/tabela/jogos?data={data_str}"
    
    logger.info(f"🔍 Buscando GE na data: {data_str}") # LOG IMPORTANTE
    jogos = []
    
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(url)
            if r.status_code == 200:
                lista = r.json()
                logger.info(f"✅ GE respondeu. Total de eventos brutos: {len(lista)}")
                
                for item in lista:
                    home = item['equipes']['mandante']['nome_popular']
                    away = item['equipes']['visitante']['nome_popular']
                    camp = item.get('campeonato', {}).get('nome', '')
                    
                    status = "agendado"
                    if item.get('realizado', False): status = "post"
                    elif item.get('placar_oficial_mandante') is not None: status = "in"
                    
                    # Filtro VIP
                    is_serie_a = "Série A" in camp
                    has_big = any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)
                    
                    if not (is_serie_a or has_big): continue # Pula jogo irrelevante
                    if "Sub-" in camp or "Feminino" in camp: continue

                    jogos.append({
                        "id": f"ge_{item['id']}",
                        "match": f"{home} x {away}", "home": home, "away": away,
                        "time": item.get('hora', '00:00'), "league": f"🇧🇷 {camp}",
                        "status": status,
                        "score_home": item.get('placar_oficial_mandante', 0),
                        "score_away": item.get('placar_oficial_visitante', 0)
                    })
            else:
                logger.error(f"❌ Erro GE API: Status {r.status_code}")
    except Exception as e:
        logger.error(f"❌ Exceção no GE: {e}")
        
    return jogos

async def fetch_espn_europe():
    """Motor Europa (ESPN)"""
    data_real = get_api_dates()
    leagues = {'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
               'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'}
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    logger.info("🔍 Buscando ESPN Europa...")
    async with httpx.AsyncClient(timeout=25) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={data_real.strftime('%Y%m%d')}"
                r = await client.get(url)
                if r.status_code != 200: continue
                data = r.json()
                for event in data.get('events', []):
                    status_raw = event['status']['type']['state']
                    if status_raw == 'pre': status = 'agendado'
                    elif status_raw == 'in': status = 'in'
                    else: status = 'post'
                    
                    comp = event['competitions'][0]['competitors']
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    jogos.append({
                        "id": event['id'],
                        "match": f"{comp[0]['team']['name']} x {comp[1]['team']['name']}",
                        "home": comp[0]['team']['name'], "away": comp[1]['team']['name'],
                        "time": dt.strftime("%H:%M"), "league": name,
                        "status": status,
                        "score_home": int(comp[0]['score']), "score_away": int(comp[1]['score'])
                    })
            except: pass
    logger.info(f"✅ ESPN processada. Total jogos Europeus: {len(jogos)}")
    return jogos

async def fetch_nba_professional():
    api_date = get_api_dates()
    date_str = api_date.strftime("%Y%m%d")
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
                    t_home = comp['competitors'][0]
                    t_away = comp['competitors'][1]
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

async def fetch_all_soccer():
    try:
        br = await fetch_ge_data()
        eu = await fetch_espn_europe()
        todos = br + eu
        todos.sort(key=lambda x: x['time'])
        global TODAYS_GAMES; TODAYS_GAMES = todos
        logger.info(f"📊 GRADE FINAL ATUALIZADA: {len(TODAYS_GAMES)} jogos totais.")
        return todos
    except Exception as e:
        logger.error(f"❌ Erro fatal no Fetch All: {e}")
        return []

# --- 4. AUTOMAÇÕES (LOOP PROTEGIDO) ---

async def live_narrator_routine(app):
    global GAME_MEMORY
    logger.info("🤖 Narrador Iniciado")
    while True:
        await asyncio.sleep(60)
        try:
            current_games = await fetch_all_soccer()
            for game in current_games:
                gid = game['id']; status = game['status']
                if gid not in GAME_MEMORY:
                    GAME_MEMORY[gid] = {'h': game['score_home'], 'a': game['score_away'], 'status': status}
                    continue
                
                old = GAME_MEMORY[gid]
                
                # Alerta de Gol
                if status == 'in' and (game['score_home'] > old['h'] or game['score_away'] > old['a']):
                    msg = f"⚽ <b>GOOL!</b>\n{game['match']}\nPlacar: {game['score_home']} - {game['score_away']}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                # Green
                if status == 'post' and old['status'] == 'in':
                    pick, _, _, _, _, _ = get_market_analysis(game['home'], game['away'], game['league'])
                    msg = f"🏁 <b>FIM DE JOGO</b>\n{game['match']}\nPlacar: {game['score_home']} - {game['score_away']}\nTip Era: {pick}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                GAME_MEMORY[gid] = {'h': game['score_home'], 'a': game['score_away'], 'status': status}
        except Exception as e:
            logger.error(f"Narrator Error: {e}")

async def automation_routine(app):
    logger.info("⏰ Rotina de Bom Dia armada")
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=-3)))
            if now.hour == 8 and now.minute == 0:
                await fetch_all_soccer()
                if TODAYS_GAMES:
                    txt = f"🦁 <b>BOM DIA! GRADE VIP | {get_display_date_str()}</b> 🦁\n\n"
                    for g in TODAYS_GAMES:
                        if g['status'] != 'post':
                            card = format_card(g)
                            if len(txt)+len(card) > 4000:
                                await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                            txt += card
                    if txt: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                await asyncio.sleep(65)
        except Exception as e:
            logger.error(f"Automation Error: {e}")
        await asyncio.sleep(30)

async def news_loop(app):
    logger.info("📰 News loop armado")
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

# --- 5. MENUS ---

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete Pronto", callback_data="ticket")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V301 (FORTALEZA)</b>\nLogs de Erro Ativos.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        msg = await q.message.reply_text("🔎 Buscando...")
        await fetch_all_soccer()
        if not TODAYS_GAMES:
            await msg.edit_text("❌ Grade vazia ou erro na API (Verifique Logs).")
            return
        txt = f"🦁 <b>GRADE VIP | {get_display_date_str()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            if g['status'] != 'post':
                card = format_card(g)
                if len(txt)+len(card) > 4000:
                    await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()
        
    elif q.data == "ticket":
        await fetch_all_soccer()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        if not cands:
            await q.message.reply_text("❌ Sem jogos.")
            return
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
            await msg.edit_text("❌ Sem jogos da NBA hoje.")
            return
        txt = f"🏀 <b>NBA | {get_display_date_str()}</b>\n\n"
        for g in jogos: txt += format_nba_card(g)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.delete()

# --- 6. EXECUÇÃO PRINCIPAL (ORDEM CORRIGIDA) ---

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V301 FORTRESS")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 BOT INICIANDO...")
    await fetch_all_soccer()
    # Inicia rotinas sem travar
    asyncio.create_task(live_narrator_routine(app))
    asyncio.create_task(automation_routine(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # ADICIONA HANDLERS AGORA QUE ELES EXISTEM
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler) # Agora error_handler está definido lá em cima!
    
    logger.info("✅ Polling iniciando...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
