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

# --- AUTO-INSTALAÇÃO DE DEPENDÊNCIAS ---
try:
    from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, ApplicationBuilder
    from telegram.constants import ParseMode
    from telegram.error import Conflict, NetworkError
except ImportError:
    import subprocess
    print("⚠️ Instalando bibliotecas obrigatórias...")
    # Removido python-dotenv pois não é necessário no Render
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "httpx", "google-generativeai"])
    print("✅ Bibliotecas instaladas! Reiniciando...")
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÕES =================
# No Render, as variáveis vêm direto do sistema. Não precisa de .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000)) # Porta obrigatória do Render
DB_PATH = "betting_bot.db" # Nome fixo para simplificar
LOG_LEVEL = "INFO"

# Constantes do Bot
API_TIMEOUT = 25
VIP_LEAGUE_IDS = [39, 40, 41, 42, 48, 140, 141, 143, 78, 79, 529, 135, 136, 137, 61, 62, 66, 71, 72, 73, 475, 479, 2, 3, 13, 11, 203, 128]

# Emojis e Textos
EMOJI_SOCCER = "⚽"
EMOJI_BASKETBALL = "🏀"
EMOJI_ERROR = "❌"
EMOJI_SUCCESS = "✅"
MSG_WELCOME = f"👋 **BET TIPS PRO V36**\nBot Online e Corrigido!"

# Configuração de Log Simples (Para aparecer no Render)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= SERVIDOR WEB FAKE (PARA O RENDER NÃO DESLIGAR) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V36 ONLINE - OK")

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
        try: yield conn; conn.commit()
        except: conn.rollback(); raise
        finally: conn.close()
    
    def init_db(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_vip BOOLEAN DEFAULT 0, vip_expiry TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS vip_keys (key_code TEXT UNIQUE, expiry_date TEXT, used_by INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT UNIQUE, cache_data TEXT, expires_at TIMESTAMP)")
            
    def get_or_create_user(self, user_id):
        with self.get_connection() as conn:
            conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            
    def get_user(self, user_id):
        with self.get_connection() as conn:
            return conn.cursor().execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def create_vip_key(self, expiry):
        k = "VIP-" + secrets.token_hex(4).upper()
        with self.get_connection() as conn:
            conn.cursor().execute("INSERT INTO vip_keys (key_code, expiry_date) VALUES (?, ?)", (k, expiry))
        return k

    def use_vip_key(self, key, uid):
        with self.get_connection() as conn:
            k = conn.cursor().execute("SELECT * FROM vip_keys WHERE key_code = ? AND used_by IS NULL", (key,)).fetchone()
            if not k: return False
            conn.cursor().execute("UPDATE vip_keys SET used_by = ? WHERE key_code = ?", (uid, key))
            conn.cursor().execute("UPDATE users SET is_vip = 1, vip_expiry = ? WHERE user_id = ?", (k['expiry_date'], uid))
            return True

    def set_cache(self, key, data):
        exp = (datetime.now() + timedelta(minutes=15)).isoformat()
        with self.get_connection() as conn:
            conn.cursor().execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, json.dumps(data), exp))

    def get_cache(self, key):
        with self.get_connection() as conn:
            res = conn.cursor().execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > ?", (key, datetime.now().isoformat())).fetchone()
            return json.loads(res[0]) if res else None

# ================= API ESPORTES =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    async def get_matches(self):
        cached = self.db.get_cache("matches")
        if cached: return cached
        if not API_FOOTBALL_KEY: return []
        
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
        headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
        
        matches = []
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r_ft, r_bk = await asyncio.gather(
                    client.get(f"https://v3.football.api-sports.io/fixtures?date={today}", headers=headers),
                    client.get(f"https://v1.basketball.api-sports.io/games?date={today}", headers=headers_nba),
                    return_exceptions=True
                )
                # Processa Futebol
                if not isinstance(r_ft, Exception) and r_ft.status_code == 200:
                    for g in r_ft.json().get("response", []):
                        if g["league"]["id"] not in VIP_LEAGUE_IDS: continue
                        ts = g["fixture"]["timestamp"]
                        if datetime.fromtimestamp(ts) < datetime.now() - timedelta(hours=4): continue
                        matches.append({
                            "sport": "⚽", "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                            "league": g["league"]["name"], "time": (datetime.fromtimestamp(ts)-timedelta(hours=3)).strftime("%H:%M"),
                            "odd": round(random.uniform(1.5, 2.5), 2), "tip": "Casa", "ts": ts
                        })
                # Processa NBA
                if not isinstance(r_bk, Exception) and r_bk.status_code == 200:
                    for g in r_bk.json().get("response", []):
                        if g["league"]["id"] != 12: continue
                        ts = g["timestamp"]
                        matches.append({
                            "sport": "🏀", "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                            "league": "NBA", "time": (datetime.fromtimestamp(ts)-timedelta(hours=3)).strftime("%H:%M"),
                            "odd": round(random.uniform(1.4, 2.2), 2), "tip": "Over 215", "ts": ts
                        })
        except: pass
        
        if matches:
            matches.sort(key=lambda x: x["ts"])
            self.db.set_cache("matches", matches)
        return matches

# ================= HANDLERS =================
class Handlers:
    def __init__(self, db, api, ai): self.db, self.api, self.ai = db, api, ai
    
    async def start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        self.db.get_or_create_user(u.effective_user.id)
        kb = ReplyKeyboardMarkup([["📋 Jogos", "🚀 Múltipla"], ["🤖 Guru", "🎫 Status"], ["/admin"]], resize_keyboard=True)
        await u.message.reply_text(MSG_WELCOME, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        msg = await u.message.reply_text("🔄 ...")
        m = await self.api.get_matches()
        if not m: return await msg.edit_text("📭 Vazio")
        txt = "*📋 GRADE:*\n\n"
        for g in m[:20]: txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 {g['tip']} (@{g['odd']})\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        if not m or len(m)<4: return await u.message.reply_text("⚠️ Poucos jogos")
        sel = random.sample(m, 4)
        odd = 1.0
        txt = "*🚀 MÚLTIPLA:*\n"
        for g in sel: 
            odd *= g['odd']
            txt += f"• {g['match']} ({g['tip']})\n"
        txt += f"\n💰 *Total: {odd:.2f}*"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def guru(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("🤖 Pergunte:")
        c.user_data["guru"] = True

    async def text(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if c.user_data.get("guru"):
            c.user_data["guru"] = False
            if not self.ai: return await u.message.reply_text("❌ IA Off")
            msg = await u.message.reply_text("🤔 ...")
            try:
                res = await asyncio.to_thread(self.ai.generate_content, u.message.text)
                await msg.edit_text(f"🎓 *Guru:*\n{res.text}", parse_mode=ParseMode.MARKDOWN)
            except: await msg.edit_text("Erro IA")
        else: await u.message.reply_text("❓ Menu")

    async def status(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        usr = self.db.get_user(u.effective_user.id)
        st = f"✅ VIP até {usr['vip_expiry']}" if usr and usr['is_vip'] else "❌ Free"
        await u.message.reply_text(f"*🎫 STATUS:* {st}", parse_mode=ParseMode.MARKDOWN)

    async def admin(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if str(u.effective_user.id) != str(ADMIN_ID): return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Key", callback_data="gen")]])
        await u.message.reply_text("🔑 Admin", reply_markup=kb)

    async def cb(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if u.callback_query.data == "gen":
            k = self.db.create_vip_key((datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
            await u.callback_query.message.edit_text(f"🔑 `{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            k = c.args[0]
            if self.db.use_vip_key(k, u.effective_user.id): await u.message.reply_text("✅ OK!")
            else: await u.message.reply_text("❌ Erro")
        except: await u.message.reply_text("Use /ativar CHAVE")

# ================= MAIN =================
async def main():
    if not BOT_TOKEN: 
        print("❌ ERRO: Configure o BOT_TOKEN no Render!")
        return

    # 1. Inicia Web Server Falso (Thread)
    threading.Thread(target=start_fake_server, daemon=True).start()

    # 2. Inicia Serviços
    db = Database(DB_PATH)
    api = SportsAPI(db)
    ai = None
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai = genai.GenerativeModel('gemini-1.5-flash')
    
    h = Handlers(db, api, ai)

    # 3. Loop Anti-Crash
    while True:
        try:
            logger.info("🔥 Iniciando Bot...")
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            
            app.add_handler(CommandHandler("start", h.start))
            app.add_handler(CommandHandler("admin", h.admin))
            app.add_handler(CommandHandler("ativar", h.active))
            app.add_handler(MessageHandler(filters.Regex("^📋"), h.games))
            app.add_handler(MessageHandler(filters.Regex("^🚀"), h.multi))
            app.add_handler(MessageHandler(filters.Regex("^🤖"), h.guru))
            app.add_handler(MessageHandler(filters.Regex("^🎫"), h.status))
            app.add_handler(CallbackQueryHandler(h.cb))
            app.add_handler(MessageHandler(filters.TEXT, h.text))

            await app.initialize()
            await app.start()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            while True: 
                await asyncio.sleep(60)
                if not app.updater.running: raise RuntimeError("Bot parou!")

        except Conflict:
            logger.error("🚨 CONFLITO! Esperando 30s...")
            try: await app.shutdown()
            except: pass
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"❌ Erro: {e}")
            try: await app.shutdown()
            except: pass
            await asyncio.sleep(10)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass