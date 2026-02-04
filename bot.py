import os
import sys
import asyncio
import logging
import sqlite3
import json
import secrets
import random
import threading
import httpx
import google.generativeai as genai
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, List, Any
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()

# ================= CONFIGURAÇÕES =================
# Nota: No Render, configure estas variáveis no "Environment"
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000)) # Porta obrigatória do Render
DB_PATH = os.getenv("DB_PATH", "betting_bot.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Constantes do Bot
API_TIMEOUT = 25
CACHE_EXPIRY = 900
VIP_LEAGUE_IDS = [39, 40, 41, 42, 48, 140, 141, 143, 78, 79, 529, 135, 136, 137, 61, 62, 66, 71, 72, 73, 475, 479, 2, 3, 13, 11, 203, 128]

# Emojis
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

# Mensagens
MSG_WELCOME = f"{EMOJI_WELCOME} **BET TIPS PRO V35**\nBem-vindo ao seu assistente de apostas!"
MSG_LOADING = f"{EMOJI_LOADING} Carregando..."
MSG_EMPTY = f"{EMOJI_EMPTY} Nenhum jogo disponível no momento."
MSG_ERROR = f"{EMOJI_ERROR} Erro ao processar sua solicitação."
MSG_IA_OFF = f"{EMOJI_ERROR} IA desativada no momento."
MSG_FEW_GAMES = f"{EMOJI_WARNING} Poucos jogos disponíveis para múltipla."
MSG_MENU = f"{EMOJI_MENU} Use o menu para navegar."

# Configuração de Log
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=getattr(logging, LOG_LEVEL), 
    handlers=[logging.StreamHandler()] # No Render, StreamHandler é melhor que FileHandler
)
logger = logging.getLogger(__name__)

# ================= SERVIDOR WEB FAKE (PARA O RENDER) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT ONLINE - KEEP ALIVE")

def start_fake_server():
    """Inicia um servidor web simples para enganar o timeout do Render"""
    try:
        server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
        logger.info(f"🌍 WEB SERVER INICIADO NA PORTA {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Erro no Web Server: {e}")

# ================= BANCO DE DADOS =================
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
    
    def create_vip_key(self, expiry_date: str) -> str:
        key_code = "VIP-" + secrets.token_hex(6).upper()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO vip_keys (key_code, expiry_date) VALUES (?, ?)", (key_code, expiry_date))
            conn.commit()
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
            cursor.execute("UPDATE users SET is_vip = 1, vip_expiry = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (key["expiry_date"], user_id))
            conn.commit()
            return True
    
    def set_cache(self, key: str, data: Dict, expiry_seconds: int) -> bool:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expiry_seconds)).isoformat()
        data_json = json.dumps(data)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, data_json, expires_at))
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

# ================= API DE ESPORTES =================
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
                
                # Futebol
                if not isinstance(responses[0], Exception) and responses[0].status_code == 200:
                    data = responses[0].json().get("response", [])
                    for game in data:
                        if game["league"]["id"] not in VIP_LEAGUE_IDS: continue
                        ts = game["fixture"]["timestamp"]
                        if datetime.fromtimestamp(ts) < datetime.now() - timedelta(hours=4): continue
                        
                        matches.append({
                            "sport": EMOJI_SOCCER,
                            "match": f"{game['teams']['home']['name']} x {game['teams']['away']['name']}",
                            "league": game["league"]["name"],
                            "time": (datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)).strftime("%H:%M"),
                            "odd": round(random.uniform(1.5, 2.5), 2),
                            "tip": "Over 2.5" if random.random() > 0.5 else "Casa",
                            "ts": ts
                        })
                
                # Basquete
                if not isinstance(responses[1], Exception) and responses[1].status_code == 200:
                    data = responses[1].json().get("response", [])
                    for game in data:
                        if game["league"]["id"] != 12: continue
                        ts = game["timestamp"]
                        matches.append({
                            "sport": EMOJI_BASKETBALL,
                            "match": f"{game['teams']['home']['name']} x {game['teams']['away']['name']}",
                            "league": "NBA",
                            "time": (datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=3)).strftime("%H:%M"),
                            "odd": round(random.uniform(1.4, 2.2), 2),
                            "tip": "Casa",
                            "ts": ts
                        })

        except Exception as e:
            logger.error(f"❌ Erro ao buscar partidas: {e}")
            return []
        
        if matches:
            matches.sort(key=lambda x: x["ts"])
            self.db.set_cache(cache_key, matches, 900)
            logger.info(f"✅ {len(matches)} partidas carregadas da API")
        
        return matches
    
    @staticmethod
    def get_multiple(matches: List[Dict], count: int = 4) -> Optional[Dict]:
        if not matches or len(matches) < count: return None
        selected = random.sample(matches, count)
        total_odd = 1.0
        for match in selected: total_odd *= match["odd"]
        return {"games": selected, "total": round(total_odd, 2), "count": count}
    
    @staticmethod
    def format_matches_message(matches: List[Dict], limit: int = 25) -> str:
        if not matches: return MSG_EMPTY
        message = "*📋 GRADE DE HOJE:*\n\n"
        for match in matches[:limit]:
            message += f"{match['sport']} {match['time']} | {match['league']}\n⚔️ {match['match']}\n👉 *{match['tip']}* (@{match['odd']})\n\n"
        return message
    
    @staticmethod
    def format_multiple_message(multiple: Dict) -> str:
        if not multiple: return MSG_FEW_GAMES
        message = f"*🚀 MÚLTIPLA {multiple['count']}x:*\n\n"
        for game in multiple["games"]:
            message += f"• {game['sport']} {game['match']} ({game['tip']})\n"
        message += f"\n💰 *ODD TOTAL: {multiple['total']}*"
        return message

# ================= SERVIÇO DE IA =================
class AIService:
    def __init__(self):
        if GEMINI_API_KEY and GEMINI_API_KEY != "sua_chave_gemini":
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
            self.enabled = True
        else:
            self.enabled = False
            logger.warning("⚠️ IA Guru desativada - chave não configurada")
    
    async def ask_guru(self, question: str) -> Optional[str]:
        if not self.enabled: return None
        try:
            # Executa em thread separada para não bloquear o bot
            response = await asyncio.to_thread(self.model.generate_content, question)
            return response.text if response else None
        except Exception as e:
            logger.error(f"❌ Erro IA: {e}")
            return None

# ================= HANDLERS =================
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
            self.db.get_or_create_user(user.id, username=user.username, first_name=user.first_name)
            await update.message.reply_text(MSG_WELCOME, reply_markup=self.get_main_keyboard(), parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Erro start: {e}")
    
    async def show_games(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = await update.message.reply_text(MSG_LOADING)
        matches = await self.sports_api.get_real_matches()
        if not matches:
            await msg.edit_text(MSG_EMPTY)
        else:
            text = self.sports_api.format_matches_message(matches)
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def show_multiple(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = await update.message.reply_text(MSG_LOADING)
        matches = await self.sports_api.get_real_matches()
        multiple = self.sports_api.get_multiple(matches, count=4)
        if not multiple:
            await msg.edit_text(MSG_FEW_GAMES)
        else:
            text = self.sports_api.format_multiple_message(multiple)
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = self.db.get_user(update.effective_user.id)
        if user and user["is_vip"]:
            status_text = f"{EMOJI_VIP} *STATUS:* ✅ VIP\n📅 Válido até: {user['vip_expiry']}"
        else:
            status_text = f"{EMOJI_VIP} *STATUS:* ❌ Free"
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)
    
    async def guru_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.ai_service.enabled:
            await update.message.reply_text(MSG_IA_OFF)
            return
        await update.message.reply_text(f"{EMOJI_GURU} Faça sua pergunta sobre apostas:")
        context.user_data["guru_mode"] = True
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get("guru_mode"):
            context.user_data["guru_mode"] = False
            msg = await update.message.reply_text(MSG_LOADING)
            response = await self.ai_service.ask_guru(update.message.text)
            if response:
                await msg.edit_text(f"🎓 *Guru IA:*\n\n{response}", parse_mode=ParseMode.MARKDOWN)
            else:
                await msg.edit_text(MSG_ERROR)
        else:
            await update.message.reply_text(MSG_MENU)
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != str(ADMIN_ID): return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Gerar Chave VIP", callback_data="gen_key")],
            [InlineKeyboardButton("🗑️ Limpar Cache", callback_data="clear_cache")]
        ])
        await update.message.reply_text(f"{EMOJI_ADMIN} *Painel Admin*", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    
    async def admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if str(query.from_user.id) != str(ADMIN_ID):
            await query.answer("Acesso negado", show_alert=True)
            return
        await query.answer()
        
        if query.data == "gen_key":
            expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            key = self.db.create_vip_key(expiry)
            await query.edit_message_text(f"{EMOJI_SUCCESS} *Chave Gerada:*\n`{key}`\nValidade: {expiry}", parse_mode=ParseMode.MARKDOWN)
        elif query.data == "clear_cache":
            cleared = self.db.clear_expired_cache()
            await query.edit_message_text(f"{EMOJI_SUCCESS} Cache limpo ({cleared} registros).", parse_mode=ParseMode.MARKDOWN)
    
    async def activate_vip(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not context.args:
                await update.message.reply_text("Use: `/ativar CHAVE`", parse_mode=ParseMode.MARKDOWN)
                return
            key_code = context.args[0].upper()
            user_id = update.effective_user.id
            if self.db.use_vip_key(key_code, user_id):
                await update.message.reply_text(f"{EMOJI_SUCCESS} VIP ativado!", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(f"{EMOJI_ERROR} Chave inválida ou usada.")
        except:
            await update.message.reply_text(MSG_ERROR)

# ================= EXECUÇÃO PRINCIPAL =================
async def main():
    # Verifica se o token foi configurado
    if not BOT_TOKEN or BOT_TOKEN == "seu_token_aqui":
        print("❌ ERRO CRÍTICO: BOT_TOKEN não configurado no Environment!")
        return

    # 1. Inicia o Web Server Falso (Thread separada)
    # Isso impede que o Render mate o bot por falta de porta web
    server_thread = threading.Thread(target=start_fake_server, daemon=True)
    server_thread.start()

    # 2. Inicializa Serviços
    db = Database(DB_PATH)
    sports_api = SportsAPIService(db)
    ai_service = AIService()
    handlers = BotHandlers(db, sports_api, ai_service)

    # 3. Loop de Conexão com Retry (Anti-Conflito)
    while True:
        try:
            logger.info("🔥 Iniciando conexão com Telegram...")
            app = Application.builder().token(BOT_TOKEN).build()
            
            # Registra Handlers
            app.add_handler(CommandHandler("start", handlers.start))
            app.add_handler(CommandHandler("admin", handlers.admin_panel))
            app.add_handler(CommandHandler("ativar", handlers.activate_vip))
            app.add_handler(MessageHandler(filters.Regex("^📋"), handlers.show_games))
            app.add_handler(MessageHandler(filters.Regex("^🚀"), handlers.show_multiple))
            app.add_handler(MessageHandler(filters.Regex("^🤖"), handlers.guru_mode))
            app.add_handler(MessageHandler(filters.Regex("^🎫"), handlers.show_status))
            app.add_handler(CallbackQueryHandler(handlers.admin_callback))
            app.add_handler(MessageHandler(filters.TEXT, handlers.handle_text))

            # Inicia
            await app.initialize()
            await app.start()
            
            # Limpa webhook velho
            await app.bot.delete_webhook(drop_pending_updates=True)
            
            # Roda
            logger.info("✅ Bot Conectado!")
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            # Mantém vivo se polling rodar sem bloquear
            while True: await asyncio.sleep(3600)
            
        except Conflict:
            logger.error("🚨 CONFLITO DETECTADO! Outro bot está usando este token.")
            logger.info("⏳ Esperando 30s antes de tentar reconectar...")
            try: await app.shutdown() 
            except: pass
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"❌ Erro Geral: {e}")
            try: await app.shutdown()
            except: pass
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass