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
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000))

SENT_LINKS = set()

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
        self.wfile.write(b"BOT V90 ONLINE")

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


# ================= MOTOR V90 (VARREDURA COMPLETA) =================
class SportsEngine:
    """Engine para buscar e processar jogos de futebol e NBA."""

    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_dates_range(self, days_offset=0):
        """Retorna um range de datas para buscar (hoje e próximos dias)."""
        dates = []
        for i in range(days_offset, days_offset + 3):  # Busca 3 dias
            date = datetime.now(timezone.utc) - timedelta(hours=3) + timedelta(days=i)
            dates.append(date.strftime("%Y-%m-%d"))
        return dates

    def get_today_date(self):
        """Retorna a data de hoje em formato YYYY-MM-DD (timezone São Paulo)."""
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def test_api_connection(self):
        """Testa conexão com a API e retorna informações de debug."""
        debug_info = {
            "api_key_configured": bool(API_FOOTBALL_KEY),
            "api_key_length": len(API_FOOTBALL_KEY) if API_FOOTBALL_KEY else 0,
            "test_date": self.get_today_date(),
            "status": "❌ Erro",
            "error": None,
            "response_code": None,
            "fixtures_found": 0
        }

        if not API_FOOTBALL_KEY:
            debug_info["error"] = "API_FOOTBALL_KEY não configurada"
            logger.error("API_FOOTBALL_KEY não configurada!")
            return debug_info

        try:
            url = f"https://v3.football.api-sports.io/fixtures?date={debug_info['test_date']}&timezone=America/Sao_Paulo"
            logger.info(f"Testando API com URL: {url}")

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, headers=self.headers)
                debug_info["response_code"] = response.status_code

                if response.status_code == 200:
                    data = response.json()
                    debug_info["status"] = "✅ Conectado"
                    debug_info["fixtures_found"] = len(data.get("response", []))
                    logger.info(f"API respondeu com sucesso. Fixtures encontrados: {debug_info['fixtures_found']}")
                else:
                    debug_info["error"] = f"HTTP {response.status_code}"
                    logger.error(f"Erro HTTP: {response.status_code}")

                # Verifica se há erros na resposta
                if data.get("errors"):
                    debug_info["error"] = str(data.get("errors"))
                    logger.error(f"Erros da API: {data.get('errors')}")

        except Exception as e:
            debug_info["error"] = str(e)
            logger.error(f"Erro ao testar API: {e}")

        return debug_info

    async def get_matches(self, mode="soccer", days_offset=0):
        """Busca jogos com odds disponíveis (busca múltiplos dias se necessário)."""
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        dates = self.get_dates_range(days_offset)

        logger.info(f"Buscando jogos para as datas: {dates}")

        async with httpx.AsyncClient(timeout=30) as client:
            all_games = []

            # Tenta buscar em múltiplas datas
            for date_str in dates:
                try:
                    url = f"https://{host}/fixtures?date={date_str}&timezone=America/Sao_Paulo"
                    if mode == "nba":
                        url += "&league=12&season=2025"

                    logger.info(f"Buscando jogos para {date_str}...")
                    response = await client.get(url, headers=self.headers)
                    data = response.json()

                    if data.get("errors"):
                        logger.warning(f"Erro na API para {date_str}: {data.get('errors')}")
                        continue

                    response_list = data.get("response", [])
                    logger.info(f"Encontrados {len(response_list)} fixtures para {date_str}")

                    games_list = []

                    for item in response_list:
                        try:
                            home_team = item['teams']['home']['name']
                            away_team = item['teams']['away']['name']
                            fixture_id = item['fixture']['id']
                            league = item['league']['name']
                            match_time = datetime.fromisoformat(item['fixture']['date']).strftime("%H:%M")
                            full_name = normalize_name(f"{home_team} {away_team} {league}")

                            # Filtra categorias indesejadas
                            if "WOMEN" in full_name or "U20" in full_name:
                                logger.debug(f"Filtrando: {full_name}")
                                continue

                            # Calcula score de prioridade
                            score = 10
                            if any(v in normalize_name(home_team) for v in VIP_TEAMS_LIST) or \
                               any(v in normalize_name(away_team) for v in VIP_TEAMS_LIST):
                                score += 5000

                            if "FLAMENGO" in full_name:
                                score += 10000

                            if mode == "nba":
                                score += 2000

                            games_list.append({
                                "id": fixture_id,
                                "match": f"{home_team} x {away_team}",
                                "league": league,
                                "time": match_time,
                                "date": date_str,
                                "score": score,
                                "home": home_team,
                                "away": away_team
                            })

                        except Exception as e:
                            logger.debug(f"Erro ao processar jogo: {e}")
                            continue

                    all_games.extend(games_list)

                except Exception as e:
                    logger.error(f"Erro ao buscar jogos para {date_str}: {e}")
                    continue

            if not all_games:
                logger.warning("Nenhum jogo encontrado em nenhuma data")
                return []

            # Ordena por score e pega top 8
            all_games.sort(key=lambda x: x['score'], reverse=True)
            top_games = all_games[:8]

            logger.info(f"Top 8 jogos selecionados: {len(top_games)}")

            final_list = []

            # 2. Busca QUALQUER ODD (Vencedor -> Gols -> Escanteio -> Cartão)
            for game in top_games:
                odd_val, tip_str = await self._get_any_market(client, host, game['id'], game['home'], game['away'])

                final_list.append({
                    "match": game['match'],
                    "league": game['league'],
                    "time": game['time'],
                    "date": game['date'],
                    "odd": odd_val,
                    "tip": tip_str
                })

            return final_list

    async def _get_any_market(self, client, host, fixture_id, home_team, away_team):
        """Busca odds em ordem de prioridade: Vencedor > Gols > Dupla Chance > Escanteios > Cartões."""
        try:
            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6&timezone=America/Sao_Paulo"
            response = await client.get(url, headers=self.headers)
            data = response.json().get("response", [])

            if not data:
                return 0.0, "🔒 Aguardando Odd"

            bets = data[0]['bookmakers'][0]['bets']
            if not bets:
                return 0.0, "🔒 Mercado Fechado"

            # --- PRIORIDADE 1: VENCEDOR ---
            winner_bet = next((b for b in bets if b['id'] == 1), None)
            if winner_bet:
                home_odd = next((float(v['odd']) for v in winner_bet['values'] if v['value'] == 'Home'), 0)
                away_odd = next((float(v['odd']) for v in winner_bet['values'] if v['value'] == 'Away'), 0)

                if home_odd > 0 and away_odd > 0:
                    if home_odd < 1.65:
                        return home_odd, f"✅ {home_team} Vence"
                    if away_odd < 1.65:
                        return away_odd, f"✅ {away_team} Vence"

            # --- PRIORIDADE 2: GOLS (Over 1.5 ou 2.5) ---
            goals_bet = next((b for b in bets if b['id'] == 5), None)
            if goals_bet:
                over_odd = next((float(v['odd']) for v in goals_bet['values'] if 'Over' in v['value']), 0)
                if over_odd > 1:
                    return over_odd, f"⚽ {goals_bet['values'][0]['value']} Gols"

            # --- PRIORIDADE 3: DUPLA CHANCE ---
            double_chance = next((b for b in bets if b['id'] == 12), None)
            if double_chance:
                return float(double_chance['values'][0]['odd']), f"🛡️ {double_chance['values'][0]['value']}"

            # --- PRIORIDADE 4: ESCANTEIOS (CORNERS) ---
            corners_bet = next((b for b in bets if "Corner" in b['name'] or "Escanteio" in b['name']), None)
            if corners_bet:
                val = corners_bet['values'][0]
                return float(val['odd']), f"⛳ {corners_bet['name']} ({val['value']})"

            # --- PRIORIDADE 5: CARTÕES (CARDS) ---
            cards_bet = next((b for b in bets if "Card" in b['name'] or "Cartão" in b['name']), None)
            if cards_bet:
                val = cards_bet['values'][0]
                return float(val['odd']), f"🟨 {cards_bet['name']} ({val['value']})"

            # --- PRIORIDADE 6: DESESPERO (PEGA A PRIMEIRA DA LISTA) ---
            first_bet = bets[0]
            val = first_bet['values'][0]
            return float(val['odd']), f"🎲 {first_bet['name']} ({val['value']})"

        except Exception as e:
            logger.error(f"Erro ao buscar odds: {e}")
            return 0.0, "🔒 Indisponível"


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
            InlineKeyboardButton("🔧 Testar API", callback_data="test_api"),
            InlineKeyboardButton("📊 Próximos Dias", callback_data="proximos_dias")
        ]
    ]
    await update.message.reply_text(
        "🦁 **PAINEL V90 - SCANNER TOTAL**\n\nBusca: Vencedor > Gols > Escanteios > Cartões.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para botões inline."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- TESTE DE API ---
    if data == "test_api":
        await query.message.reply_text("🔧 Testando conexão com a API...")
        debug_info = await engine.test_api_connection()

        debug_text = f"""
🔧 **TESTE DE API**

✅ API Key Configurada: {'Sim' if debug_info['api_key_configured'] else 'Não'}
📏 Tamanho da Chave: {debug_info['api_key_length']} caracteres
📅 Data Testada: {debug_info['test_date']}
🌐 Status: {debug_info['status']}
📡 Código HTTP: {debug_info['response_code'] or 'N/A'}
⚽ Fixtures Encontrados: {debug_info['fixtures_found']}

{'❌ Erro: ' + debug_info['error'] if debug_info['error'] else '✅ Sem erros'}
"""
        await query.message.reply_text(debug_text, parse_mode=ParseMode.MARKDOWN)
        return

    # --- PRÓXIMOS DIAS ---
    if data == "proximos_dias":
        await query.message.reply_text("📊 Buscando jogos dos próximos 3 dias...")
        games = await engine.get_matches("soccer", days_offset=0)

        if not games:
            await query.message.reply_text("❌ Nenhum jogo encontrado nos próximos 3 dias.")
            return

        message = "📊 **JOGOS PRÓXIMOS 3 DIAS**\n\n"
        for game in games:
            txt_odd = f"@{game['odd']}" if game['odd'] > 0 else "⏳ (S/ Odd)"
            message += f"📅 {game['date']} | ⏰ {game['time']} | 🏟 {game['match']}\n🏆 {game['league']}\n🎯 {game['tip']} | {txt_odd}\n\n"

        await enviar(context, message)
        await query.message.reply_text("✅ Postado!")
        return

    # --- TOP JOGOS E NBA ---
    await query.message.reply_text("🔎 Varrendo TODOS os mercados...")

    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ Lista vazia. A API não retornou jogos para hoje.")
        return

    message = "🔥 **GRADE COMPLETA (V90)**\n\n"
    for game in games:
        txt_odd = f"@{game['odd']}" if game['odd'] > 0 else "⏳ (S/ Odd)"
        message += f"⏰ {game['time']} | 🏟 {game['match']}\n🏆 {game['league']}\n🎯 {game['tip']} | {txt_odd}\n\n"

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

    logger.info("Bot iniciado com sucesso!")
    app.run_polling()


if __name__ == "__main__":
    main()
