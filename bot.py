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
from telegram.error import Conflict # <--- ESSA LINHA FALTAVA NA V37

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"
LOG_LEVEL = "INFO"

# Configuração de Log
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO, 
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= SERVIDOR WEB FAKE (MANTÉM O BOT VIVO) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V38 ONLINE - FIX IMPORT")

def start_fake_server():
    try:
        server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
        logger.info(f"🌍 WEB SERVER RODANDO NA PORTA {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Erro no Web Server: {e}")

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
        # Cache de 30 minutos
        exp = (datetime.now() + timedelta(minutes=30)).isoformat()
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
        
        if not API_FOOTBALL_KEY: return []
        
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
        headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
        
        matches = []
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r_ft, r_bk = await asyncio.gather(
                    client.get(f"https://v3.football.api-sports.io/fixtures?date={today}&status=NS-LIVE-FT", headers=headers),
                    client.get(f"https://v1.basketball.api-sports.io/games?date={today}", headers=headers_nba),
                    return_exceptions=True
                )
                
                # Futebol
                if not isinstance(r_ft, Exception) and r_ft.status_code == 200:
                    data = r_ft.json().get("response", [])
                    # IDs principais + Lógica de Serie A
                    VIP_IDS = [39, 40, 140, 141, 78, 79, 135, 136, 61, 71, 72, 2, 3, 13, 11, 4, 9, 10, 203, 88, 94, 128, 144, 253, 307]
                    
                    for g in data:
                        lid = g["league"]["id"]
                        if lid not in VIP_IDS and "Serie A" not in g["league"]["name"]: continue
                            
                        ts = g["fixture"]["timestamp"]
                        if datetime.fromtimestamp(ts) < datetime.now() - timedelta(hours=6): continue
                        
                        odd_val = round(random.uniform(1.45, 2.65), 2)
                        
                        matches.append({
                            "sport": "⚽", 
                            "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                            "league": g["league"]["name"],
                            "time": (datetime.fromtimestamp(ts)-timedelta(hours=3)).strftime("%H:%M"),
                            "odd": odd_val,
                            "tip": "Over 2.5 Gols" if random.random() > 0.5 else f"Vence {g['teams']['home']['name']}",
                            "ts": ts
                        })

                # NBA
                if not isinstance(r_bk, Exception) and r_bk.status_code == 200:
                    for g in r_bk.json().get("response", []):
                        if g["league"]["id"] != 12: continue
                        ts = g["timestamp"]
                        matches.append({
                            "sport": "🏀",
                            "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                            "league": "NBA",
                            "time": (datetime.fromtimestamp(ts)-timedelta(hours=3)).strftime("%H:%M"),
                            "odd": round(random.uniform(1.4, 2.3), 2),
                            "tip": f"Vence {g['teams']['home']['name']}",
                            "ts": ts
                        })
        except Exception as e:
            logger.error(f"Erro API: {e}")

        if matches:
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
        await u.message.reply_text("👋 **DVD TIPS V38 - FIX**\nBot Corrigido e Operante!", reply_markup=self.get_kb(), parse_mode=ParseMode.MARKDOWN)

    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        msg = await u.message.reply_text("🔄 Buscando grade...")
        m = await self.api.get_matches()
        if not m: return await msg.edit_text("📭 Nenhum jogo encontrado. Verifique com /debug")
        
        txt = "*📋 JOGOS DE HOJE:*\n\n"
        for g in m[:20]: 
            txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        if not m or len(m)<4: return await u.message.reply_text("⚠️ Poucos jogos.")
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
        if not m: return await u.message.reply_text("📭 Sem dados.")
        zebra = max(m, key=lambda x: x['odd'])
        txt = f"🦓 **ZEBRA DO DIA:**\n\n🏆 {zebra['league']}\n⚔️ {zebra['match']}\n🔥 **{zebra['tip']}**\n📈 Odd: **@{zebra['odd']}**"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def safe(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        if not m: return await u.message.reply_text("📭 Sem dados.")
        safe = min(m, key=lambda x: x['odd'])
        txt = f"🛡️ **APOSTA SEGURA:**\n\n🏆 {safe['league']}\n⚔️ {safe['match']}\n✅ **{safe['tip']}**\n📉 Odd: **@{safe['odd']}**"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def leagues(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m = await self.api.get_matches()
        if not m: return await u.message.reply_text("📭 Sem dados.")
        ls = sorted(list(set([g['league'] for g in m])))
        txt = "*🏆 Ligas na Grade:*\n\n" + "\n".join([f"• {l}" for l in ls])
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def glossario(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        txt = "📚 **GLOSSÁRIO:**\n\n• **Over:** Mais de\n• **Under:** Menos de\n• **ML:** Vencedor\n• **BTTS:** Ambas Marcam"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def debug(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if str(u.effective_user.id) != str(ADMIN_ID): return
        self.db.get_conn().cursor().execute("DELETE FROM api_cache")
        m = await self.api.get_matches()
        await u.message.reply_text(f"🔍 Debug API: {len(m)} jogos encontrados.")

    async def guru(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("🤖 **Guru:** Mande sua dúvida:", parse_mode=ParseMode.MARKDOWN)
        c.user_data["guru"] = True

    async def text(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if c.user_data.get("guru"):
            c.user_data["guru"] = False
            if not self.ai: return await u.message.reply_text("❌ IA Off.")
            msg = await u.message.reply_text("🤔 Analisando...")
            try:
                res = await asyncio.to_thread(self.ai.generate_content, u.message.text)
                await msg.edit_text(f"🎓 *Guru Responde:*\n\n{res.text}", parse_mode=ParseMode.MARKDOWN)
            except: await msg.edit_text("❌ Erro na IA.")
        else: await u.message.reply_text("❓ Use o menu.")

    async def status(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        usr = self.db.get_user(u.effective_user.id)
        st = f"✅ VIP até {usr['vip_expiry']}" if usr and usr['is_vip'] else "❌ Grátis"
        await u.message.reply_text(f"*🎫 SEU PERFIL*\nStatus: {st}", parse_mode=ParseMode.MARKDOWN)

    async def admin(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if str(u.effective_user.id) != str(ADMIN_ID): return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Gerar Key", callback_data="gen")]])
        await u.message.reply_text("🔑 Admin Panel", reply_markup=kb)

    async def cb(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if u.callback_query.data == "gen":
            k = self.db.create_key((datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
            await u.callback_query.message.edit_text(f"🔑 Chave: `{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            k = c.args[0]
            if self.db.use_key(k, u.effective_user.id): await u.message.reply_text("✅ VIP Ativado!")
            else: await u.message.reply_text("❌ Chave inválida.")
        except: await u.message.reply_text("Use: `/ativar CHAVE`")

# ================= MAIN =================
async def main():
    if not BOT_TOKEN: 
        print("❌ ERRO: BOT_TOKEN faltando!")
        return

    # 1. Inicia Web Server Fake
    threading.Thread(target=start_fake_server, daemon=True).start()

    # 2. Inicializa
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
            logger.info("🔥 Iniciando Bot V38...")
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
            
            # Limpa webhook velho para evitar conflito de update
            await app.bot.delete_webhook(drop_pending_updates=True)
            
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