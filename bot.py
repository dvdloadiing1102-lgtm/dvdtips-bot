import os
import logging
import asyncio
import feedparser
import httpx
import threading
import unicodedata
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

# Telegram Imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- LOGS ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# --- VARIÁVEIS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# APIs
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")  # Agenda de Futebol
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")      # Odds de Futebol + NBA Completa

SENT_LINKS = set()

# Times VIP (Para ordenar a lista e deixar os importantes no topo)
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "REAL MADRID", "MANCHESTER CITY",
    "BAYERN", "PSG", "CHELSEA", "LIVERPOOL", "ARSENAL", "BARCELONA", "BOCA JUNIORS", "RIVER PLATE",
    "LAKERS", "WARRIORS", "CELTICS", "HEAT", "BUCKS" # Adicionei times da NBA
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVER WEB (KEEP ALIVE) ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V93 ONLINE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

# --- NEWS JOB ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)

        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular"]
        blacklist = ["bbb", "festa", "namorada", "traição"]
        count = 0

        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            
            title = entry.title.lower()
            if any(w in title for w in whitelist) and not any(b in title for b in blacklist):
                try:
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=f"⚠️ **BOLETIM REAL**\n\n📰 {entry.title}\n🔗 {entry.link}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    SENT_LINKS.add(entry.link)
                    count += 1
                    if count >= 2: break
                except: pass
        if len(SENT_LINKS) > 500: SENT_LINKS.clear()
    except Exception as e:
        logger.error(f"News Error: {e}")

# ================= MOTOR V93 (CORRIGIDO) =================
class SportsEngine:
    def __init__(self):
        self.football_data_url = "https://api.football-data.org/v4"
        self.football_data_token = FOOTBALL_DATA_TOKEN
        
        self.theodds_url = "https://api.the-odds-api.com/v4"
        self.theodds_key = THE_ODDS_API_KEY

        # MAPA: ID da API de Agenda -> ID da API de Odds
        self.league_map = {
            "PL": "soccer_epl",             # Premier League
            "PD": "soccer_spain_la_liga",   # La Liga
            "BL1": "soccer_germany_bundesliga",
            "SA": "soccer_italy_serie_a",
            "FL1": "soccer_france_ligue_one",
            "BSA": "soccer_brazil_campeonato_brasileiro_serie_a",
            "CL": "soccer_uefa_champs_league"
        }

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_odds_from_the_odds(self, home, away, league_code):
        """Busca a odd correta baseada na liga."""
        if not self.theodds_key: return 0.0, 0.0

        # Traduz o código da liga. Se não tiver mapa, usa genérico ou retorna zero
        sport_key = self.league_map.get(league_code)
        if not sport_key: return 0.0, 0.0

        try:
            url = f"{self.theodds_url}/sports/{sport_key}/odds"
            params = {"apiKey": self.theodds_key, "regions": "eu,uk", "markets": "h2h", "oddsFormat": "decimal"}
            
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, params=params)
                if r.status_code != 200: return 0.0, 0.0
                
                data = r.json()
                # Procura o jogo na lista de odds
                for event in data:
                    h_api = normalize_name(event['home_team'])
                    a_api = normalize_name(event['away_team'])
                    h_req = normalize_name(home)
                    a_req = normalize_name(away)
                    
                    # Verifica se os nomes batem
                    if (h_req in h_api or h_api in h_req) and (a_req in a_api or a_api in a_req):
                        # Pega odds do primeiro bookmaker
                        outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
                        odd_h = next((x['price'] for x in outcomes if x['name'] == event['home_team']), 0)
                        odd_a = next((x['price'] for x in outcomes if x['name'] == event['away_team']), 0)
                        return odd_h, odd_a
        except: pass
        return 0.0, 0.0

    async def get_nba_matches(self):
        """Busca jogos e odds da NBA direto da The Odds API (pois Football-Data não tem basquete)."""
        if not self.theodds_key: return []
        
        try:
            url = f"{self.theodds_url}/sports/basketball_nba/odds"
            params = {"apiKey": self.theodds_key, "regions": "us,eu", "markets": "h2h", "oddsFormat": "decimal"}
            
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(url, params=params)
                if r.status_code != 200: return []
                
                data = r.json()
                games = []
                today = datetime.now(timezone.utc).date()

                for event in data:
                    # Filtra data (commence_time)
                    dt_obj = datetime.fromisoformat(event['commence_time'].replace("Z", "+00:00"))
                    # Se o jogo for hoje ou na madrugada de amanhã (fuso horário louco da NBA)
                    if dt_obj.date() < today: continue 

                    h = event['home_team']
                    a = event['away_team']
                    time = dt_obj.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
                    
                    # Tenta pegar odds
                    try:
                        outcomes = event['bookmakers'][0]['markets'][0]['outcomes']
                        odd_h = next((x['price'] for x in outcomes if x['name'] == h), 0)
                        odd_a = next((x['price'] for x in outcomes if x['name'] == a), 0)
                    except:
                        odd_h, odd_a = 0.0, 0.0

                    games.append({
                        "match": f"{h} x {a}", "league": "NBA", "time": time,
                        "home_odd": odd_h, "away_odd": odd_a, "score": 5000 # NBA tem prioridade
                    })
                return games
        except: return []

    async def get_matches(self, mode="soccer"):
        # SE FOR NBA, USA LÓGICA ESPECÍFICA
        if mode == "nba":
            return await self.get_nba_matches()

        # SE FOR FUTEBOL, USA FOOTBALL-DATA + THE ODDS
        if not self.football_data_token: return []

        try:
            headers = {"X-Auth-Token": self.football_data_token}
            all_games = []
            today = self.get_today_date()
            
            async with httpx.AsyncClient(timeout=30) as client:
                # Itera apenas nas ligas que temos mapeadas
                for comp_id in self.league_map.keys():
                    try:
                        url = f"{self.football_data_url}/competitions/{comp_id}/matches?dateFrom={today}&dateTo={today}"
                        r = await client.get(url, headers=headers)
                        
                        if r.status_code == 200:
                            data = r.json()
                            for match in data.get("matches", []):
                                if match['status'] not in ["SCHEDULED", "TIMED"]: continue
                                
                                h = match['homeTeam']['name']
                                a = match['awayTeam']['name']
                                dt = datetime.fromisoformat(match['utcDate'].replace("Z", "+00:00"))
                                time_br = dt.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
                                
                                score = 10
                                if any(v in normalize_name(h) for v in VIP_TEAMS_LIST) or any(v in normalize_name(a) for v in VIP_TEAMS_LIST):
                                    score += 5000
                                
                                all_games.append({
                                    "match": f"{h} x {a}",
                                    "league": match['competition']['name'],
                                    "league_code": comp_id, # Importante para achar a odd depois
                                    "time": time_br,
                                    "score": score,
                                    "home": h, "away": a
                                })
                    except: continue

            # Ordena e Pega Top 8
            all_games.sort(key=lambda x: x['score'], reverse=True)
            top_games = all_games[:8]
            
            final_list = []
            for game in top_games:
                # Busca odd cruzando dados
                h_odd, a_odd = await self.get_odds_from_the_odds(game['home'], game['away'], game['league_code'])
                
                final_list.append({
                    "match": game['match'], "league": game['league'], "time": game['time'],
                    "home_odd": h_odd, "away_odd": a_odd
                })
            return final_list
            
        except Exception as e:
            logger.error(f"Erro Geral: {e}")
            return []

engine = SportsEngine()

async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e: logger.error(f"Erro envio: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top Jogos (Futebol)", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
        [InlineKeyboardButton("🔧 Testar APIs", callback_data="test_api")]
    ]
    await update.message.reply_text("🦁 **PAINEL V93 - INTEGRADO**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- TESTE ---
    if data == "test_api":
        await query.message.reply_text(f"🔧 Conexões:\nFootball-Data: {'✅' if FOOTBALL_DATA_TOKEN else '❌'}\nThe Odds API: {'✅' if THE_ODDS_API_KEY else '❌'}")
        return

    # --- JOGOS ---
    await query.message.reply_text("🔎 Buscando Agenda & Odds...")
    
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ Nenhum jogo encontrado. (Verifique se é madrugada ou se as cotas da liga estão abertas).")
        return

    emoji = "🏀" if mode == "nba" else "🔥"
    msg = f"{emoji} **GRADE V93**\n\n"
    
    for g in games:
        # Formatação para não mostrar 0.00
        odd_h = f"@{g['home_odd']:.2f}" if g['home_odd'] > 0 else "🚫"
        odd_a = f"@{g['away_odd']:.2f}" if g['away_odd'] > 0 else "🚫"
        
        msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🏆 {g['league']}\n💰 Casa: {odd_h} | Visitante: {odd_a}\n\n"

    await enviar(context, msg)
    await query.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    if app.job_queue: app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
    app.run_polling()

if __name__ == "__main__":
    main()
