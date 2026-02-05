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
import re
import xml.etree.ElementTree as ET

# Telegram & Gemini
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.error import Conflict, BadRequest
import google.generativeai as genai

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"

# Inicializa Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    ai_model = None

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= FILTROS E LISTAS =================
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 848, 143, 45, 48, 528] 
BLOCKLIST_TERMS = ["U19", "U20", "U21", "U23", "WOMEN", "FEMININO", "YOUTH", "RESERVES", "LADIES"]
VIP_TEAMS_NAMES = ["FLAMENGO", "PALMEIRAS", "SAO PAULO", "CORINTHIANS", "SANTOS", "GREMIO", "INTER", "MANCHESTER CITY", "REAL MADRID", "BARCELONA"]

BETTING_KEYWORDS = ["lesão", "desfalque", "fora", "dúvida", "suspenso", "titular", "reforço", "injury", "out", "bench"]

def normalize_str(s):
    if not s: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').upper()

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
            c.execute("CREATE TABLE IF NOT EXISTS sent_news (news_url TEXT PRIMARY KEY, sent_at TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS zebra_alerts (match_id TEXT PRIMARY KEY, alert_time TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS tips_history (id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, match_name TEXT, league TEXT, tip_type TEXT, odd REAL, date_sent DATE, status TEXT DEFAULT 'PENDING')")

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
        exp = (datetime.now() + timedelta(minutes=20)).isoformat()
        with self.get_conn() as conn:
            conn.cursor().execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, json.dumps(data), exp))

    def get_cache(self, key):
        with self.get_conn() as conn:
            res = conn.cursor().execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > ?", (key, datetime.now().isoformat())).fetchone()
            return json.loads(res[0]) if res else None

    def is_news_sent(self, url):
        with self.get_conn() as conn:
            return conn.cursor().execute("SELECT 1 FROM sent_news WHERE news_url = ?", (url,)).fetchone() is not None

    def mark_news_sent(self, url):
        with self.get_conn() as conn:
            conn.cursor().execute("INSERT OR IGNORE INTO sent_news (news_url, sent_at) VALUES (?, ?)", (url, datetime.now()))

# ================= SPORTS API (COM ODDS REAIS) =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    async def analyze_with_gemini(self, text):
        if not ai_model: return text[:200]
        try:
            prompt = f"Como analista de apostas, resuma em 1 frase curta: {text}. Se for irrelevante, diga 'PULAR'."
            response = await asyncio.to_thread(ai_model.generate_content, prompt)
            return response.text.strip()
        except: return text[:200]

    async def get_matches(self, force_debug=False):
        if not force_debug:
            cached = self.db.get_cache("top10_matches")
            if cached: return cached, "Cache"
        
        matches = []
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        
        # 1. FUTEBOL COM ODDS REAIS DA BET365
        if API_FOOTBALL_KEY:
            try:
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=25) as client:
                    # Endpoint de Odds do dia
                    url = f"https://v3.football.api-sports.io/odds?date={today}&bookmaker=6"
                    r = await client.get(url, headers=headers)
                    if r.status_code == 200:
                        data = r.json().get("response", [])
                        for item in data:
                            h_team = normalize_str(item["teams"]["home"]["name"])
                            a_team = normalize_str(item["teams"]["away"]["name"])
                            
                            if any(bad in h_team for bad in BLOCKLIST_TERMS): continue

                            p_score = 0
                            if "FLAMENGO" in h_team or "FLAMENGO" in a_team: p_score += 5000
                            elif item["league"]["id"] in VIP_LEAGUES_IDS: p_score += 1000
                            
                            if p_score > 0:
                                try:
                                    # Pega odds de 'Match Winner'
                                    odds_list = item['bookmakers'][0]['bets'][0]['values']
                                    # Ordena para pegar o favorito (menor odd)
                                    fav = sorted(odds_list, key=lambda x: float(x['odd']))[0]
                                    
                                    matches.append({
                                        "id": item["fixture"]["id"], "sport": "⚽", 
                                        "match": f"{item['teams']['home']['name']} x {item['teams']['away']['name']}",
                                        "league": item["league"]["name"], 
                                        "time": (datetime.fromtimestamp(item["fixture"]["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                        "odd": float(fav['odd']), "tip": fav['value'], "score": p_score, "ts": item["fixture"]["timestamp"]
                                    })
                                except: continue
            except Exception as e: logger.error(f"Erro Odds: {e}")

        # 2. NBA
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                url_nba = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
                r_nba = await client.get(url_nba)
                if r_nba.status_code == 200:
                    for event in r_nba.json().get('events', []):
                        comp = event['competitions'][0]
                        odds_details = comp.get('odds', [{}])[0].get('details', 'N/A')
                        if odds_details == 'N/A': continue # Só manda se tiver odd real

                        matches.append({
                            "id": event['id'], "sport": "🏀", "match": event['name'], "league": "NBA",
                            "time": "Noite", "odd": 1.91, "tip": f"Favorito {odds_details}", "score": 800, "ts": 0
                        })
        except: pass

        matches.sort(key=lambda x: -x["score"])
        top = matches[:15]
        self.db.set_cache("top10_matches", top)
        return top, "Dados Reais"

    async def get_hot_news(self):
        news_list = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # News NBA
                r = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news")
                if r.status_code == 200:
                    for a in r.json().get('articles', [])[:3]:
                        news_list.append({"title": a['headline'], "url": a['links']['web']['href'], "tag": "🏀 NBA"})
                # News UOL (Futebol)
                r_uol = await client.get("http://rss.uol.com.br/feed/esporte.xml")
                if r_uol.status_code == 200:
                    root = ET.fromstring(r_uol.content)
                    for item in root.findall('./channel/item')[:5]:
                        title = item.find('title').text
                        if any(k in title.lower() for k in BETTING_KEYWORDS) or "flamengo" in title.lower():
                            news_list.append({"title": title, "url": item.find('link').text, "tag": "⚽ FUTEBOL"})
        except: pass
        return news_list

# ================= HANDLERS (TODAS AS SUAS FUNÇÕES) =================
class Handlers:
    def __init__(self, db, api): self.db, self.api = db, api
    def is_admin(self, uid): return str(uid) == str(ADMIN_ID)

    async def start(self, u, c):
        if not self.is_admin(u.effective_user.id): 
            return await u.message.reply_text("⛔ Acesse o canal ou use `/ativar CHAVE` para o VIP.")
        
        kb = ReplyKeyboardMarkup([
            ["🔥 Top Jogos", "🚀 Múltipla Segura"], 
            ["💣 Troco do Pão", "🏀 NBA"], 
            ["📰 Escrever Notícia", "📢 Publicar no Canal"], 
            ["🎫 Gerar Key"]
        ], resize_keyboard=True)
        await u.message.reply_text(f"🦁 **PAINEL ADMIN V62.1**\nFutebol e NBA com Odds Reais e IA ativa.", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def games(self, u, c):
        msg = await u.message.reply_text("🔎 Consultando mercado real...")
        m, status = await self.api.get_matches()
        if not m: return await msg.edit_text("📭 Sem jogos com odds no momento.")
        
        txt = f"*🔥 TOP JOGOS ({status}):*\n\n"
        for g in m[:8]:
            txt += f"{g['sport']} {g['match']}\n🏆 {g['league']} | 🕒 {g['time']}\n🎯 {g['tip']} | 📈 @{g['odd']}\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_safe(self, u, c):
        m, _ = await self.api.get_matches()
        safe = [g for g in m if g['odd'] < 1.7]
        if len(safe) < 2: return await u.message.reply_text("Sem jogos seguros agora.")
        sel = random.sample(safe, min(3, len(safe)))
        odd_total = 1.0
        txt = "🚀 **MÚLTIPLA SEGURA**\n\n"
        for g in sel:
            odd_total *= g['odd']
            txt += f"✅ {g['match']} - {g['tip']} (@{g['odd']})\n"
        txt += f"\n💰 **ODD TOTAL: @{odd_total:.2f}**"
        await u.message.reply_text(txt)

    async def multi_risk(self, u, c):
        m, _ = await self.api.get_matches()
        risk = [g for g in m if g['odd'] >= 1.8]
        if len(risk) < 3: return await u.message.reply_text("Sem jogos para bilhete de risco.")
        sel = random.sample(risk, min(4, len(risk)))
        odd_total = 1.0
        txt = "💣 **BILHETE TROCO DO PÃO**\n\n"
        for g in sel:
            odd_total *= g['odd']
            txt += f"🔥 {g['match']} - {g['tip']} (@{g['odd']})\n"
        txt += f"\n💰 **ODD TOTAL: @{odd_total:.2f}**"
        await u.message.reply_text(txt)

    async def gen_key(self, u, c):
        if not self.is_admin(u.effective_user.id): return
        k = self.db.create_key((datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
        await u.message.reply_text(f"🔑 **KEY GERADA:** `{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active_vip(self, u, c):
        if not c.args: return await u.message.reply_text("Use: `/ativar VIP-XXXX`")
        if self.db.use_key(c.args[0], u.effective_user.id):
            await u.message.reply_text("✅ **VIP ATIVADO COM SUCESSO!**")
        else:
            await u.message.reply_text("❌ Key inválida ou já usada.")

    async def ask_news(self, u, c):
        if not self.is_admin(u.effective_user.id): return
        c.user_data['waiting_news'] = True
        await u.message.reply_text("📝 Digite a notícia ou mande uma foto com legenda:")

    async def process_input(self, u, c):
        if not c.user_data.get('waiting_news'): return
        txt = f"🚨 **PLANTÃO VIP**\n\n" + (u.message.caption or u.message.text or "")
        try:
            if u.message.photo: await c.bot.send_photo(CHANNEL_ID, u.message.photo[-1].file_id, caption=txt, parse_mode=ParseMode.MARKDOWN)
            else: await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.MARKDOWN)
            await u.message.reply_text("✅ Publicado!")
        except: await u.message.reply_text("❌ Erro ao postar.")
        c.user_data['waiting_news'] = False

# ================= SCHEDULER & WEB SERVER =================
async def main_scheduler(app, db, api):
    while True:
        try:
            now = datetime.now(timezone.utc) - timedelta(hours=3)
            # Notícias Automáticas com Gemini
            if now.minute % 30 == 0:
                news = await api.get_hot_news()
                for n in news:
                    if not db.is_news_sent(n['url']):
                        resumo = await api.analyze_with_gemini(n['title'])
                        if "PULAR" not in resumo:
                            msg = f"{n['tag']}\n🚨 **{n['title']}**\n\n💡 {resumo}\n\n[Ler mais]({n['url']})"
                            await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.MARKDOWN)
                            db.mark_news_sent(n['url'])
                            break
            await asyncio.sleep(60)
        except: await asyncio.sleep(60)

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"BOT V62.1 ONLINE")

def start_server():
    HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()

# ================= MAIN =================
async def main():
    if not BOT_TOKEN: return
    threading.Thread(target=start_server, daemon=True).start()
    db = Database(DB_PATH); api = SportsAPI(db); h = Handlers(db, api)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers Comandos
    app.add_handler(CommandHandler("start", h.start))
    app.add_handler(CommandHandler("ativar", h.active_vip))
    app.add_handler(CommandHandler("key", h.gen_key))
    
    # Handlers Botões Admin
    app.add_handler(MessageHandler(filters.Regex("^🔥"), h.games))
    app.add_handler(MessageHandler(filters.Regex("^🚀"), h.multi_safe))
    app.add_handler(MessageHandler(filters.Regex("^💣"), h.multi_risk))
    app.add_handler(MessageHandler(filters.Regex("^🎫"), h.gen_key))
    app.add_handler(MessageHandler(filters.Regex("^📰"), h.ask_news))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, h.process_input))

    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)
    
    asyncio.create_task(main_scheduler(app, db, api))
    
    logger.info("🔥 BOT V62.1 TOTALMENTE OPERACIONAL!")
    await app.updater.start_polling()
    while True: await asyncio.sleep(100)

if __name__ == "__main__":
    asyncio.run(main())
