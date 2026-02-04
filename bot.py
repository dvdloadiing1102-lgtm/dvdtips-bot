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
import unicodedata

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import Conflict

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"
LOG_LEVEL = "INFO"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# ================= LISTAS VIP (O SEU PEDIDO) =================
# IDs das Ligas API-Football
VIP_LEAGUES_IDS = [
    39,   # Premier League (England)
    140,  # La Liga (Spain)
    135,  # Serie A (Italy)
    71,   # Brasileirão Série A (Brazil)
    78,   # Bundesliga (Germany)
    128,  # Liga Profesional (Argentina)
    94,   # Liga Portugal
    61,   # Ligue 1 (France)
    144,  # Jupiler Pro League (Belgium)
    88,   # Eredivisie (Netherlands)
    203,  # Süper Lig (Turkey)
    239,  # Categoría Primera A (Colombia)
    197,  # Super League (Greece)
    345,  # Fortuna Liga (Czech Republic)
    268,  # Primera División (Uruguay)
    233,  # Premier League (Egypt)
    252,  # Primera División (Paraguay)
    262,  # Liga MX (Mexico)
    179,  # Premiership (Scotland)
    98    # J1 League (Japan)
]

# Times que SEMPRE devem aparecer (Normalizados sem acento)
VIP_TEAMS_NAMES = [
    "MANCHESTER CITY", "REAL MADRID", "INTER", "PORTO", "AL AHLY", "FLAMENGO", 
    "MANCHESTER UNITED", "PALMEIRAS", "FIORENTINA", "NAPOLI", "RB LEIPZIG", "PSV", 
    "FORTALEZA", "BAYERN MUNICH", "SAO PAULO", "BENFICA", "FENERBAHCE", "JUVENTUS", 
    "ROMA", "ARSENAL", "FLUMINENSE", "INTERNACIONAL", "BARCELONA", "UNION ST GILLOISE", 
    "FEYENOORD", "MILAN", "WEST HAM", "SEVILLA", "SPORTING CP", "BOTAFOGO"
]

def normalize_str(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVIDOR WEB FAKE =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V43 ONLINE - ELITE FILTER")

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
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
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
        try:
            with self.get_conn() as conn:
                conn.cursor().execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
        except: pass

    def get_user(self, uid):
        try:
            with self.get_conn() as conn:
                res = conn.cursor().execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
                return dict(res) if res else None
        except: return None

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
        exp = (datetime.now() + timedelta(minutes=20)).isoformat()
        try:
            with self.get_conn() as conn:
                conn.cursor().execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, json.dumps(data), exp))
        except: pass

    def get_cache(self, key):
        try:
            with self.get_conn() as conn:
                res = conn.cursor().execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > ?", (key, datetime.now().isoformat())).fetchone()
                return json.loads(res[0]) if res else None
        except: return None
    
    def clear_cache(self):
        try:
            with self.get_conn() as conn: conn.cursor().execute("DELETE FROM api_cache")
        except: pass

# ================= API DE ESPORTES (FILTRADA) =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    async def get_matches(self, force_debug=False):
        if not force_debug:
            cached = await asyncio.to_thread(self.db.get_cache, "all_matches")
            if cached: return cached, "Cache"
        
        matches = []
        status_msg = "API Oficial"
        today = datetime.now().strftime("%Y-%m-%d")
        
        if API_FOOTBALL_KEY:
            try:
                # REQUISIÇÃO FUTEBOL
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=15) as client:
                    # Busca jogos não iniciados ou ao vivo
                    url = f"https://v3.football.api-sports.io/fixtures?date={today}"
                    r = await client.get(url, headers=headers)
                    
                    if r.status_code == 200:
                        data = r.json().get("response", [])
                        for g in data:
                            # Ignora cancelados
                            if g["fixture"]["status"]["short"] in ["CANC", "ABD", "PST"]: continue
                            
                            # LOGICA DO FILTRO DE ELITE
                            league_id = g["league"]["id"]
                            home_team = normalize_str(g["teams"]["home"]["name"])
                            away_team = normalize_str(g["teams"]["away"]["name"])
                            
                            is_vip_league = league_id in VIP_LEAGUES_IDS
                            is_vip_team = any(vip in home_team for vip in VIP_TEAMS_NAMES) or any(vip in away_team for vip in VIP_TEAMS_NAMES)
                            
                            # Só passa se for Liga VIP ou Time VIP
                            if not (is_vip_league or is_vip_team):
                                continue

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
                    else:
                        logger.error(f"Erro Foot API: {r.status_code}")

                # REQUISIÇÃO BASQUETE (NBA)
                # Nota: As vezes a chave de Futebol não funciona no Basquete, depende do plano.
                headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=15) as client:
                    url_nba = f"https://v1.basketball.api-sports.io/games?date={today}&league=12" # ID 12 = NBA
                    r_nba = await client.get(url_nba, headers=headers_nba)
                    
                    if r_nba.status_code == 200:
                        data_nba = r_nba.json().get("response", [])
                        for g in data_nba:
                            # NBA não precisa de filtro extra, ID 12 já filtra
                            matches.append({
                                "sport": "🏀",
                                "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                                "league": "NBA",
                                "time": (datetime.fromtimestamp(g["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                "odd": round(random.uniform(1.8, 2.2), 2),
                                "tip": "Over Points",
                                "ts": g["timestamp"]
                            })
                    else:
                         logger.error(f"Erro NBA API: {r_nba.status_code} (Pode ser falta de assinatura na API de Basquete)")

            except Exception as e:
                logger.error(f"Exceção API: {e}")

        # BACKUP (SÓ SE NÃO ACHAR NADA DO QUE VOCÊ PEDIU)
        if not matches:
            status_msg = "Modo Backup (Filtro muito restrito ou API Off)"
            base_ts = datetime.now().timestamp()
            matches = [
                {"sport": "⚽", "match": "Flamengo x Palmeiras [Simulado]", "league": "Brasileirão", "time": "16:00", "odd": 2.10, "tip": "Casa", "ts": base_ts},
                {"sport": "🏀", "match": "Lakers x Warriors [Simulado]", "league": "NBA", "time": "22:00", "odd": 1.90, "tip": "Over", "ts": base_ts+3600},
            ]

        matches.sort(key=lambda x: x["ts"])
        await asyncio.to_thread(self.db.set_cache, "all_matches", matches)
        return matches, status_msg

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
        await asyncio.to_thread(self.db.get_or_create_user, u.effective_user.id)
        msg = f"👋 **DVD TIPS V43 - ELITE**\nFiltro de Ligas: ATIVO\nFiltro de Times: ATIVO\nNBA: ATIVO"
        await u.message.reply_text(msg, reply_markup=self.get_kb(), parse_mode=ParseMode.MARKDOWN)

    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        msg = await u.message.reply_text("🔄 Buscando na grade VIP...")
        try:
            m, source = await asyncio.wait_for(self.api.get_matches(), timeout=20)
        except asyncio.TimeoutError:
            return await msg.edit_text("⚠️ Timeout API.")
            
        if not m: return await msg.edit_text("📭 Nenhum jogo VIP hoje.")
            
        txt = f"*📋 JOGOS VIP ({len(m)}):*\n\n"
        for g in m[:25]: # Mostra até 25 jogos
            txt += f"{g['sport']} {g['time']} | {g['league']}\n⚔️ {g['match']}\n👉 *{g['tip']}* (@{g['odd']})\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m, _ = await self.api.get_matches()
        if len(m)<4: return await u.message.reply_text("⚠️ Poucos jogos VIP para múltipla.")
        sel = random.sample(m, 4)
        total = 1.0
        txt = "*🚀 MÚLTIPLA VIP:*\n\n"
        for g in sel: 
            total *= g['odd']
            txt += f"• {g['match']} ({g['tip']})\n"
        txt += f"\n💰 *ODD TOTAL: {total:.2f}*"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def zebra(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m, _ = await self.api.get_matches()
        if not m: return await u.message.reply_text("📭 Sem dados.")
        zebra = max(m, key=lambda x: x['odd'])
        txt = f"🦓 **ZEBRA:**\n🏆 {zebra['league']}\n⚔️ {zebra['match']}\n🔥 **{zebra['tip']}** (@{zebra['odd']})"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def safe(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m, _ = await self.api.get_matches()
        if not m: return await u.message.reply_text("📭 Sem dados.")
        safe = min(m, key=lambda x: x['odd'])
        txt = f"🛡️ **SEGURA:**\n🏆 {safe['league']}\n⚔️ {safe['match']}\n✅ **{safe['tip']}** (@{safe['odd']})"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def leagues(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        m, _ = await self.api.get_matches()
        if not m: return await u.message.reply_text("📭 Sem dados.")
        ls = sorted(list(set([g['league'] for g in m])))
        txt = "*🏆 Ligas Encontradas:*\n" + "\n".join([f"• {l}" for l in ls])
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def glossario(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("📚 *Glossário*\nOver=Mais\nUnder=Menos\nML=Vencedor", parse_mode=ParseMode.MARKDOWN)

    async def debug(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        user_id = str(u.effective_user.id)
        admin_id_env = str(ADMIN_ID).strip()
        if user_id != admin_id_env:
            return await u.message.reply_text(f"⛔ ACESSO NEGADO.", parse_mode=ParseMode.MARKDOWN)

        msg = await u.message.reply_text("🔍 Limpando Cache e Testando...")
        await asyncio.to_thread(self.db.clear_cache)
        m, status = await self.api.get_matches(force_debug=True)
        
        nba_count = len([g for g in m if g['sport'] == '🏀'])
        foot_count = len([g for g in m if g['sport'] == '⚽'])
        
        await msg.edit_text(f"✅ STATUS: {status}\n⚽ Futebol: {foot_count}\n🏀 NBA: {nba_count}\nTotal: {len(m)}")

    async def guru(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        await u.message.reply_text("🤖 Mande sua dúvida:")
        c.user_data["guru"] = True

    async def text(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if c.user_data.get("guru"):
            c.user_data["guru"] = False
            if not self.ai: return await u.message.reply_text("❌ IA Off.")
            msg = await u.message.reply_text("🤔 Analisando...")
            try:
                res = await asyncio.to_thread(self.ai.generate_content, u.message.text)
                await msg.edit_text(f"🎓 *Guru Responde:*\n\n{res.text}", parse_mode=ParseMode.MARKDOWN)
            except: await msg.edit_text("❌ Erro IA.")
        else: await u.message.reply_text("❓ Use o menu.")

    async def status(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        usr = await asyncio.to_thread(self.db.get_user, u.effective_user.id)
        st = f"✅ VIP até {usr['vip_expiry']}" if usr and usr['is_vip'] else "❌ Grátis"
        await u.message.reply_text(f"*🎫 STATUS:* {st}", parse_mode=ParseMode.MARKDOWN)

    async def admin(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if str(u.effective_user.id) != str(ADMIN_ID): 
            return await u.message.reply_text("⛔ Admin only")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Key", callback_data="gen")]])
        await u.message.reply_text("🔑 Admin", reply_markup=kb)

    async def cb(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if u.callback_query.data == "gen":
            k = await asyncio.to_thread(self.db.create_key, (datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
            await u.callback_query.message.edit_text(f"🔑 `{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            k = c.args[0]
            success = await asyncio.to_thread(self.db.use_key, k, u.effective_user.id)
            if success: await u.message.reply_text("✅ OK!")
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
            logger.info("🔥 Iniciando Bot V43...")
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
