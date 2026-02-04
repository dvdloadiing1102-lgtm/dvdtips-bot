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
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import Conflict

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "") # Padrão vazio para não quebrar
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"
LOG_LEVEL = "INFO"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# ================= SERVIDOR WEB FAKE =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V40 ONLINE")

def start_fake_server():
    try:
        server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
        server.serve_forever()
    except: pass

# ================= BANCO DE DADOS =================
class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    @contextmanager
    def get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try: yield conn; conn.commit()
        except: conn.rollback(); raise
        finally: conn.close()
    
    def init_db(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_vip BOOLEAN DEFAULT 0, vip_expiry TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS vip_keys (key_code TEXT UNIQUE, expiry_date TEXT, used_by INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT UNIQUE, cache_data TEXT, expires_at TIMESTAMP)")

    def get_or_create_user(self, uid):
        with self.get_conn() as conn:
            conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))

    def get_user(self, uid):
        with self.get_conn() as conn:
            res = conn.cursor().execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
            return dict(res) if res else None

    def create_key(self, expiry):
        k = "VIP-" + secrets.token_hex(4).upper()
        with self.get_conn() as conn:
            conn.cursor().execute("INSERT INTO vip_keys (key_code, expiry_date) VALUES (?, ?)", (k, expiry))
        return k

    def use_key(self, key, uid):
        with self.get_conn() as conn:
            k = conn.cursor().execute("SELECT * FROM vip_keys WHERE key_code = ? AND used_by IS NULL", (key,)).fetchone()
            if not k: return False
            conn.cursor().execute("UPDATE vip_keys SET used_by = ? WHERE key_code = ?", (uid, key))
            conn.cursor().execute("UPDATE users SET is_vip = 1, vip_expiry = ? WHERE user_id = ?", (k['expiry_date'], uid))
            return True

    def set_cache(self, key, data):
        exp = (datetime.now() + timedelta(minutes=15)).isoformat()
        with self.get_conn() as conn:
            conn.cursor().execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, json.dumps(data), exp))

    def get_cache(self, key):
        with self.get_conn() as conn:
            res = conn.cursor().execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > ?", (key, datetime.now().isoformat())).fetchone()
            return json.loads(res[0]) if res else None

# ================= API DE ESPORTES =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    async def get_matches(self):
        cached = self.db.get_cache("all_matches")
        if cached: return cached
        
        matches = []
        today = datetime.now().strftime("%Y-%m-%d")
        
        if API_FOOTBALL_KEY:
            try:
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=15) as client:
                    url = f"https://v3.football.api-sports.io/fixtures?date={today}"
                    r = await client.get(url, headers=headers)
                    if r.status_code == 200:
                        data = r.json().get("response", [])
                        for g in data:
                            if g["fixture"]["status"]["short"] in ["CANC", "ABD", "PST"]: continue
                            odd_val = round(random.uniform(1.3, 3.5), 2)
                            matches.append({
                                "sport": "⚽", 
                                "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                                "league": g["league"]["name"],
                                "time": (datetime.fromtimestamp(g["fixture"]["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                "odd": odd_val,
                                "tip": "Over 1.5" if odd_val < 1.8 else "Casa Vence",
                                "ts": g["fixture"]["timestamp"]
                            })
            except Exception as e:
                logger.error(f"Erro Conexão API: {e}")

        # BACKUP
        if not matches:
            base_ts = datetime.now().timestamp()
            matches = [
                {"sport": "⚽", "match": "Teste A x Teste B (Backup)", "league": "Liga Backup", "time": "20:00", "odd": 2.10, "tip": "Casa", "ts": base_ts},
                {"sport": "🏀", "match": "Lakers x Bulls (Backup)", "league": "NBA Teste", "time": "22:00", "odd": 1.90, "tip": "Over", "ts": base_ts+3600},
            ]

        matches.sort(key=lambda x: x["ts"])
        self.db.set_cache("all_matches", matches)
        return matches

# ================= HANDLERS =================
class Handlers:
    def __init__(self, db, api, ai): self.db, self.api, self.ai = db, api, ai
    
    def get_kb(self):
        return ReplyKeyboardMarkup([
            ["📋 Jogos de Hoje", "🚀 Múltipla 20x"],
            ["🦓 Zebra do Dia", "🛡️ Aposta Segura"],
            ["🏆 Ligas", "🤖 Guru IA"],
            ["📚 Glossário", "🎫 Meu Status"]
        ], resize_keyboard=True)

    async def start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        self.db.get_or_create_user(u.effective_user.id)
        # MOSTRA O ID DO USUÁRIO PARA CONFERÊNCIA
        await u.message.reply_text(
            f"👋 **DVD TIPS V40**\nSeu ID: `{u.effective_user.id}`\n(Copie este ID para colocar no ADMIN_ID do Render se precisar)", 
            reply_markup=self.get_kb(), 
            parse_mode=ParseMode.MARKDOWN
        )

    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        msg = await u.message.reply_text("🔄 Buscando...")
        m = await self.api.get_matches()
        txt = "*📋 JOGOS ENCONTRADOS:*\n\n"
        for g in m[:20]: 
            txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        if len(m)<4: return await u.message.reply_text("⚠️ Poucos jogos.")
        sel = random.sample(m, 4)
        total = 1.0
        txt = "*🚀 MÚLTIPLA SUGERIDA:*\n\n"
        for g in sel: 
            total *= g['odd']
            txt += f"• {g['match']} ({g['tip']})\n"
        txt += f"\n💰 *ODD TOTAL: {total:.2f}*"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def zebra(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        zebra = max(m, key=lambda x: x['odd'])
        txt = f"🦓 **ZEBRA:**\n🏆 {zebra['league']}\n⚔️ {zebra['match']}\n🔥 **{zebra['tip']}** (@{zebra['odd']})"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def safe(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        safe = min(m, key=lambda x: x['odd'])
        txt = f"🛡️ **SEGURA:**\n🏆 {safe['league']}\n⚔️ {safe['match']}\n✅ **{safe['tip']}** (@{safe['odd']})"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def leagues(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        ls = sorted(list(set([g['league'] for g in m])))
        txt = "*🏆 Ligas:*\n" + "\n".join([f"• {l}" for l in ls[:50]])
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def glossario(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("📚 *Glossário*\nOver=Mais\nUnder=Menos\nML=Vencedor", parse_mode=ParseMode.MARKDOWN)

    # === DIAGNÓSTICO DO ID ===
    async def debug(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        user_id = str(u.effective_user.id)
        admin_id_env = str(ADMIN_ID).strip()
        
        # Se o ID não bater, ele avisa (antes ele ficava mudo)
        if user_id != admin_id_env:
            msg = f"⛔ **ACESSO NEGADO**\n\nSeu ID: `{user_id}`\nAdmin ID no Render: `{admin_id_env}`\n\n⚠️ Copie o 'Seu ID' e coloque no Render!"
            return await u.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

        # Se bater, roda o teste
        await u.message.reply_text("🔎 ID Confirmado! Testando API...")
        self.db.get_conn().cursor().execute("DELETE FROM api_cache")
        m = await self.api.get_matches()
        status = "✅ API OK" if "Backup" not in m[0]['match'] else "⚠️ API Falhou (Usando Backup)"
        await u.message.reply_text(f"{status}\nJogos: {len(m)}")

    async def guru(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("🤖 Mande sua dúvida:")
        c.user_data["guru"] = True

    async def text(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if c.user_data.get("guru"):
            c.user_data["guru"] = False
            if not self.ai: return await u.message.reply_text("❌ IA Off.")
            msg = await u.message.reply_text("🤔 ...")
            try:
                res = await asyncio.to_thread(self.ai.generate_content, u.message.text)
                await msg.edit_text(f"🎓 *Guru:*\n{res.text}", parse_mode=ParseMode.MARKDOWN)
            except: await msg.edit_text("❌ Erro IA.")
        else: await u.message.reply_text("❓ Menu")

    async def status(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        usr = self.db.get_user(u.effective_user.id)
        st = f"✅ VIP até {usr['vip_expiry']}" if usr and usr['is_vip'] else "❌ Grátis"
        await u.message.reply_text(f"*🎫 STATUS:* {st}", parse_mode=ParseMode.MARKDOWN)

    async def admin(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if str(u.effective_user.id) != str(ADMIN_ID): 
            return await u.message.reply_text("⛔ Admin only")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Key", callback_data="gen")]])
        await u.message.reply_text("🔑 Admin", reply_markup=kb)

    async def cb(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if u.callback_query.data == "gen":
            k = self.db.create_key((datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
            await u.callback_query.message.edit_text(f"🔑 `{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            k = c.args[0]
            if self.db.use_key(k, u.effective_user.id): await u.message.reply_text("✅ OK!")
            else: await u.message.reply_text("❌ Inválido")
        except: await u.message.reply_text("Use: `/ativar CHAVE`")

# ================= MAIN =================
async def main():
    if not BOT_TOKEN: 
        print("❌ Faltam Variáveis!")
        return

    threading.Thread(target=start_fake_server, daemon=True).start()

    db = Database(DB_PATH)
    api = SportsAPI(db)
    ai = None
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai = genai.GenerativeModel('gemini-1.5-flash')
    
    h = Handlers(db, api, ai)

    while True:
        try:
            logger.info("🔥 Iniciando Bot V40...")
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            
            app.add_handler(CommandHandler("start", h.start))
            app.add_handler(CommandHandler("admin", h.admin))
            app.add_handler(CommandHandler("ativar", h.active))
            app.add_handler(CommandHandler("debug", h.debug))
            
            app.add_handler(MessageHandler(filters.Regex("^📋"), h.games))
            app.add_handler(MessageHandler(filters.Regex("^🚀"), h.multi))
            app.add_handler(MessageHandler(filters.Regex("^🦓"), h.zebra))
            app.add_handler(MessageHandler(filters.Regex("^🛡️"), h.safe))
            app.add_handler(MessageHandler(filters.Regex("^🏆"), h.leagues))
            app.add_handler(MessageHandler(filters.Regex("^📚"), h.glossario))
            app.add_handler(MessageHandler(filters.Regex("^🤖"), h.guru))
            app.add_handler(MessageHandler(filters.Regex("^🎫"), h.status))
            
            app.add_handler(CallbackQueryHandler(h.cb))
            app.add_handler(MessageHandler(filters.TEXT, h.text))

            await app.initialize()
            await app.start()
            await app.bot.delete_webhook(drop_pending_updates=True)
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            
            while True: 
                await asyncio.sleep(60)
                if not app.updater.running: raise RuntimeError("Bot parou!")

        except Conflict:
            logger.error("🚨 CONFLITO! 30s...")
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