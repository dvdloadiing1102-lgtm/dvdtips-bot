import os
import asyncio
import logging
import random
import httpx
import threading
import unicodedata
import time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import wraps

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ================= CONFIGURAÇÃO DE LOGGING =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('/tmp/bot_v75.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# Validação de variáveis de ambiente críticas
REQUIRED_VARS = {
    "BOT_TOKEN": BOT_TOKEN,
    "ADMIN_ID": ADMIN_ID,
    "CHANNEL_ID": CHANNEL_ID
}

for var_name, var_value in REQUIRED_VARS.items():
    if not var_value:
        logger.error(f"❌ Variável de ambiente obrigatória não configurada: {var_name}")
        raise ValueError(f"Variável de ambiente '{var_name}' não pode estar vazia")

logger.info("✅ Todas as variáveis de ambiente obrigatórias foram configuradas")

# ================= RATE LIMITING =================
class RateLimiter:
    """Implementa rate limiting para requisições de API"""
    def __init__(self, calls_per_second=2):
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0
    
    async def wait(self):
        """Aguarda se necessário para respeitar rate limit"""
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

rate_limiter = RateLimiter(calls_per_second=2)

# ================= FILTRO ANTI-LIXO =================
BLACKLIST_KEYWORDS = [
    "WOMEN", "FEMININO", "FEM", "(W)", "LADIES", "GIRLS", "MULLER",
    "U19", "U20", "U21", "U23", "U18", "U17", "SUB-20", "SUB 19", "SUB-19",
    "SUB 20", "YOUTH", "JUNIORES", "JUVENIL", "RESERVE", "RES.", "AMATEUR", 
    "REGIONAL", "SRL", "VIRTUAL", "SIMULATED", "ESOCCER", "BATTLE"
]

# ================= LISTA VIP (SEUS TIMES) =================
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "JUVENTUDE", "CUIABA", 
    "GOIAS", "ATLETICO GO", "AMERICA MG", "REAL MADRID", "MANCHESTER CITY", 
    "BAYERN", "INTER DE MILAO", "PSG", "CHELSEA", "ATLETICO DE MADRID", 
    "BORUSSIA DORTMUND", "BENFICA", "JUVENTUS", "PORTO", "ARSENAL", "BARCELONA", 
    "LIVERPOOL", "MILAN", "NAPOLI", "ROMA", "BOCA JUNIORS", "RIVER PLATE", 
    "AL HILAL", "AL AHLY", "MONTERREY", "LAFC", "LEVERKUSEN", "SPORTING",
    "SEVILLA", "WEST HAM", "FEYENOORD", "RB LEIPZIG", "PSV", "CHAPECOENSE", 
    "CORITIBA", "REAL BETIS", "CAPIVARIANO"
]

def normalize_name(name):
    """Normaliza nomes removendo acentos e convertendo para maiúsculas"""
    if not name: 
        return ""
    try:
        return ''.join(c for c in unicodedata.normalize('NFD', name) 
                      if unicodedata.category(c) != 'Mn').upper()
    except Exception as e:
        logger.error(f"Erro ao normalizar nome '{name}': {e}")
        return ""

# ================= SERVER WEB (CORRIGIDO) =================
class FakeHandler(BaseHTTPRequestHandler):
    """Handler HTTP para health check do bot"""
    
    def do_GET(self):
        """Responde a requisições GET"""
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"BOT V75.1 - ONLINE")
            logger.debug(f"Health check recebido de {self.client_address[0]}")
        except Exception as e:
            logger.error(f"Erro ao responder health check: {e}")
    
    def log_message(self, format, *args):
        """Suprime logs do servidor HTTP"""
        pass  # Silencia logs padrão do HTTPServer

# ================= MOTOR INTELIGENTE (FIXTURES FIRST) =================
class SportsEngine:
    """Engine para buscar e processar dados de esportes"""
    
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
        logger.info("✅ SportsEngine inicializado")

    def get_today_date(self):
        """Retorna a data de hoje em UTC-3"""
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        """Busca jogos do dia (premium ou standard)"""
        logger.info(f"🔎 Buscando jogos em modo: {mode}")
        
        if THE_ODDS_API_KEY:
            try:
                sport_key = "soccer_uefa_champs_league" if mode == "soccer" else "basketball_nba"
                data = await self._fetch_the_odds(sport_key)
                if data:
                    logger.info(f"✅ Dados premium obtidos: {len(data)} jogos")
                    return {"type": "premium", "data": data}
            except Exception as e:
                logger.warning(f"⚠️ Erro ao buscar dados premium: {e}")

        data = await self._fetch_from_fixtures(mode)
        logger.info(f"✅ Dados standard obtidos: {len(data)} jogos")
        return {"type": "standard", "data": data}

    async def _fetch_from_fixtures(self, mode):
        """Busca fixtures da API Football"""
        try:
            host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
            date_str = self.get_today_date()
            
            url_fixtures = f"https://{host}/fixtures?date={date_str}"
            if mode == "nba": 
                url_fixtures += "&league=12&season=2025"
            
            logger.debug(f"Buscando fixtures de: {url_fixtures}")
            
            async with httpx.AsyncClient(timeout=25) as client:
                await rate_limiter.wait()  # Rate limiting
                r = await client.get(url_fixtures, headers=self.headers)
                r.raise_for_status()  # Lança erro se status >= 400
                
                response_data = r.json()
                all_games = response_data.get("response", [])
                logger.info(f"📊 Total de jogos recebidos: {len(all_games)}")
                
                relevant_games = []
                
                for item in all_games:
                    try:
                        h_name = item.get('teams', {}).get('home', {}).get('name', 'Unknown')
                        a_name = item.get('teams', {}).get('away', {}).get('name', 'Unknown')
                        fixture_id = item.get('fixture', {}).get('id')
                        league_name = item.get('league', {}).get('name', 'Unknown')
                        
                        h_norm = normalize_name(h_name)
                        a_norm = normalize_name(a_name)
                        full_name = f"{h_norm} {a_norm} {normalize_name(league_name)}"

                        if any(bad in full_name for bad in BLACKLIST_KEYWORDS):
                            logger.debug(f"🚫 Jogo filtrado: {h_name} x {a_name}")
                            continue
                        
                        score = 0
                        if any(vip in h_norm for vip in VIP_TEAMS_LIST) or any(vip in a_norm for vip in VIP_TEAMS_LIST):
                            score += 5000
                        if "FLAMENGO" in h_norm or "FLAMENGO" in a_norm: 
                            score += 2000
                        
                        if score > 0:
                            relevant_games.append({
                                "id": fixture_id,
                                "match": f"{h_name} x {a_name}",
                                "league": league_name,
                                "score": score,
                                "home": h_name,
                                "away": a_name
                            })
                    except Exception as e:
                        logger.error(f"Erro ao processar jogo: {e}")
                        continue

                relevant_games.sort(key=lambda x: x['score'], reverse=True)
                final_list = []
                
                for game in relevant_games[:8]:
                    try:
                        odd_val, odd_tip = await self._get_odds_for_fixture(client, host, game['id'])
                        final_list.append({
                            "match": game['match'],
                            "league": game['league'],
                            "odd": odd_val,
                            "tip": odd_tip
                        })
                    except Exception as e:
                        logger.error(f"Erro ao buscar odds para {game['match']}: {e}")
                        continue
                
                return final_list
        
        except httpx.HTTPError as e:
            logger.error(f"❌ Erro HTTP ao buscar fixtures: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Erro inesperado ao buscar fixtures: {e}")
            return []

    async def _get_odds_for_fixture(self, client, host, fixture_id):
        """Busca odds para um fixture específico"""
        try:
            if not fixture_id:
                logger.warning("⚠️ fixture_id vazio")
                return 0.0, "Indisponível"
            
            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6"
            await rate_limiter.wait()  # Rate limiting
            r = await client.get(url, headers=self.headers)
            
            data = r.json().get("response", [])
            
            if data and len(data) > 0:
                try:
                    odds = data[0].get('bookmakers', [{}])[0].get('bets', [{}])[0].get('values', [])
                    if odds:
                        fav = sorted(odds, key=lambda x: float(x.get('odd', 0)))[0]
                        return float(fav.get('odd', 0.0)), fav.get('value', 'Aguardando Odd')
                except (IndexError, KeyError, ValueError) as e:
                    logger.debug(f"Erro ao processar odds: {e}")
                    return 0.0, "Aguardando Odd"
            
            return 0.0, "Aguardando Odd"
        except Exception as e:
            logger.error(f"Erro ao buscar odds: {e}")
            return 0.0, "Indisponível"

    async def _fetch_the_odds(self, sport_key):
        """Busca dados premium da The Odds API"""
        try:
            params = {
                "apiKey": THE_ODDS_API_KEY,
                "regions": "br,uk,eu",
                "markets": "h2h",
                "oddsFormat": "decimal"
            }
            
            async with httpx.AsyncClient(timeout=10) as client:
                await rate_limiter.wait()  # Rate limiting
                r = await client.get(self.odds_base_url.format(sport=sport_key), params=params)
                r.raise_for_status()
                
                data = r.json()
                if not data or (isinstance(data, dict) and data.get("errors")):
                    logger.warning(f"⚠️ Erro na resposta da The Odds API: {data}")
                    return None
                
                results = []
                for event in data[:6]:
                    try:
                        home = event.get('home_team', 'Unknown')
                        away = event.get('away_team', 'Unknown')
                        full = normalize_name(f"{home} {away}")
                        
                        if any(bad in full for bad in BLACKLIST_KEYWORDS):
                            continue
                        
                        all_h = []
                        for b in event.get('bookmakers', []):
                            for m in b.get('markets', []):
                                for o in m.get('outcomes', []):
                                    if o.get('name') == home:
                                        all_h.append({"p": o.get('price', 0), "b": b.get('title', 'Unknown')})
                        
                        if not all_h:
                            continue
                        
                        best = max(all_h, key=lambda x: x['p'])
                        worst = min(all_h, key=lambda x: x['p'])
                        profit = (best['p'] - worst['p']) * 100
                        
                        results.append({
                            "match": f"{home} x {away}",
                            "odd": best['p'],
                            "book": best['b'],
                            "profit": round(profit, 2),
                            "league": "🏆 Lucro"
                        })
                    except Exception as e:
                        logger.error(f"Erro ao processar evento premium: {e}")
                        continue
                
                return results
        
        except httpx.HTTPError as e:
            logger.error(f"❌ Erro HTTP ao buscar The Odds: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Erro inesperado ao buscar The Odds: {e}")
            return None

engine = SportsEngine()

# ================= HANDLERS =================
async def start(u: Update, c):
    """Handler do comando /start"""
    try:
        if str(u.effective_user.id) != str(ADMIN_ID):
            logger.warning(f"⚠️ Usuário não autorizado tentou usar /start: {u.effective_user.id}")
            return
        
        logger.info(f"✅ /start executado por {u.effective_user.id}")
        kb = [["🔥 Top Jogos", "🏀 NBA"], ["💣 Troco do Pão", "✍️ Mensagem Livre"]]
        await u.message.reply_text("🦁 **PAINEL V75.1 - CAÇADOR ONLINE**", 
                                   reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    except Exception as e:
        logger.error(f"❌ Erro no handler /start: {e}")
        await u.message.reply_text("❌ Erro ao processar comando")

async def handle_request(u: Update, c, mode="soccer", is_multi=False):
    """Handler para requisições de jogos"""
    try:
        logger.info(f"🔎 Requisição de jogos: mode={mode}, is_multi={is_multi}")
        msg = await u.message.reply_text(f"🔎 Varrendo a agenda de hoje...")
        
        api_mode = "nba" if mode == "nba" else "soccer"
        result = await engine.get_matches(api_mode)
        data = result["data"]
        
        if not data:
            logger.info("ℹ️ Nenhum jogo VIP encontrado")
            return await msg.edit_text("❌ Nenhum jogo dos seus times VIP na agenda de hoje.")

        if is_multi:
            valid_games = [g for g in data if g['odd'] > 1.0]
            if len(valid_games) < 2:
                logger.info("ℹ️ Poucos jogos para múltipla")
                return await msg.edit_text("❌ Poucos jogos com odds para múltipla.")
            
            sel = random.sample(valid_games, min(5, len(valid_games)))
            odd_t = 1.0
            txt = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
            for g in sel:
                odd_t *= g['odd']
                txt += f"📍 {g['match']} (@{g['odd']})\n"
            txt += f"\n💰 **ODD TOTAL: @{odd_t:.2f}**"
        
        elif result["type"] == "premium":
            txt = f"🏆 **SCANNER DE VALOR**\n\n"
            for g in data:
                txt += f"⚔️ {g['match']}\n⭐ Odd: @{g['odd']} ({g['book']})\n💰 Lucro: +R$ {g['profit']}\n\n"
                
        else:
            txt = f"{'🏀' if mode=='nba' else '🔥'} **GRADE DE ELITE**\n\n"
            for g in data:
                icon = "🔴⚫" if "FLAMENGO" in normalize_name(g['match']) else "⭐"
                odd_txt = f"@{g['odd']}" if g['odd'] > 0 else "⏳ Aguardando"
                txt += f"{icon} {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {odd_txt}\n\n"

        kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="send")]]
        await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        await msg.delete()
        logger.info(f"✅ Jogos enviados com sucesso")
    
    except Exception as e:
        logger.error(f"❌ Erro no handler de requisição: {e}")
        try:
            await u.message.reply_text("❌ Erro ao processar requisição")
        except:
            pass

async def handle_free_text(u: Update, c):
    """Handler para texto livre"""
    try:
        if str(u.effective_user.id) != str(ADMIN_ID):
            logger.warning(f"⚠️ Usuário não autorizado tentou enviar mensagem: {u.effective_user.id}")
            return
        
        if any(k in u.message.text for k in ["Top", "NBA", "Troco", "Livre"]):
            return
        
        logger.info(f"📝 Mensagem livre recebida: {u.message.text[:50]}...")
        kb = [[InlineKeyboardButton("📤 Enviar para o Canal", callback_data="send")]]
        await u.message.reply_text(f"📝 **PRÉVIA:**\n\n{u.message.text}", reply_markup=InlineKeyboardMarkup(kb))
    
    except Exception as e:
        logger.error(f"❌ Erro no handler de texto livre: {e}")

async def callback_handler(u: Update, c):
    """Handler para callbacks de botões"""
    try:
        q = u.callback_query
        await q.answer()
        
        if q.data == "send":
            if not CHANNEL_ID:
                logger.error("❌ CHANNEL_ID não configurado")
                await q.edit_message_text("❌ Canal não configurado")
                return
            
            txt = q.message.text.replace("📝 PRÉVIA:\n\n", "")
            await c.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.MARKDOWN)
            await q.edit_message_text(txt + "\n\n✅ **POSTADO!**")
            logger.info(f"✅ Mensagem postada no canal {CHANNEL_ID}")
    
    except Exception as e:
        logger.error(f"❌ Erro no callback handler: {e}")
        try:
            await q.edit_message_text("❌ Erro ao postar mensagem")
        except:
            pass

# ================= MAIN =================
async def main():
    """Função principal do bot"""
    try:
        logger.info("🚀 Iniciando Bot V75.1...")
        
        # Iniciar servidor HTTP
        logger.info(f"🌐 Iniciando servidor HTTP na porta {PORT}")
        threading.Thread(
            target=lambda: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever(),
            daemon=True
        ).start()
        
        # Iniciar bot
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Regex("Top Jogos"), lambda u,c: handle_request(u,c,"soccer")))
        app.add_handler(MessageHandler(filters.Regex("NBA"), lambda u,c: handle_request(u,c,"nba")))
        app.add_handler(MessageHandler(filters.Regex("Troco do Pão"), lambda u,c: handle_request(u,c,"soccer", True)))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
        app.add_handler(CallbackQueryHandler(callback_handler))

        logger.info("✅ Handlers registrados com sucesso")
        
        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.initialize()
        await app.start()
        
        logger.info("✅ Bot iniciado com sucesso!")
        logger.info("🔄 Aguardando mensagens...")
        
        await app.updater.start_polling()
        while True:
            await asyncio.sleep(1)
    
    except Exception as e:
        logger.critical(f"❌ Erro crítico ao iniciar bot: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ Bot interrompido pelo usuário")
    except Exception as e:
        logger.critical(f"❌ Erro fatal: {e}")
