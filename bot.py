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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from http.server import HTTPServer, BaseHTTPRequestHandler
import unicodedata

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import Conflict, BadRequest

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"
LOG_LEVEL = "INFO"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# ================= FILTROS DE ELITE =================
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 848, 143, 45, 48, 528] 
BLOCKLIST_TERMS = ["U19", "U20", "U21", "U23", "WOMEN", "FEMININO", "YOUTH", "RESERVES", "LADIES", "JUNIOR", "GIRLS"]
VIP_TEAMS_NAMES = ["FLAMENGO", "PALMEIRAS", "SAO PAULO", "CORINTHIANS", "SANTOS", "GREMIO", "INTERNACIONAL", "ATLETICO MINEIRO", "BOTAFOGO", "FLUMINENSE", "VASCO", "CRUZEIRO", "BAHIA", "FORTALEZA", "MANCHESTER CITY", "REAL MADRID", "BARCELONA", "LIVERPOOL", "ARSENAL", "PSG", "INTER", "MILAN", "JUVENTUS", "BAYERN", "BOCA JUNIORS", "RIVER PLATE"]

def normalize_str(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVIDOR WEB FAKE =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V52.1 ONLINE - FIXED")

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
        try: 
            yield conn
            conn.commit()
        except: 
            conn.rollback()
            raise
        finally: 
            conn.close()
    
    def init_db(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_vip BOOLEAN DEFAULT 0)")
            c.execute("CREATE TABLE IF NOT EXISTS vip_keys (key_code TEXT UNIQUE, expiry_date TEXT, used_by INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT UNIQUE, cache_data TEXT, expires_at TIMESTAMP)")

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
            conn.cursor().execute("INSERT OR IGNORE INTO users (user_id, is_vip) VALUES (?, 1)", (uid,))
            return True

    def set_cache(self, key, data):
        exp = (datetime.now() + timedelta(minutes=30)).isoformat()
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
            with self.get_conn() as conn: 
                conn.cursor().execute("DELETE FROM api_cache")
        except: pass

# ================= API DE ESPORTES =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    async def get_matches(self, force_debug=False):
        if not force_debug:
            cached = await asyncio.to_thread(self.db.get_cache, "top10_matches")
            if cached: return cached, "Cache"
        
        matches = []
        status_msg = "API Oficial"
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        
        if API_FOOTBALL_KEY:
            try:
                # 1. FUTEBOL
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=25) as client:
                    url = f"https://v3.football.api-sports.io/fixtures?date={today}"
                    r = await client.get(url, headers=headers)
                    if r.status_code == 200:
                        data = r.json().get("response", [])
                        for g in data:
                            if g["fixture"]["status"]["short"] in ["CANC", "ABD", "PST", "FT"]: continue
                            
                            league_id = g["league"]["id"]
                            h_team = normalize_str(g["teams"]["home"]["name"])
                            a_team = normalize_str(g["teams"]["away"]["name"])
                            
                            # FILTRO ANTI-JUNK
                            if any(bad in h_team for bad in BLOCKLIST_TERMS) or any(bad in a_team for bad in BLOCKLIST_TERMS):
                                continue

                            priority_score = 0
                            if league_id in VIP_LEAGUES_IDS: 
                                priority_score += 1000
                            elif any(vip in h_team for vip in VIP_TEAMS_NAMES) or any(vip in a_team for vip in VIP_TEAMS_NAMES): 
                                priority_score += 500
                            
                            if priority_score == 0: continue

                            odd_h = round(random.uniform(1.3, 2.9), 2)
                            tip_text = "Casa Vence"
                            if odd_h > 2.4: tip_text = "Empate ou Visitante"
                            elif odd_h < 1.6: tip_text = "Casa Vence"
                            else: tip_text = "Over 1.5 Gols"

                            matches.append({
                                "sport": "⚽", 
                                "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                                "league": g["league"]["name"],
                                "time": (datetime.fromtimestamp(g["fixture"]["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                "odd": odd_h,
                                "tip": tip_text,
                                "ts": g["fixture"]["timestamp"],
                                "score": priority_score
                            })

                # 2. NBA
                headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=15) as client:
                    r_nba = await client.get(f"https://v1.basketball.api-sports.io/games?date={today}&league=12", headers=headers_nba)
                    if r_nba.status_code == 200:
                        nba_data = r_nba.json().get("response", [])
                        for g in nba_data:
                            if g["status"]["short"] == "FT": continue
                            matches.append({
                                "sport": "🏀",
                                "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                                "league": "NBA",
                                "time": (datetime.fromtimestamp(g["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                "odd": 1.90,
                                "tip": "Over Pontos",
                                "ts": g["timestamp"],
                                "score": 2000
                            })
            except Exception as e: logger.error(f"Erro API Geral: {e}")

        # BACKUP
        if not matches:
            status_msg = "Backup"
            base_ts = datetime.now().timestamp()
            matches = [
                {"sport": "⚽", "match": "Flamengo x Palmeiras [Sim]", "league": "Brasileirão", "time": "21:30", "odd": 2.10, "tip": "Casa Vence", "ts": base_ts, "score": 200},
                {"sport": "🏀", "match": "Lakers x Celtics [Sim]", "league": "NBA", "time": "22:00", "odd": 1.90, "tip": "Over 220", "ts": base_ts, "score": 300}
            ]

        matches.sort(key=lambda x: (-x["score"], x["ts"]))
        top_matches = matches[:20] 
        await asyncio.to_thread(self.db.set_cache, "top10_matches", top_matches)
        return top_matches, status_msg

# ================= SISTEMA DE ENVIO P/ CANAL =================
async def send_channel_report(app, db, api):
    if not CHANNEL_ID: return False, "Sem Channel ID"
    await asyncio.to_thread(db.clear_cache)
    m, source = await api.get_matches(force_debug=True)
    if not m: return False, "Sem jogos"

    today_str = datetime.now().strftime("%d/%m")
    
    nba_games = [g for g in m if g['sport'] == '🏀']
    foot_games = [g for g in m if g['sport'] == '⚽']
    
    post = f"🦁 **BOLETIM VIP - {today_str}**\n"
    post += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # 1. JOGO DO DIA
    if foot_games:
        best = foot_games[0]
        post += f"💎 **JOGO DE OURO (MAX)**\n"
        post += f"⚽ {best['match']}\n"
        post += f"🏆 {best['league']} | 🕒 {best['time']}\n"
        post += f"🔥 **Entrada:** {best['tip']}\n"
        post += f"📈 **Odd:** @{best['odd']}\n\n"
    
    # 2. NBA
    if nba_games:
        post += f"🏀 **SESSÃO NBA**\n"
        for g in nba_games[:2]:
            post += f"🇺🇸 {g['match']}\n"
            post += f"🎯 {g['tip']} (@{g['odd']})\n\n"

    # 3. LISTA FUTEBOL
    post += "📋 **GRADE DE ELITE**\n"
    count = 0
    for g in foot_games[1:]:
        if count >= 5: break
        post += f"⚔️ {g['match']}\n"
        post += f"   ↳ {g['tip']} (@{g['odd']})\n"
        count += 1
        
    post += "\n━━━━━━━━━━━━━━━━━━━━\n"
    
    # 4. TROCO DO PÃO (RISCO) - AGORA MOSTRANDO OS JOGOS!
    post += f"💣 **TROCO DO PÃO (RISCO)**\n"
    
    # Pega jogos com odd >= 1.90
    risk_candidates = [g for g in m if g['odd'] >= 1.90]
    # Se tiver poucos, pega da lista geral mesmo
    if len(risk_candidates) < 4: risk_candidates = m
    
    sel_risk = random.sample(risk_candidates, min(4, len(risk_candidates)))
    total_risk = 1.0
    
    for g in sel_risk:
        total_risk *= g['odd']
        post += f"🔥 {g['match']}\n   👉 {g['tip']} (@{g['odd']})\n"
        
    # Boost na odd simulada se necessário
    if total_risk < 15: total_risk = random.uniform(15.5, 25.0)
    
    post += f"\n💰 **ODD FINAL: @{total_risk:.2f}**\n"
    post += f"⚠️ _Gestão de banca sempre!_ 🦁"

    try:
        await app.bot.send_message(chat_id=CHANNEL_ID, text=post, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return True, "Sucesso"
    except Exception as e: return False, str(e)

# ================= AGENDADOR AUTOMÁTICO (RECOLOCADO!) =================
async def daily_scheduler(app, db, api):
    logger.info("⏰ Agendador iniciado...")
    while True:
        try:
            # Fuso Horário de Brasília (UTC-3)
            now_br = datetime.now(timezone.utc) - timedelta(hours=3)

            # Se for 08:00 da manhã
            if now_br.hour == 8 and now_br.minute == 0:
                logger.info("⏰ Hora do envio automático!")
                await send_channel_report(app, db, api)
                await asyncio.sleep(61) # Espera 1 minuto para não repetir
            
            await asyncio.sleep(30) # Verifica a cada 30 segundos
        except Exception as e:
            logger.error(f"Erro no Scheduler: {e}")
            await asyncio.sleep(60)

# ================= HANDLERS =================
class Handlers:
    def __init__(self, db, api): self.db, self.api = db, api
    
    def is_admin(self, uid): return str(uid) == str(ADMIN_ID)

    async def start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        uid = u.effective_user.id
        # CLIENTE
        if not self.is_admin(uid):
            msg = (f"👋 **Bem-vindo ao DVD TIPS**\n\n"
                   f"⛔ Acesso restrito.\n"
                   f"O conteúdo VIP é enviado no Canal Oficial.\n\n"
                   f"Para entrar, digite:\n"
                   f"`/ativar SUA-CHAVE`")
            return await u.message.reply_text(msg, reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.MARKDOWN)

        # ADMIN (AGORA COM BOTÕES!)
        msg = f"🦁 **PAINEL DO DONO**\nCanal: `{CHANNEL_ID}`"
        kb = ReplyKeyboardMarkup([
            ["🔥 Top Jogos", "🚀 Múltipla Segura"],
            ["💣 Troco do Pão", "🏀 NBA"],
            ["📢 Publicar no Canal", "🎫 Gerar Key"]
        ], resize_keyboard=True)
        await u.message.reply_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    # --- FUNÇÕES ADMIN (VISUALIZAÇÃO NO BOT) ---
    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        msg = await u.message.reply_text("🔎 Buscando...")
        m, _ = await self.api.get_matches()
        if not m: return await msg.edit_text("📭 Sem jogos.")
        txt = "*🔥 TOP JOGOS (PREVIEW ADMIN):*\n\n"
        for g in m[:10]:
            txt += f"{g['sport']} {g['match']} | {g['league']}\n💡 {g['tip']} (@{g['odd']})\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_risk_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        risk = [g for g in m if g['odd'] >= 1.9]
        if len(risk)<4: risk = m
        sel = random.sample(risk, min(4, len(risk)))
        total = 1.0
        txt = "*💣 PREVIEW TROCO DO PÃO:*\n\n"
        for g in sel:
            total *= g['odd']
            txt += f"🔥 {g['match']} ({g['tip']})\n"
        txt += f"\n💰 ODD: {total:.2f}"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def nba_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        nba = [g for g in m if g['sport'] == '🏀']
        if not nba: return await u.message.reply_text("Sem NBA.")
        txt = "*🏀 PREVIEW NBA:*\n\n"
        for g in nba: txt += f"{g['match']} ({g['tip']})\n"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_safe_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        safe = [g for g in m if g['odd'] < 1.7]
        if len(safe)<3: safe = m[:3]
        sel = random.sample(safe, min(3, len(safe)))
        total = 1.0
        txt = "*🚀 PREVIEW SEGURA:*\n\n"
        for g in sel:
            total *= g['odd']
            txt += f"✅ {g['match']} ({g['tip']})\n"
        txt += f"\n💰 ODD: {total:.2f}"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def publish(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        msg = await u.message.reply_text("⏳ Postando...")
        ok, info = await send_channel_report(c.application, self.db, self.api)
        await msg.edit_text("✅ Postado!" if ok else f"❌ Erro: {info}")

    async def gen_key_btn(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        k = await asyncio.to_thread(self.db.create_key, (datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
        await u.message.reply_text(f"🔑 **NOVA CHAVE:**\n`{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            key_input = c.args[0]
            success = await asyncio.to_thread(self.db.use_key, key_input, u.effective_user.id)
            if success:
                try:
                    if CHANNEL_ID:
                        link = await c.bot.create_chat_invite_link(chat_id=CHANNEL_ID, member_limit=1, expire_date=datetime.now() + timedelta(hours=24))
                        invite_link = link.invite_link
                    else: invite_link = "(Sem ID Canal)"
                except: invite_link = "(Erro Link)"
                await u.message.reply_text(f"✅ **VIP ATIVADO!**\n\nEntre no canal:\n👉 {invite_link}")
            else:
                await u.message.reply_text("❌ Chave inválida.")
        except: await u.message.reply_text("❌ Use: `/ativar CHAVE`")

# ================= MAIN =================
async def main():
    if not BOT_TOKEN: 
        print("❌ Faltam Variáveis!")
        return

    threading.Thread(target=start_fake_server, daemon=True).start()

    db = Database(DB_PATH)
    api = SportsAPI(db)
    h = Handlers(db, api)

    while True:
        try:
            logger.info("🔥 Iniciando Bot V52.1...")
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            
            app.add_handler(CommandHandler("start", h.start))
            app.add_handler(CommandHandler("publicar", h.publish))
            app.add_handler(CommandHandler("ativar", h.active))
            
            # Botões ADMIN
            app.add_handler(MessageHandler(filters.Regex("^🔥"), h.games))
            app.add_handler(MessageHandler(filters.Regex("^💣"), h.multi_risk_preview)) # Troco do Pão
            app.add_handler(MessageHandler(filters.Regex("^🚀"), h.multi_safe_preview))
            app.add_handler(MessageHandler(filters.Regex("^🏀"), h.nba_preview))
            app.add_handler(MessageHandler(filters.Regex("^📢"), h.publish))
            app.add_handler(MessageHandler(filters.Regex("^🎫"), h.gen_key_btn))
            
            await app.initialize()
            await app.start()
            asyncio.create_task(daily_scheduler(app, db, api))
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
