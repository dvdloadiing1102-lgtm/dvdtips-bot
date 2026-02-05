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
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")  # football-data.org
THEODDS_KEY = os.getenv("THEODDS_KEY")  # The Odds API

SENT_LINKS = set()

# Times VIP para priorizar
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "REAL MADRID", "MANCHESTER CITY",
    "BAYERN", "PSG", "CHELSEA", "LIVERPOOL", "ARSENAL", "BARCELONA", "BOCA JUNIORS", "RIVER PLATE"
]


def normalize_name(name):
    """Normaliza nomes removendo acentos e convertendo para maiúsculas."""
    if not name:
        return ""
    return ''.join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    ).upper()


# --- SERVER ---
class FakeHandler(BaseHTTPRequestHandler):
    """Handler HTTP para manter o servidor vivo no Render."""

    def do_GET(self):
        """Responde com status 200 para health checks."""
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V90 ONLINE - Football-Data + The Odds")

    def log_message(self, format, *args):
        """Suprime logs de requisições HTTP."""
        pass


def run_web_server():
    """Inicia servidor HTTP em thread separada."""
    try:
        server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
        logger.info(f"Servidor HTTP iniciado na porta {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Erro ao iniciar servidor HTTP: {e}")


# --- NEWS JOB ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    """Busca e envia notícias de futebol periodicamente."""
    try:
        def get_feed():
            return feedparser.parse("https://ge.globo.com/rss/ge/")

        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)

        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular"]
        blacklist = ["bbb", "festa", "namorada", "traição"]
        count = 0

        for entry in feed.entries:
            if entry.link in SENT_LINKS:
                continue

            title_lower = entry.title.lower()
            has_whitelist = any(w in title_lower for w in whitelist)
            has_blacklist = any(b in title_lower for b in blacklist)

            if has_whitelist and not has_blacklist:
                try:
                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=f"⚠️ **BOLETIM REAL**\n\n📰 {entry.title}\n🔗 {entry.link}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    SENT_LINKS.add(entry.link)
                    count += 1
                    if count >= 2:
                        break
                except Exception as e:
                    logger.error(f"Erro ao enviar notícia: {e}")

        # Limpa cache se ficar muito grande
        if len(SENT_LINKS) > 500:
            SENT_LINKS.clear()
            logger.info("Cache de links limpo")

    except Exception as e:
        logger.error(f"Erro no auto_news_job: {e}")


# ================= MOTOR V90 COM FOOTBALL-DATA + THE ODDS =================
class SportsEngine:
    """Engine para buscar jogos e odds usando Football-Data.org + The Odds API."""

    def __init__(self):
        self.football_data_url = "https://api.football-data.org/v4"
        self.football_data_token = FOOTBALL_DATA_TOKEN
        
        self.theodds_url = "https://api.the-odds-api.com/v4"
        self.theodds_key = THEODDS_KEY

    def get_today_date(self):
        """Retorna a data de hoje em formato YYYY-MM-DD (timezone São Paulo)."""
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def test_api_connection(self):
        """Testa conexão com as APIs."""
        debug_info = {
            "football_data": {"status": "❌ Não configurada", "error": None},
            "theodds": {"status": "❌ Não configurada", "error": None},
            "test_date": self.get_today_date()
        }

        # Testa Football-Data.org
        if self.football_data_token:
            try:
                headers = {"X-Auth-Token": self.football_data_token}
                url = f"{self.football_data_url}/competitions"
                
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, headers=headers)
                    
                    if response.status_code == 200:
                        debug_info["football_data"]["status"] = "✅ Conectado"
                        logger.info("Football-Data.org: Conectado com sucesso")
                    else:
                        debug_info["football_data"]["error"] = f"HTTP {response.status_code}"
                        logger.error(f"Football-Data.org: HTTP {response.status_code}")
                        
            except Exception as e:
                debug_info["football_data"]["error"] = str(e)
                logger.error(f"Football-Data.org: {e}")
        
        # Testa The Odds
        if self.theodds_key:
            try:
                url = f"{self.theodds_url}/sports"
                params = {"apiKey": self.theodds_key}
                
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, params=params)
                    
                    if response.status_code == 200:
                        debug_info["theodds"]["status"] = "✅ Conectado"
                        logger.info("The Odds: Conectado com sucesso")
                    else:
                        debug_info["theodds"]["error"] = f"HTTP {response.status_code}"
                        logger.error(f"The Odds: HTTP {response.status_code}")
                        
            except Exception as e:
                debug_info["theodds"]["error"] = str(e)
                logger.error(f"The Odds: {e}")

        return debug_info

    async def get_odds_for_match(self, home_team, away_team):
        """Busca odds reais do The Odds API para um match específico."""
        if not self.theodds_key:
            logger.warning("The Odds key não configurada")
            return None

        try:
            # Busca odds para futebol
            url = f"{self.theodds_url}/sports/soccer_epl/odds"
            params = {
                "apiKey": self.theodds_key,
                "markets": "h2h",
                "oddsFormat": "decimal"
            }
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Procura pelo match
                    for event in data.get("events", []):
                        event_home = normalize_name(event.get("home_team", ""))
                        event_away = normalize_name(event.get("away_team", ""))
                        
                        if event_home in normalize_name(home_team) or normalize_name(home_team) in event_home:
                            if event_away in normalize_name(away_team) or normalize_name(away_team) in event_away:
                                # Retorna as odds do primeiro bookmaker
                                bookmakers = event.get("bookmakers", [])
                                if bookmakers:
                                    markets = bookmakers[0].get("markets", [])
                                    if markets:
                                        outcomes = markets[0].get("outcomes", [])
                                        if len(outcomes) >= 2:
                                            return {
                                                "home_win": outcomes[0].get("price", 1.50),
                                                "away_win": outcomes[1].get("price", 2.50)
                                            }
                
        except Exception as e:
            logger.debug(f"Erro ao buscar odds: {e}")
        
        return None

    async def get_matches(self):
        """Busca jogos de hoje usando Football-Data.org."""
        if not self.football_data_token:
            logger.error("Football-Data token não configurado")
            return []

        try:
            headers = {"X-Auth-Token": self.football_data_token}
            
            # IDs das competições principais
            competition_ids = [
                "PL",      # Premier League
                "PD",      # La Liga
                "BL1",     # Bundesliga
                "SA",      # Serie A
                "FL1",     # Ligue 1
                "BSA",     # Brasileirão
                "CL",      # Champions League
            ]
            
            all_games = []
            today = self.get_today_date()
            
            async with httpx.AsyncClient(timeout=30) as client:
                for comp_id in competition_ids:
                    try:
                        url = f"{self.football_data_url}/competitions/{comp_id}/matches"
                        response = await client.get(url, headers=headers)
                        
                        if response.status_code == 200:
                            data = response.json()
                            
                            for match in data.get("matches", []):
                                try:
                                    # Filtra apenas jogos de hoje
                                    match_date = match.get("utcDate", "").split("T")[0]
                                    if match_date != today:
                                        continue
                                    
                                    # Filtra apenas jogos agendados
                                    if match.get("status") != "SCHEDULED":
                                        continue
                                    
                                    home_team = match.get("homeTeam", {}).get("name", "Time A")
                                    away_team = match.get("awayTeam", {}).get("name", "Time B")
                                    utc_date = match.get("utcDate", "")
                                    
                                    # Converte para hora local (São Paulo)
                                    if utc_date:
                                        dt = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
                                        dt_local = dt.astimezone(timezone(timedelta(hours=-3)))
                                        event_time = dt_local.strftime("%H:%M")
                                    else:
                                        event_time = "20:00"
                                    
                                    competition = match.get("competition", {}).get("name", "Campeonato")
                                    
                                    # Score de prioridade
                                    score = 10
                                    if any(v in normalize_name(home_team) for v in VIP_TEAMS_LIST) or \
                                       any(v in normalize_name(away_team) for v in VIP_TEAMS_LIST):
                                        score += 5000
                                    
                                    all_games.append({
                                        "match": f"{home_team} x {away_team}",
                                        "league": competition,
                                        "time": event_time,
                                        "score": score,
                                        "home": home_team,
                                        "away": away_team
                                    })
                                    
                                except Exception as e:
                                    logger.debug(f"Erro ao processar match: {e}")
                                    continue
                        
                    except Exception as e:
                        logger.error(f"Erro ao buscar {comp_id}: {e}")
                        continue
            
            logger.info(f"Football-Data.org: {len(all_games)} jogos encontrados para hoje")
            
            if not all_games:
                return []
            
            # Ordena por score
            all_games.sort(key=lambda x: x['score'], reverse=True)
            top_games = all_games[:8]
            
            logger.info(f"Top 8 jogos selecionados: {len(top_games)}")
            
            # Busca odds para cada jogo
            final_list = []
            for game in top_games:
                odds_data = await self.get_odds_for_match(game['home'], game['away'])
                
                if odds_data:
                    home_odd = odds_data.get("home_win", 1.50)
                    away_odd = odds_data.get("away_win", 2.50)
                else:
                    # Usa odds padrão se não encontrar
                    home_odd = 1.65
                    away_odd = 2.20
                
                final_list.append({
                    "match": game['match'],
                    "league": game['league'],
                    "time": game['time'],
                    "home_odd": home_odd,
                    "away_odd": away_odd
                })
            
            return final_list
            
        except Exception as e:
            logger.error(f"Erro ao buscar jogos: {e}")
            return []


engine = SportsEngine()


async def enviar(context, text):
    """Envia mensagem para o canal."""
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start - exibe menu principal."""
    keyboard = [
        [
            InlineKeyboardButton("🔥 Top Jogos (Scanner)", callback_data="top_jogos"),
            InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")
        ],
        [
            InlineKeyboardButton("🔧 Testar APIs", callback_data="test_api"),
            InlineKeyboardButton("📊 Próximos Jogos", callback_data="proximos_dias")
        ]
    ]
    await update.message.reply_text(
        "🦁 **PAINEL V90 - SCANNER TOTAL**\n\n*Football-Data.org + The Odds API*\n\nBusca: Vencedor > Gols > Escanteios > Cartões.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para botões inline."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- TESTE DE APIS ---
    if data == "test_api":
        await query.message.reply_text("🔧 Testando conexão com as APIs...")
        debug_info = await engine.test_api_connection()

        debug_text = f"""
🔧 **TESTE DE APIs**

📅 Data Testada: {debug_info['test_date']}

**Football-Data.org:**
🌐 Status: {debug_info['football_data']['status']}
{'❌ Erro: ' + debug_info['football_data']['error'] if debug_info['football_data']['error'] else '✅ Sem erros'}

**The Odds API:**
🌐 Status: {debug_info['theodds']['status']}
{'❌ Erro: ' + debug_info['theodds']['error'] if debug_info['theodds']['error'] else '✅ Sem erros'}

💡 *Odds reais do The Odds + Jogos do Football-Data.org*
"""
        await query.message.reply_text(debug_text, parse_mode=ParseMode.MARKDOWN)
        return

    # --- PRÓXIMOS JOGOS ---
    if data == "proximos_dias":
        await query.message.reply_text("📊 Buscando próximos jogos de hoje...")
        games = await engine.get_matches()

        if not games:
            await query.message.reply_text("❌ Nenhum jogo encontrado para hoje.")
            return

        message = "📊 **PRÓXIMOS JOGOS DE HOJE**\n\n"
        for game in games:
            message += f"⏰ {game['time']} | 🏟 {game['match']}\n"
            message += f"🏆 {game['league']}\n"
            message += f"💰 Vencedor Casa: @{game['home_odd']:.2f} | Visitante: @{game['away_odd']:.2f}\n\n"

        await enviar(context, message)
        await query.message.reply_text("✅ Postado!")
        return

    # --- TOP JOGOS ---
    await query.message.reply_text("🔎 Varrendo TODOS os mercados...")

    games = await engine.get_matches()

    if not games:
        await query.message.reply_text("❌ Nenhum jogo encontrado para hoje.")
        return

    message = "🔥 **GRADE COMPLETA (V90) - HOJE**\n\n"
    for game in games:
        message += f"⏰ {game['time']} | 🏟 {game['match']}\n"
        message += f"🏆 {game['league']}\n"
        message += f"💰 Vencedor Casa: @{game['home_odd']:.2f} | Visitante: @{game['away_odd']:.2f}\n\n"

    await enviar(context, message)
    await query.message.reply_text("✅ Postado!")


def main():
    """Função principal - inicializa o bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN não configurado!")
        return

    # Inicia servidor HTTP em thread separada
    threading.Thread(target=run_web_server, daemon=True).start()

    # Cria e configura aplicação do Telegram
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    # Adiciona job para buscar notícias a cada 30 minutos
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)

    logger.info("Bot V90 iniciado com sucesso! (Football-Data + The Odds)")
    app.run_polling()


if __name__ == "__main__":
    main()
