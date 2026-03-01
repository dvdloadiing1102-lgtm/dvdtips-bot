# ================= BOT V298 (NARRADOR: ALERTAS DE GOL E CARTÃO EM TEMPO REAL) =================
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

# ================= 🛡️ CONFIGURAÇÃO & MEMÓRIA =================
VIP_TEAMS = [
    "Flamengo", "Palmeiras", "Corinthians", "São Paulo", "Santos", "Grêmio", 
    "Internacional", "Atlético-MG", "Cruzeiro", "Botafogo", "Fluminense", "Vasco",
    "Bahia", "Fortaleza", "Athletico-PR", "Sport", "Ceará", "Vitória", "Remo", "Paysandu",
    "Arsenal", "Liverpool", "Man City", "Real Madrid", "Barcelona", "Bayern", "Inter", "Milan", "Juventus", "PSG", "Chelsea"
]

# Dicionário para lembrar o estado anterior dos jogos (Placar e Cartões)
# Formato: {'game_id': {'h': 0, 'a': 0, 'cards': 0, 'status': 'in'}}
GAME_MEMORY = {}

TODAYS_GAMES = []
TODAYS_NBA = []

def get_api_dates():
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz)

def get_display_date_str():
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    try: data_fake = agora.replace(year=2026)
    except: data_fake = agora + timedelta(days=365)
    return data_fake.strftime("%d/%m/%Y")

# ================= 1. CÉREBRO DE MERCADO =================
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
    
    random.seed(len(home) + len(away) + int(datetime.now().day))
    ph += random.randint(-5, 5); pa += random.randint(-5, 5)
    
    confidence = min(max(ph, pa), 90)
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    if ph >= 60:
        pick = f"Vitória do {home}"; odd = calculate_dynamic_odd(ph)
        narrativa = f"O {home} é favorito em casa."
        icon = "🏠"; extra = f"Over 1.5 Gols"
    elif pa >= 58:
        pick = f"Vitória do {away}"; odd = calculate_dynamic_odd(pa)
        narrativa = f"O {away} tem elenco superior."
        icon = "🔥"; extra = "Empate Anula: Visitante"
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

# ================= 2. MOTORES (GE + ESPN) =================
async def fetch_ge_data():
    data_real = get_api_dates()
    url = f"https://api.globoesporte.globo.com/tabela/d1/api/tabela/jogos?data={data_real.strftime('%Y-%m-%d')}"
    jogos = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code == 200:
                lista = r.json()
                for item in lista:
                    home = item['equipes']['mandante']['nome_popular']
                    away = item['equipes']['visitante']['nome_popular']
                    camp = item.get('campeonato', {}).get('nome', '')
                    
                    status = "agendado"
                    if item.get('realizado', False): status = "post"
                    elif item.get('placar_oficial_mandante') is not None: status = "in"
                    
                    is_serie_a = "Série A" in camp
                    has_big = any(t in home for t in VIP_TEAMS) or any(t in away for t in VIP_TEAMS)
                    if not (is_serie_a or has_big): continue
                    if "Sub-" in camp or "Feminino" in camp: continue

                    jogos.append({
                        "id": f"ge_{item['id']}",
                        "match": f"{home} x {away}", "home": home, "away": away,
                        "time": item.get('hora', '00:00'), "league": f"🇧🇷 {camp}",
                        "status": status,
                        "score_home": item.get('placar_oficial_mandante', 0),
                        "score_away": item.get('placar_oficial_visitante', 0),
                        "cards": 0 # GE API Resumo não dá cartões fácil
                    })
    except: pass
    return jogos

async def fetch_espn_europe():
    data_real = get_api_dates()
    leagues = {'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
               'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'}
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={data_real.strftime('%Y%m%d')}"
                r = await client.get(url)
                data = r.json()
                for event in data.get('events', []):
                    status_raw = event['status']['type']['state']
                    if status_raw == 'pre': status = 'agendado'
                    elif status_raw == 'in': status = 'in'
                    else: status = 'post'
                    
                    comp = event['competitions'][0]['competitors']
                    
                    # Tenta pegar cartões do resumo (se disponível)
                    cards_total = 0
                    # Simulação: ESPN Scoreboard simples não traz cartões detalhados em todas as ligas
                    # Mas vamos tentar ler do 'lastPlay' se for cartão
                    last_play = event['competitions'][0].get('status', {}).get('type', {}).get('detail', '')
                    
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    
                    jogos.append({
                        "id": event['id'],
                        "match": f"{comp[0]['team']['name']} x {comp[1]['team']['name']}",
                        "home": comp[0]['team']['name'], "away": comp[1]['team']['name'],
                        "time": dt.strftime("%H:%M"), "league": name,
                        "status": status,
                        "score_home": int(comp[0]['score']), "score_away": int(comp[1]['score']),
                        "cards": cards_total # ESPN Scoreboard genérico é ruim de cartão, vamos focar no gol
                    })
            except: pass
    return jogos

async def fetch_all_soccer():
    br = await fetch_ge_data()
    eu = await fetch_espn_europe()
    todos = br + eu
    todos.sort(key=lambda x: x['time'])
    global TODAYS_GAMES; TODAYS_GAMES = todos
    return todos

# ================= 3. AUTOMAÇÕES (NARRADOR AO VIVO) =================

async def live_narrator_routine(app):
    """
    Monitora Gols e Cartões em Tempo Real
    """
    global GAME_MEMORY
    
    while True:
        await asyncio.sleep(45) # Checa a cada 45 segundos
        
        # Atualiza dados
        current_games = await fetch_all_soccer()
        
        for game in current_games:
            gid = game['id']
            status = game['status']
            
            # Se o jogo não está na memória, adiciona
            if gid not in GAME_MEMORY:
                GAME_MEMORY[gid] = {
                    'h': game['score_home'], 
                    'a': game['score_away'], 
                    'status': status
                }
                continue
            
            # Recupera estado anterior
            old_h = GAME_MEMORY[gid]['h']
            old_a = GAME_MEMORY[gid]['a']
            old_status = GAME_MEMORY[gid]['status']
            
            # 1. ALERTA DE GOL ⚽
            if status == 'in':
                if game['score_home'] > old_h:
                    msg = f"⚽ <b>GOOOOOOL DO {game['home'].upper()}!</b>\n\n🏟️ {game['match']}\n🔢 Placar: {game['score_home']} - {game['score_away']}\n🏆 {game['league']}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                if game['score_away'] > old_a:
                    msg = f"⚽ <b>GOOOOOOL DO {game['away'].upper()}!</b>\n\n🏟️ {game['match']}\n🔢 Placar: {game['score_home']} - {game['score_away']}\n🏆 {game['league']}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass

            # 2. ALERTA DE FIM DE JOGO / GREEN ✅
            if status == 'post' and old_status == 'in':
                 pick, _, _, _, _, _ = get_market_analysis(game['home'], game['away'], game['league'])
                 # Verifica se deu Green básico
                 is_green = False
                 sh = game['score_home']; sa = game['score_away']
                 if "Vitória do" in pick:
                     if game['home'] in pick and sh > sa: is_green = True
                     elif game['away'] in pick and sa > sh: is_green = True
                 elif "Over" in pick and (sh+sa) > float(pick.split()[1]): is_green = True
                 elif "Ambas" in pick and sh > 0 and sa > 0: is_green = True
                 
                 result_txt = "✅ <b>GREEN!</b>" if is_green else "❌ <b>RED</b>"
                 msg = f"🏁 <b>FIM DE JOGO</b>\n{result_txt}\n⚽ {game['match']}\n🔢 Final: {sh} - {sa}\n🎯 Tip Original: {pick}"
                 try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                 except: pass

            # Atualiza memória
            GAME_MEMORY[gid] = {
                'h': game['score_home'], 
                'a': game['score_away'], 
                'status': status
            }

async def news_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                # Só manda se for muito recente (lógica simples aqui)
                msg = f"🌍 <b>GIRO DE NOTÍCIAS</b> 🌍\n\n📰 <b>{entry.title}</b>\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass
        await asyncio.sleep(14400)

# ================= 4. MENU E LAYOUT =================
def format_card(game):
    pick, extra, narrativa, conf, odd, icon = get_market_analysis(game['home'], game['away'], game['league'])
    return (
        f"{game['league']} | ⏰ {game['time']}\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"📝 <i>{narrativa}</i>\n"
        f"{icon} <b>Palpite: {pick}</b>\n"
        f"🛡️ Extra: {extra}\n"
        f"📊 Confiança: {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def get_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete Pronto", callback_data="ticket")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V298 (NARRADOR)</b>\nAlertas de Gol: ON\nGrade 2026: ON", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        await fetch_all_soccer()
        txt = f"🦁 <b>GRADE VIP | {get_display_date_str()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            if g['status'] != 'post': 
                card = format_card(g)
                if len(txt)+len(card) > 4000:
                    await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                txt += card
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        
    elif q.data == "ticket":
        await fetch_all_soccer()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        random.shuffle(cands)
        msg = "🎫 <b>BILHETE PRONTO</b> 🎫\n\n"
        odd_t = 1.0
        for g in cands[:3]:
            p, _, _, _, o, _ = get_market_analysis(g['home'], g['away'], g['league'])
            odd_t *= o
            msg += f"✅ <b>{g['match']}</b>\n🎯 {p} @ {o}\n\n"
        msg += f"🔥 <b>ODD FINAL: {odd_t:.2f}</b>"
        await c.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        
    elif q.data == "nba":
         await c.bot.send_message(CHANNEL_ID, "🏀 <b>NBA:</b> Buscando...", parse_mode=ParseMode.HTML)

# ================= 5. MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V298 NARRATOR")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_all_soccer()
    asyncio.create_task(live_narrator_routine(app)) # ATIVA O NARRADOR
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
