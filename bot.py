import os
import sys
import asyncio
import logging
import sqlite3
import json
import secrets
import random
import httpx
import google.generativeai as genai
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, List, Any
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "seu_token_aqui")
ADMIN_ID = os.getenv("ADMIN_ID", "seu_admin_id")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "sua_chave_gemini")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "sua_chave_api_sports")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = os.getenv("DB_PATH", "betting_bot.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

API_TIMEOUT = 25
CACHE_EXPIRY = 900
VIP_LEAGUE_IDS = [39, 40, 41, 42, 48, 140, 141, 143, 78, 79, 529, 135, 136, 137, 61, 62, 66, 71, 72, 73, 475, 479, 2, 3, 13, 11, 203, 128]

EMOJI_SOCCER = "⚽"
EMOJI_BASKETBALL = "🏀"
EMOJI_LOADING = "🔄"
EMOJI_EMPTY = "📭"
EMOJI_ERROR = "❌"
EMOJI_SUCCESS = "✅"
EMOJI_WARNING = "⚠️"
EMOJI_VIP = "🎫"
EMOJI_GURU = "🤖"
EMOJI_MULTIPLE = "🚀"
EMOJI_GAMES = "📋"
EMOJI_ADMIN = "🔑"
EMOJI_WELCOME = "👋"
EMOJI_MENU = "❓"

MSG_WELCOME = f"{EMOJI_WELCOME} **BET TIPS PRO V1**\nBem-vindo ao seu assistente de apostas!"
MSG_LOADING = f"{EMOJI_LOADING} Carregando..."
MSG_EMPTY = f"{EMOJI_EMPTY} Nenhum jogo disponível no momento."
MSG_ERROR = f"{EMOJI_ERROR} Erro ao processar sua solicitação."
MSG_IA_OFF = f"{EMOJI_ERROR} IA desativada no momento."
MSG_FEW_GAMES = f"{EMOJI_WARNING} Poucos jogos disponíveis para múltipla."
MSG_MENU = f"{EMOJI_MENU} Use o menu para navegar."

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=getattr(logging, LOG_LEVEL), handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Erro no banco de dados: {e}")
            raise
        finally:
            conn.close()
    
    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, is_vip BOOLEAN DEFAULT 0, vip_expiry TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS vip_keys (key_id INTEGER PRIMARY KEY AUTOINCREMENT, key_code TEXT UNIQUE NOT NULL, expiry_date TEXT NOT NULL, used_by INTEGER, used_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS betting_history (bet_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, match_info TEXT NOT NULL, tip TEXT NOT NULL, odds REAL NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(user_id))")
            cursor.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_id INTEGER PRIMARY KEY AUTOINCREMENT, cache_key TEXT UNIQUE NOT NULL, cache_data TEXT NOT NULL, expires_at TIMESTAMP NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, message TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
            logger.info("✅ Banco de dados inicializado")
    
    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            if user:
                return dict(user)
            cursor.execute("INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)", (user_id, username, first_name))
            conn.commit()
            logger.info(f"✅ Novo usuário criado: {user_id}")
            return {"user_id": user_id, "username": username, "first_name": first_name, "is_vip": False, "vip_expiry": None}
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None
    
    def update_user_vip(self, user_id: int, is_vip: bool, expiry: str = None) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_vip = ?, vip_expiry = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (is_vip, expiry, user_id))
            conn.commit()
            return cursor.rowcount > 0
    
    def create_vip_key(self, expiry_date: str) -> str:
        key_code = "VIP-" + secrets.token_hex(6).upper()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO vip_keys (key_code, expiry_date) VALUES (?, ?)", (key_code, expiry_date))
            conn.commit()
            logger.info(f"✅ Chave VIP criada: {key_code}")
            return key_code
    
    def get_vip_key(self, key_code: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM vip_keys WHERE key_code = ?", (key_code,))
            key = cursor.fetchone()
            return dict(key) if key else None
    
    def use_vip_key(self, key_code: str, user_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM vip_keys WHERE key_code = ?", (key_code,))
            key = cursor.fetchone()
            if not key or key["used_by"]:
                return False
            cursor.execute("UPDATE vip_keys SET used_by = ?, used_at = CURRENT_TIMESTAMP WHERE key_code = ?", (user_id, key_code))
            cursor.execute("UPDATE users SET is_vip = ?, vip_expiry = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (True, key["expiry_date"], user_id))
            conn.commit()
            logger.info(f"✅ Chave VIP ativada para usuário {user_id}")
            return True
    
    def delete_vip_key(self, key_code: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM vip_keys WHERE key_code = ?", (key_code,))
            conn.commit()
            return cursor.rowcount > 0
    
    def add_bet_history(self, user_id: int, match_info: str, tip: str, odds: float) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO betting_history (user_id, match_info, tip, odds) VALUES (?, ?, ?, ?)", (user_id, match_info, tip, odds))
            conn.commit()
            return cursor.rowcount > 0
    
    def get_user_history(self, user_id: int, limit: int = 10) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM betting_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    
    def set_cache(self, key: str, data: Dict, expiry_seconds: int) -> bool:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)).isoformat()
        data_json = json.dumps(data)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE api_cache SET cache_data = ?, expires_at = ? WHERE cache_key = ?", (data_json, expires_at, key))
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, data_json, expires_at))
            conn.commit()
            return True
    
    def get_cache(self, key: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > CURRENT_TIMESTAMP", (key,))
            result = cursor.fetchone()
            if result:
                return json.loads(result[0])
            return None
    
    def clear_expired_cache(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_cache WHERE expires_at <= CURRENT_TIMESTAMP")
            conn.commit()
            return cursor.rowcount

class SportsAPIService:
    def __init__(self, db):
        self.db = db
        self.football_host = "v3.football.api-sports.io"
        self.basketball_host = "v1.basketball.api-sports.io"
    
    def _get_headers(self, host: str) -> Dict[str, str]:
        return {"x-rapidapi-host": host, "x-rapidapi-key": API_FOOTBALL_KEY}
    
    async def get_real_matches(self) -> List[Dict]:
        cache_key = "real_matches"
        cached = self.db.get_cache(cache_key)
        if cached:
            logger.info("📦 Usando cache de partidas")
            return cached
        
        if not API_FOOTBALL_KEY:
            logger.warning("⚠️ Chave da API de esportes não configurada")
            return []
        
        matches = []
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                tasks = [
                    client.get(f"https://{self.football_host}/fixtures?date={today}", headers=self._get_headers(self.football_host)),
                    client.get(f"https://{self.basketball_host}/games?date={today}", headers=self._get_headers(self.basketball_host))
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)
                
                if not isinstance(responses[0], Exception) and responses[0].status_code == 200:
                    matches.extend(self._parse_football(responses[0].json()))
                
                if not isinstance(responses[1], Exception) and responses[1].status_code == 200:
                    matches.extend(self._parse_basketball(responses[1].json()))
        except Exception as e:
            logger.error(f"❌ Erro ao buscar partidas: {e}")
            return []
        
        if matches:
            matches.sort(key=lambda x: x["ts"])
            self.db.set_cache(cache_key, matches, 900)
            logger.info(f"✅ {len(matches)} partidas carregadas")
        
        return matches
    
    def _parse_football(self, data: Dict) -> List[Dict]:
        matches = []
        try:
            for game in data.get("response", []):
                if game["league"]["id"] not in VIP_LEAGUE_IDS:
                    continue
                ts = game["fixture"]["timestamp"]
                match_time = datetime.fromtimestamp(ts)
                if match_time < datetime.now() - timedelta(hours=4):
                    continue
                match = {
                    "sport": EMOJI_SOCCER,
                    "match": f"{game['teams']['home']['name']} x {game['teams']['away']['name']}",
                    "league": game["league"]["name"],
                    "time": (datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)).strftime("%H:%M"),
                    "odd": round(random.uniform(1.5, 2.5), 2),
                    "tip": "Over 2.5" if random.random() > 0.5 else "Casa",
                    "ts": ts,
                    "status": game["fixture"]["status"]["short"]
                }
                matches.append(match)
        except Exception as e:
            logger.error(f"❌ Erro ao processar futebol: {e}")
        return matches
    
    def _parse_basketball(self, data: Dict) -> List[Dict]:
        matches = []
        try:
            for game in data.get("response", []):
                if game["league"]["id"] != 12:
                    continue
                ts = game["timestamp"]
                match_time = datetime.fromtimestamp(ts)
                if match_time < datetime.now() - timedelta(hours=4):
                    continue
                match = {
                    "sport": EMOJI_BASKETBALL,
                    "match": f"{game['teams']['home']['name']} x {game['teams']['away']['name']}",
                    "league": "NBA",
                    "time": (datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)).strftime("%H:%M"),
                    "odd": round(random.uniform(1.4, 2.2), 2),
                    "tip": "Casa",
                    "ts": ts,
                    "status": game["status"]
                }
                matches.append(match)
        except Exception as e:
            logger.error(f"❌ Erro ao processar basquete: {e}")
        return matches
    
    @staticmethod
    def get_multiple(matches: List[Dict], count: int = 4) -> Optional[Dict]:
        if not matches or len(matches) < count:
            return None
        selected = random.sample(matches, count)
        total_odd = 1.0
        for match in selected:
            total_odd *= match["odd"]
        return {"games": selected, "total": round(total_odd, 2), "count": count}
    
    @staticmethod
    def format_matches_message(matches: List[Dict], limit: int = 25) -> str:
        if not matches:
            return "📭 Nenhuma partida disponível"
        message = "*📋 GRADE DE HOJE:*\n\n"
        for match in matches[:limit]:
            message += f"{match['sport']} {match['time']} | {match['league']}\n⚔️ {match['match']}\n👉 *{match['tip']}* (@{match['odd']})\n\n"
        return message
    
    @staticmethod
    def format_multiple_message(multiple: Dict) -> str:
        if not multiple:
            return "⚠️ Poucos jogos para múltipla"
        message = f"*🚀 MÚLTIPLA {multiple['count']}x:*\n\n"
        for game in multiple["games"]:
            message += f"• {game['sport']} {game['match']} ({game['tip']})\n"
        message += f"\n💰 *ODD TOTAL: {multiple['total']}*"
        return message

class AIService:
    def __init__(self):
        if GEMINI_API_KEY and GEMINI_API_KEY != "sua_chave_gemini":
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
            self.enabled = True
            logger.info("✅ IA Guru ativada")
        else:
            self.enabled = False
            logger.warning("⚠️ IA Guru desativada - chave não configurada")
    
    async def ask_guru(self, question: str) -> Optional[str]:
        if not self.enabled:
            return None
        try:
            response = await asyncio.to_thread(self.model.generate_content, question)
            if response and response.text:
                logger.info(f"✅ Resposta do Guru gerada com sucesso")
                return response.text
            return None
        except Exception as e:
            logger.error(f"❌ Erro ao chamar IA: {e}")
            return None
    
    @staticmethod
    def format_guru_response(response: str) -> str:
        return f"🎓 *Guru IA:*\n\n{response}"

class BotHandlers:
    def __init__(self, db, sports_api, ai_service):
        self.db = db
        self.sports_api = sports_api
        self.ai_service = ai_service
    
    @staticmethod
    def get_main_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            [f"{EMOJI_GAMES} Jogos de Hoje", f"{EMOJI_MULTIPLE} Múltipla 20x"],
            [f"{EMOJI_GURU} Guru IA", f"{EMOJI_VIP} Meu Status"],
            ["/admin"]
        ], resize_keyboard=True)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            user_id = user.id
            self.db.get_or_create_user(user_id, username=user.username, first_name=user.first_name)
            await update.message.reply_text(MSG_WELCOME, reply_markup=self.get_main_keyboard(), parse_mode=ParseMode.MARKDOWN)
            logger.info(f"✅ Usuário iniciado: {user_id}")
        except Exception as e:
            logger.error(f"❌ Erro em /start: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def show_games(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            msg = await update.message.reply_text(MSG_LOADING)
            matches = await self.sports_api.get_real_matches()
            if not matches:
                await msg.edit_text(MSG_EMPTY)
                return
            text = self.sports_api.format_matches_message(matches)
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"✅ Jogos exibidos para usuário {update.effective_user.id}")
        except Exception as e:
            logger.error(f"❌ Erro em show_games: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def show_multiple(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            msg = await update.message.reply_text(MSG_LOADING)
            matches = await self.sports_api.get_real_matches()
            multiple = self.sports_api.get_multiple(matches, count=4)
            if not multiple:
                await msg.edit_text(MSG_FEW_GAMES)
                return
            text = self.sports_api.format_multiple_message(multiple)
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"✅ Múltipla exibida para usuário {update.effective_user.id}")
        except Exception as e:
            logger.error(f"❌ Erro em show_multiple: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = update.effective_user.id
            user = self.db.get_user(user_id)
            if not user:
                status_text = f"{EMOJI_VIP} *STATUS:* ❌ Free"
            else:
                if user["is_vip"]:
                    expiry = user["vip_expiry"]
                    status_text = f"{EMOJI_VIP} *STATUS:* ✅ VIP\n📅 Válido até: {expiry}"
                else:
                    status_text = f"{EMOJI_VIP} *STATUS:* ❌ Free"
            await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"✅ Status exibido para usuário {user_id}")
        except Exception as e:
            logger.error(f"❌ Erro em show_status: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def guru_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not self.ai_service.enabled:
                await update.message.reply_text(MSG_IA_OFF)
                return
            await update.message.reply_text(f"{EMOJI_GURU} Faça sua pergunta sobre apostas:")
            context.user_data["guru_mode"] = True
            logger.info(f"✅ Modo Guru ativado para usuário {update.effective_user.id}")
        except Exception as e:
            logger.error(f"❌ Erro em guru_mode: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if context.user_data.get("guru_mode"):
                context.user_data["guru_mode"] = False
                msg = await update.message.reply_text(MSG_LOADING)
                response = await self.ai_service.ask_guru(update.message.text)
                if response:
                    text = self.ai_service.format_guru_response(response)
                    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
                else:
                    await msg.edit_text(MSG_ERROR)
                logger.info(f"✅ Pergunta do Guru respondida para usuário {update.effective_user.id}")
            else:
                await update.message.reply_text(MSG_MENU)
        except Exception as e:
            logger.error(f"❌ Erro em handle_text: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = str(update.effective_user.id)
            if user_id != str(ADMIN_ID):
                await update.message.reply_text(f"{EMOJI_ERROR} Acesso negado")
                return
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Gerar Chave VIP", callback_data="gen_key")],
                [InlineKeyboardButton("📊 Estatísticas", callback_data="stats")],
                [InlineKeyboardButton("🗑️ Limpar Cache", callback_data="clear_cache")]
            ])
            await update.message.reply_text(f"{EMOJI_ADMIN} *Painel Admin*", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"✅ Painel admin acessado por {user_id}")
        except Exception as e:
            logger.error(f"❌ Erro em admin_panel: {e}")
            await update.message.reply_text(MSG_ERROR)
    
    async def admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            user_id = str(query.from_user.id)
            if user_id != str(ADMIN_ID):
                await query.answer(f"{EMOJI_ERROR} Acesso negado", show_alert=True)
                return
            await query.answer()
            if query.data == "gen_key":
                expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                key = self.db.create_vip_key(expiry)
                await query.edit_message_text(f"{EMOJI_SUCCESS} *Chave Gerada:*\n`{key}`\n\nVálida até: {expiry}", parse_mode=ParseMode.MARKDOWN)
                logger.info(f"✅ Chave VIP gerada: {key}")
            elif query.data == "stats":
                stats_text = f"{EMOJI_ADMIN} *Estatísticas:*\n\n📊 Sistema operacional"
                await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN)
            elif query.data == "clear_cache":
                cleared = self.db.clear_expired_cache()
                await query.edit_message_text(f"{EMOJI_SUCCESS} Cache limpo!\nRegistros removidos: {cleared}", parse_mode=ParseMode.MARKDOWN)
                logger.info(f"✅ Cache limpo: {cleared} registros")
        except Exception as e:
            logger.error(f"❌ Erro em admin_callback: {e}")
            await update.callback_query.answer(MSG_ERROR, show_alert=True)
    
    async def activate_vip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not context.args:
                await update.message.reply_text(f"{EMOJI_ERROR} Use: `/ativar CHAVE`", parse_mode=ParseMode.MARKDOWN)
                return
            key_code = context.args[0].upper()
            user_id = update.effective_user.id
            key = self.db.get_vip_key(key_code)
            if not key:
                await update.message.reply_text(f"{EMOJI_ERROR} Chave inválida")
                logger.warning(f"⚠️ Tentativa de ativar chave inválida: {key_code}")
                return
            if key["used_by"]:
                await update.message.reply_text(f"{EMOJI_ERROR} Chave já foi utilizada")
                logger.warning(f"⚠️ Tentativa de reusar chave: {key_code}")
                return
            if self.db.use_vip_key(key_code, user_id):
                await update.message.reply_text(f"{EMOJI_SUCCESS} VIP ativado com sucesso!\nVálido até: {key['expiry_date']}", parse_mode=ParseMode.MARKDOWN)
                logger.info(f"✅ VIP ativado para usuário {user_id}")
            else:
                await update.message.reply_text(f"{EMOJI_ERROR} Erro ao ativar chave")
        except Exception as e:
            logger.error(f"❌ Erro em activate_vip: {e}")
            await update.message.reply_text(MSG_ERROR)

db = None
sports_api = None
ai_service = None
handlers = None
app = None

async def initialize_services():
    global db, sports_api, ai_service, handlers
    try:
        logger.info("🚀 Inicializando serviços...")
        db = Database(DB_PATH)
        logger.info("✅ Banco de dados inicializado")
        sports_api = SportsAPIService(db)
        logger.info("✅ Serviço de esportes inicializado")
        ai_service = AIService()
        logger.info("✅ Serviço de IA inicializado")
        handlers = BotHandlers(db, sports_api, ai_service)
        logger.info("✅ Handlers inicializados")
        logger.info("✅ Todos os serviços inicializados com sucesso!")
        return True
    except Exception as e:
        logger.error(f"❌ Erro ao inicializar serviços: {e}")
        return False

def setup_handlers(application: Application):
    try:
        application.add_handler(CommandHandler("start", handlers.start))
        application.add_handler(CommandHandler("admin", handlers.admin_panel))
        application.add_handler(CommandHandler("ativar", handlers.activate_vip))
        application.add_handler(MessageHandler(filters.Regex(f"^📋"), handlers.show_games))
        application.add_handler(MessageHandler(filters.Regex(f"^🚀"), handlers.show_multiple))
        application.add_handler(MessageHandler(filters.Regex(f"^🤖"), handlers.guru_mode))
        application.add_handler(MessageHandler(filters.Regex(f"^🎫"), handlers.show_status))
        application.add_handler(CallbackQueryHandler(handlers.admin_callback))
        application.add_handler(MessageHandler(filters.TEXT, handlers.handle_text))
        logger.info("✅ Handlers configurados com sucesso")
    except Exception as e:
        logger.error(f"❌ Erro ao configurar handlers: {e}")
        raise

async def run_bot():
    global app
    if not BOT_TOKEN or BOT_TOKEN == "seu_token_aqui":
        logger.error("❌ BOT_TOKEN não configurado!")
        sys.exit(1)
    if not await initialize_services():
        logger.error("❌ Falha ao inicializar serviços")
        sys.exit(1)
    reconnect_attempts = 0
    max_reconnect_attempts = 5
    reconnect_delay = 5
    while True:
        try:
            logger.info("🔥 Iniciando bot (Modo Polling)...")
            app = Application.builder().token(BOT_TOKEN).build()
            setup_handlers(app)
            await app.initialize()
            await app.start()
            logger.info("✅ Bot iniciado com sucesso!")
            logger.info("🎯 Aguardando mensagens...")
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, poll_interval=1.0, timeout=30)
            logger.info("⏹️ Bot desligado normalmente")
            break
        except Conflict as e:
            logger.error(f"🚨 CONFLITO DETECTADO: {e}")
            logger.error("⚠️ Outro bot está rodando com este token!")
            logger.info(f"⏳ Aguardando {reconnect_delay}s antes de reconectar...")
            try:
                if app:
                    await app.stop()
                    await app.shutdown()
            except:
                pass
            reconnect_attempts += 1
            if reconnect_attempts >= max_reconnect_attempts:
                logger.error(f"❌ Máximo de tentativas de reconexão atingido ({max_reconnect_attempts})")
                sys.exit(1)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except NetworkError as e:
            logger.error(f"🌐 ERRO DE REDE: {e}")
            logger.info(f"⏳ Aguardando {reconnect_delay}s antes de reconectar...")
            try:
                if app:
                    await app.stop()
                    await app.shutdown()
            except:
                pass
            reconnect_attempts += 1
            if reconnect_attempts >= max_reconnect_attempts:
                logger.error(f"❌ Máximo de tentativas de reconexão atingido ({max_reconnect_attempts})")
                sys.exit(1)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except Exception as e:
            logger.error(f"❌ ERRO INESPERADO: {e}", exc_info=True)
            logger.info(f"⏳ Aguardando {reconnect_delay}s antes de reconectar...")
            try:
                if app:
                    await app.stop()
                    await app.shutdown()
            except:
                pass
            reconnect_attempts += 1
            if reconnect_attempts >= max_reconnect_attempts:
                logger.error(f"❌ Máximo de tentativas de reconexão atingido ({max_reconnect_attempts})")
                sys.exit(1)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)

async def main():
    try:
        await run_bot()
    except KeyboardInterrupt:
        logger.info("⏹️ Bot interrompido pelo usuário")
    except Exception as e:
        logger.error(f"❌ Erro fatal: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("✅ Bot finalizado")
    except Exception as e:
        logger.error(f"❌ Erro ao executar bot: {e}", exc_info=True)
        sys.exit(1)