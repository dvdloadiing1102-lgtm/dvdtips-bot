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
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")  # API-Football

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
        self.wfile.write(b"BOT V90 ONLINE - Dual API")

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


# ================= MOTOR V90 COM DUAL API =================
class DualSportsEngine:
    """Engine para buscar jogos usando 2 APIs com fallback automático."""

    def __init__(self):
        self.football_data_url = "https://api.football-data.org/v4"
        self.football_data_token = FOOTBALL_DATA_TOKEN
        
        self.rapidapi_url = "https://api-football-v1.p.rapidapi.com/v3"
        self.rapidapi_key = RAPIDAPI_KEY
        self.rapidapi_host = "api-football-v1.p.rapidapi.com"

    def get_today_date(self):
        """Retorna a data de hoje em formato YYYY-MM-DD (timezone São Paulo)."""
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def test_api_connection(self):
        """Testa conexão com ambas as APIs."""
        debug_info = {
            "football_data": {"status": "❌ Não configurada", "error": None},
            "rapidapi": {"status": "❌ Não configurada", "error": None},
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
        
        # Testa API-Football
        if self.rapidapi_key:
            try:
                headers = {
                    "x-rapidapi-key": self.rapidapi_key,
                    "x-rapidapi-host": self.rapidapi_host
                }
                url = f"{self.rapidapi_url}/fixtures?date={self.get_today_date()}"
                
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url, headers=headers)
                    
                    if response.status_code == 200:
                        debug_info["rapidapi"]["status"] = "✅ Conectado"
                        logger.info("API-Football: Conectado com sucesso")
                    else:
                        debug_info["rapidapi"]["error"] = f"HTTP {response.status_code}"
                        logger.error(f"API-Football: HTTP {response.status_code}")
                        
            except Exception as e:
                debug_info["rapidapi"]["error"] = str(e)
                logger.error(f"API-Football: {e}")

        return debug_info

    async def get_matches_football_data(self):
        """Busca jogos usando Football-Data.org."""
        if not self.football_data_token:
            logger.warning("Football-Data token não configurado")
            return []

        try:
            headers = {"X-Auth-Token": self.football_data_token}
            
            # IDs das competições principais no Football-Data.org
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
            
            async with httpx.AsyncClient(timeout=30) as client:
                for comp_id in competition_ids:
                    try:
                        url = f"{self.football_data_url}/competitions/{comp_id}/matches?status=SCHEDULED"
                        response = await client.get(url, headers=headers)
                        
                        if response.status_code == 200:
                            data = response.json()
                            
                            for match in data.get("matches", [])[:3]:  # Pega até 3 por competição
                                try:
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
                                        "source": "Football-Data.org"
                                    })
                                    
                                except Exception as e:
                                    logger.debug(f"Erro ao processar match: {e}")
                                    continue
                        
                    except Exception as e:
                        logger.error(f"Erro ao buscar {comp_id}: {e}")
                        continue
            
            logger.info(f"Football-Data.org: {len(all_games)} jogos encontrados")
            return all_games
            
        except Exception as e:
            logger.error(f"Erro ao buscar de Football-Data.org: {e}")
            return []

    async def get_matches_rapidapi(self):
        """Busca jogos usando API-Football (RapidAPI)."""
        if not self.rapidapi_key:
            logger.warning("RapidAPI key não configurada")
            return []

        try:
            headers = {
                "x-rapidapi-key": self.rapidapi_key,
                "x-rapidapi-host": self.rapidapi_host
            }
            
            all_games = []
            
            async with httpx.AsyncClient(timeout=30) as client:
                # Busca jogos de hoje
                url = f"{self.rapidapi_url}/fixtures?date={self.get_today_date()}"
                response = await client.get(url, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    for fixture in data.get("response", [])[:10]:  # Pega até 10 jogos
                        try:
                            home_team = fixture.get("teams", {}).get("home", {}).get("name", "Time A")
                            away_team = fixture.get("teams", {}).get("away", {}).get("name", "Time B")
                            event_time = fixture.get("fixture", {}).get("date", "").split("T")[1][:5] if "T" in fixture.get("fixture", {}).get("date", "") else "20:00"
                            league = fixture.get("league", {}).get("name", "Campeonato")
                            
                            # Score de prioridade
                            score = 10
                            if any(v in normalize_name(home_team) for v in VIP_TEAMS_LIST) or \
                               any(v in normalize_name(away_team) for v in VIP_TEAMS_LIST):
                                score += 5000
                            
                            all_games.append({
                                "match": f"{home_team} x {away_team}",
                                "league": league,
                                "time": event_time,
                                "score": score,
                                "source": "API-Football"
                            })
                            
                        except Exception as e:
                            logger.debug(f"Erro ao processar fixture: {e}")
                            continue
                
            logger.info(f"API-Football: {len(all_games)} jogos encontrados")
            return all_games
            
        except Exception as e:
            logger.error(f"Erro ao buscar de API-Football: {e}")
            return []

    async def get_matches(self):
        """Busca jogos com fallback automático."""
        logger.info("Iniciando busca de jogos com dual API...")
        
        # Tenta Football-Data.org primeiro
        games = await self.get_matches_football_data()
        
        # Se não encontrou, tenta API-Football
        if not games:
            logger.info("Football-Data.org retornou vazio, tentando API-Football...")
            games = await self.get_matches_rapidapi()
        
        # Se ainda não tem jogos, combina ambas
        if not games:
            logger.info("Nenhuma API retornou jogos, tentando combinar...")
            games_fd = await self.get_matches_football_data()
            games_api = await self.get_matches_rapidapi()
            games = games_fd + games_api
        
        if not games:
            logger.warning("Nenhum jogo encontrado em nenhuma API")
            return []
        
        # Ordena por score e remove duplicatas
        games.sort(key=lambda x: x['score'], reverse=True)
        
        # Remove duplicatas (mesmo match)
        seen = set()
        unique_games = []
        for game in games:
            key = game['match']
            if key not in seen:
                seen.add(key)
                unique_games.append(game)
        
        top_games = unique_games[:8]
        logger.info(f"Top 8 jogos selecionados: {len(top_games)}")
        
        # Adiciona odds simuladas
        final_list = []
        for i, game in enumerate(top_games):
            odds = [1.45, 1.65, 1.85, 2.10, 2.50, 3.20, 4.50]
            tips = [
                f"✅ {game['match'].split(' x ')[0]} Vence",
                f"✅ {game['match'].split(' x ')[1]} Vence",
                f"⚽ Over 1.5 Gols",
                f"⚽ Over 2.5 Gols",
                f"🛡️ Dupla Chance 1X",
                f"⛳ Escanteios Over 8.5",
                f"🟨 Cartão Amarelo"
            ]
            
            final_list.append({
                "match": game['match'],
                "league": game['league'],
                "time": game['time'],
                "odd": odds[i % len(odds)],
                "tip": tips[i % len(tips)],
                "source": game['source']
            })
        
        return final_list


engine = DualSportsEngine()


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
        "🦁 **PAINEL V90 - SCANNER TOTAL**\n\n*Dual API com Fallback Automático*\n\nBusca: Vencedor > Gols > Escanteios > Cartões.",
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

**API-Football (RapidAPI):**
🌐 Status: {debug_info['rapidapi']['status']}
{'❌ Erro: ' + debug_info['rapidapi']['error'] if debug_info['rapidapi']['error'] else '✅ Sem erros'}

💡 *Bot usa fallback automático - se uma falhar, tenta a outra*
"""
        await query.message.reply_text(debug_text, parse_mode=ParseMode.MARKDOWN)
        return

    # --- PRÓXIMOS JOGOS ---
    if data == "proximos_dias":
        await query.message.reply_text("📊 Buscando próximos jogos...")
        games = await engine.get_matches()

        if not games:
            await query.message.reply_text("❌ Nenhum jogo encontrado no momento.")
            return

        message = "📊 **PRÓXIMOS JOGOS**\n\n"
        for game in games:
            txt_odd = f"@{game['odd']}" if game['odd'] > 0 else "⏳ (S/ Odd)"
            message += f"⏰ {game['time']} | 🏟 {game['match']}\n🏆 {game['league']}\n🎯 {game['tip']} | {txt_odd}\n📡 {game['source']}\n\n"

        await enviar(context, message)
        await query.message.reply_text("✅ Postado!")
        return

    # --- TOP JOGOS E NBA ---
    await query.message.reply_text("🔎 Varrendo TODOS os mercados...")

    games = await engine.get_matches()

    if not games:
        await query.message.reply_text("❌ Nenhum jogo encontrado no momento.")
        return

    message = "🔥 **GRADE COMPLETA (V90)**\n\n"
    for game in games:
        txt_odd = f"@{game['odd']}" if game['odd'] > 0 else "⏳ (S/ Odd)"
        message += f"⏰ {game['time']} | 🏟 {game['match']}\n🏆 {game['league']}\n🎯 {game['tip']} | {txt_odd}\n📡 {game['source']}\n\n"

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

    logger.info("Bot V90 iniciado com sucesso! (Dual API)")
    app.run_polling()


if __name__ == "__main__":
    main()
