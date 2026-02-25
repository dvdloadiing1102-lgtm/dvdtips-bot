# ================= BOT V291 (MODO PATRIOTA: API GLOBO ESPORTE + ESPN EUROPA) =================
import os
import logging
import asyncio
import threading
import httpx
import feedparser
import random
import json
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

# ================= CONFIGURAÇÃO DATA =================
def get_current_date_data():
    br_tz = timezone(timedelta(hours=-3))
    agora = datetime.now(br_tz)
    if agora.hour < 5: 
        data_real = agora - timedelta(days=1)
    else: 
        data_real = agora
    
    # Data Visual (2026)
    try: data_display = data_real.replace(year=2026)
    except: data_display = data_real + timedelta(days=365)
    
    return data_real, data_display

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
    feeds = ["https://ge.globo.com/rss/ge/futebol/"]
    noticias = []
    try:
        for url in feeds:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:2]:
                noticias.append(f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler matéria</a>")
    except: pass
    return noticias

async def news_loop(app: Application):
    await asyncio.sleep(10)
    while True:
        noticias = await fetch_news()
        if noticias:
            try: await app.bot.send_message(chat_id=CHANNEL_ID, text="🌍 <b>GIRO GE</b> 🌍\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass
        await asyncio.sleep(14400) 

# ================= 3. BUSCA DE ODDS E ANÁLISE =================
def calculate_dynamic_odd(probability):
    if probability <= 0: return 2.00
    fair_odd = 100 / probability
    return round(fair_odd + random.uniform(0.05, 0.15), 2)

def get_market_analysis(home, away, league_name):
    # Lógica simplificada de análise baseada em nomes de peso
    GIGANTES_BRASIL = ["Flamengo", "Palmeiras", "Atlético-MG", "São Paulo", "Internacional", "Grêmio", "Fluminense", "Botafogo", "Fortaleza", "Cruzeiro", "Corinthians", "Vasco", "Bahia", "Athletico-PR"]
    GIGANTES_EUROPA = ["Real Madrid", "Man City", "Bayern", "Liverpool", "Inter", "Arsenal", "Barcelona", "PSG"]
    
    h_weight = 10
    a_weight = 10
    
    if any(g in home for g in GIGANTES_BRASIL): h_weight += 30
    if any(g in away for g in GIGANTES_BRASIL): a_weight += 20 # Fora pesa menos
    
    if any(g in home for g in GIGANTES_EUROPA): h_weight += 40
    if any(g in away for g in GIGANTES_EUROPA): a_weight += 35

    total = h_weight + a_weight
    ph = (h_weight / total) * 100
    pa = (a_weight / total) * 100
    
    # Ajuste de Mando de Campo
    ph += 10
    pa -= 10
    
    # Normalização
    if ph > 90: ph = 88
    if pa > 90: pa = 88
    if ph < 15: ph = 15
    if pa < 15: pa = 15

    confidence = max(ph, pa)
    bars = int(confidence / 10)
    conf_bar = "█" * bars + "░" * (10 - bars)
    
    strategy_icon = "🎯"
    strategy_name = "Análise Tática"
    
    if ph >= 65:
        pick = f"Vitória do {home}"
        odd = calculate_dynamic_odd(ph)
        narrativa = f"O {home} joga em casa e tem o favoritismo."
        strategy_icon = "🛡️"; strategy_name = "Muralha em Casa"
    elif pa >= 60:
        pick = f"Vitória do {away}"
        odd = calculate_dynamic_odd(pa)
        narrativa = f"O {away} é tecnicamente superior mesmo fora."
        strategy_icon = "🔥"; strategy_name = "Visitante indigesto"
    else:
        pick = "Over 1.5 Gols"
        odd = 1.45
        narrativa = "Jogo muito equilibrado, tendência a gols."
        
    return pick, "Over 0.5 HT" if confidence > 70 else "Menos de 3.5 Gols", narrativa, f"{conf_bar} {int(confidence)}%", odd, strategy_icon, strategy_name

# ================= 4. NBA (MANTIDA DA ESPN) =================
def format_nba_card(game):
    return f"🏀 <b>NBA</b>\n⚔️ {game['match']}\n✅ Palpite: {game['pick']}\n━━━━━━━━━━━━━━━━━━━━\n"

async def fetch_nba_professional():
    # Código NBA mantido simplificado para focar no Futebol
    return [] 

# ================= 5. FUTEBOL: O MOTOR HÍBRIDO =================

# --- MOTOR 1: GLOBO ESPORTE (BRASIL) ---
async def fetch_ge_data():
    """Busca jogos diretamente da API do Globo Esporte"""
    data_real, _ = get_current_date_data()
    data_str = data_real.strftime("%Y-%m-%d")
    
    url = f"https://api.globoesporte.globo.com/tabela/d1/api/tabela/jogos?data={data_str}"
    
    jogos_ge = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code == 200:
                lista = r.json()
                for item in lista:
                    # O GE retorna tudo, precisamos filtrar o que é relevante ou trazer tudo
                    # Estrutura do GE: item['campeonato']['nome'], item['equipes']['mandante']['nome_popular']
                    
                    camp = item.get('campeonato', {}).get('nome', 'Futebol Brasileiro')
                    home = item['equipes']['mandante']['nome_popular']
                    away = item['equipes']['visitante']['nome_popular']
                    hora = item.get('hora', '00:00')
                    local = item.get('sede', {}).get('nome_popular', 'Estádio')
                    
                    placar_h = item.get('placar_oficial_mandante')
                    placar_a = item.get('placar_oficial_visitante')
                    
                    status = "agendado"
                    if placar_h is not None: status = "encerrado"
                    
                    # Filtro Básico: Ignorar Sub-20 se quiser (ou manter se o usuário quiser tudo)
                    if "Sub-" in camp or "Feminino" in camp:
                        continue 

                    jogos_ge.append({
                        "id": f"ge_{home}_{away}",
                        "match": f"{home} x {away}",
                        "home": home, "away": away,
                        "time": hora,
                        "league": f"🏆 {camp}", # Nome bonito do GE
                        "stadium": f"🏟️ {local}",
                        "status": status,
                        "score_home": placar_h if placar_h else 0,
                        "score_away": placar_a if placar_a else 0,
                        "source": "GE"
                    })
    except Exception as e:
        logger.error(f"Erro GE: {e}")
        
    return jogos_ge

# --- MOTOR 2: ESPN (EUROPA/MUNDO) ---
async def fetch_espn_europe():
    """Busca apenas as ligas internacionais principais na ESPN"""
    data_real, _ = get_current_date_data()
    data_str = data_real.strftime("%Y%m%d")
    
    leagues = ['uefa.champions', 'eng.1', 'esp.1', 'ita.1', 'ger.1', 'ksa.1']
    jogos_espn = []
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={data_str}"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    league_name = data.get('leagues', [{}])[0].get('name', 'Futebol Int.')
                    
                    for event in data.get('events', []):
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']
                        away = comp[1]['team']['name']
                        date_obj = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-3)))
                        
                        jogos_espn.append({
                            "id": event['id'],
                            "match": f"{home} x {away}",
                            "home": home, "away": away,
                            "time": date_obj.strftime("%H:%M"),
                            "league": f"🏆 {league_name}",
                            "stadium": "",
                            "status": event['status']['type']['state'],
                            "score_home": int(comp[0]['score']),
                            "score_away": int(comp[1]['score']),
                            "source": "ESPN"
                        })
            except: pass
    return jogos_espn

# --- FUSÃO DOS MOTORES ---
async def fetch_all_soccer():
    # 1. Pega Brasil do GE (Prioridade)
    jogos_br = await fetch_ge_data()
    
    # 2. Pega Europa da ESPN
    jogos_eu = await fetch_espn_europe()
    
    # 3. Junta tudo e ordena por horário
    todos = jogos_br + jogos_eu
    todos.sort(key=lambda x: x['time'])
    
    global TODAYS_GAMES; TODAYS_GAMES = todos
    return todos

# ================= 6. LAYOUTS =================
def format_card(game):
    # Gera análise
    pick, extra, narrativa, conf, odd, icon, sname = get_market_analysis(game['home'], game['away'], game['league'])
    
    stadium_txt = f"{game['stadium']}\n" if game.get('stadium') else ""
    
    return (
        f"{game['league']}\n"
        f"⚔️ <b>{game['match']}</b>\n"
        f"⏰ {game['time']}\n{stadium_txt}"
        f"🧠 <b>Estratégia:</b> {icon} {sname}\n"
        f"📝 <b>Análise:</b> <i>{narrativa}</i>\n"
        f"✅ <b>Palpite:</b> {pick}\n"
        f"🛡️ <b>Extra:</b> {extra}\n"
        f"📊 <b>Confiança:</b> {conf}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# ================= 7. ROTINAS =================
async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    # Data para visualização
    _, data_visual = get_current_date_data()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text(f"🔎 <b>Varrendo API do GE e ESPN ({data_visual})...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_all_soccer()
        
        if not jogos:
            await msg.edit_text("❌ Grade vazia nas fontes oficiais (GE/ESPN).")
            return
            
        header = f"🦁 <b>DVD TIPS | GRADE OFICIAL</b> 🦁\n📅 <b>Data Simulada: {data_visual}</b>\n➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        txt = header
        
        # Filtro de exibição para não estourar o limite (prioriza Série A e Champions)
        count = 0
        for g in jogos:
            # Mostra tudo que é Brasileiro ou Champions, limita resto
            card = format_card(g)
            if len(txt) + len(card) > 4000:
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                txt = ""
            txt += card
            count += 1
            
        if txt: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        await msg.edit_text(f"✅ <b>Postado! {count} jogos encontrados.</b>")

# ================= 8. START =================
def get_menu(): 
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Puxar Grade (GE + ESPN)", callback_data="fut_market")]])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V291 - MODO PATRIOTA 🇧🇷</b>\nFonte Brasil: Globo Esporte (Oficial)\nFonte Europa: ESPN", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V291 GLOBO ESPORTE API")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()