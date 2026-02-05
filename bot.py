import os
import asyncio
import logging
import sqlite3
import json
import secrets
import random
import threading
import httpx
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import contextmanager

# Telegram
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from telegram.constants import ParseMode

# NOVO PACOTE GOOGLE GENAI (Certifique-se de ter 'google-genai' no requirements.txt)
from google import genai

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"

# Inicializa o Novo Cliente Gemini (Versão 2026)
if GEMINI_API_KEY:
    client_gemini = genai.Client(api_key=GEMINI_API_KEY)
    MODEL_NAME = "gemini-1.5-flash"
else:
    client_gemini = None

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= FILTROS =================
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 13, 848] 
BETTING_KEYWORDS = ["lesao", "desfalque", "fora", "duvida", "suspenso", "titular", "reforço", "escalação", "relacionados"]

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
            c.execute("CREATE TABLE IF NOT EXISTS sent_news (news_url TEXT PRIMARY KEY, sent_at TIMESTAMP)")

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

# ================= SPORTS API (DADOS REAIS) =================
class SportsAPI:
    def __init__(self, db): self.db = db

    async def analyze_with_gemini(self, text):
        if not client_gemini: return text[:150]
        try:
            prompt = f"Como analista de apostas, resuma em 1 frase curta: {text}. Se for irrelevante, responda 'PULAR'."
            response = client_gemini.models.generate_content(model=MODEL_NAME, contents=prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Erro Gemini: {e}")
            return text[:150]

    async def get_matches(self):
        matches = []
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. FUTEBOL - ODDS REAIS
            try:
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                r = await client.get(f"https://v3.football.api-sports.io/odds?date={today}&bookmaker=6", headers=headers)
                if r.status_code == 200:
                    for item in r.json().get("response", []):
                        h_team = normalize_str(item["teams"]["home"]["name"])
                        a_team = normalize_str(item["teams"]["away"]["name"])
                        p_score = 5000 if "FLAMENGO" in h_team or "FLAMENGO" in a_team else 0
                        if item["league"]["id"] in VIP_LEAGUES_IDS: p_score += 1000
                        
                        if p_score > 0:
                            odds = item['bookmakers'][0]['bets'][0]['values']
                            fav = sorted(odds, key=lambda x: float(x['odd']))[0]
                            matches.append({
                                "sport": "⚽", "match": f"{item['teams']['home']['name']} x {item['teams']['away']['name']}",
                                "league": item["league"]["name"], "odd": float(fav['odd']), "tip": fav['value'], "score": p_score
                            })
            except: pass

            # 2. NBA - ODDS REAIS
            try:
                headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                r_nba = await client.get(f"https://v1.basketball.api-sports.io/odds?date={today}&league=12&bookmaker=6", headers=headers_nba)
                if r_nba.status_code == 200:
                    for item in r_nba.json().get("response", []):
                        fav_nba = sorted(item['bookmakers'][0]['bets'][0]['values'], key=lambda x: float(x['odd']))[0]
                        matches.append({
                            "sport": "🏀", "match": f"{item['teams']['home']['name']} x {item['teams']['away']['name']}",
                            "league": "NBA", "odd": float(fav_nba['odd']), "tip": fav_nba['value'], "score": 800
                        })
            except: pass

        matches.sort(key=lambda x: -x["score"])
        return matches[:15]

    async def get_hot_news(self):
        news = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Prioridade GE (Futebol)
                r = await client.get("https://ge.globo.com/servico/semantica/editorias/plantao/futebol/feed.rss")
                root = ET.fromstring(r.content)
                for item in root.findall('./channel/item')[:12]:
                    title = item.find('title').text
                    url = item.find('link').text
                    if "flamengo" in title.lower():
                        news.append({"title": f"🔴⚫ {title}", "url": url, "tag": "MENGO URGENTE"})
                    elif any(k in normalize_str(title.lower()) for k in BETTING_KEYWORDS):
                        news.append({"title": title, "url": url, "tag": "⚽ FUTEBOL"})
                
                # NBA Limitada
                if len(news) < 4:
                    r_espn = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news")
                    for a in r_espn.json().get('articles', [])[:2]:
                        news.append({"title": a['headline'], "url": a['links']['web']['href'], "tag": "🏀 NBA"})
        except: pass
        return news

# ================= HANDLERS =================
class Handlers:
    def __init__(self, db, api): self.db, self.api = db, api
    def is_admin(self, uid): return str(uid) == str(ADMIN_ID)

    async def start(self, u, c):
        if not self.is_admin(u.effective_user.id): return
        kb = ReplyKeyboardMarkup([["🔥 Top Jogos", "🚀 Múltipla Segura"], ["💣 Troco do Pão", "🏀 NBA"], ["📰 Notícia", "🎫 Gerar Key"]], resize_keyboard=True)
        await u.message.reply_text("🦁 **PAINEL V62.1 - GENAI ATIVA**", reply_markup=kb)

    async def top_games(self, u, c):
        msg = await u.message.reply_text("🔎 Buscando odds reais...")
        m = await self.api.get_matches()
        txt = "🔥 **GRADE DE ELITE**\n\n"
        for g in m[:8]: txt += f"{g['sport']} {g['match']}\n🎯 {g['tip']} | @{g['odd']}\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_risk(self, u, c):
        m = await self.api.get_matches()
        sel = random.sample(m, min(5, len(m)))
        odd_t = 1.0
        res = "💣 **TROCO DO PÃO (ODD 20+)**\n\n"
        for g in sel:
            odd_t *= g['odd']
            res += f"🔥 {g['match']} (@{g['odd']})\n"
        res += f"\n💰 **ODD FINAL: @{odd_t:.2f}**"
        await u.message.reply_text(res)

    async def gen_key(self, u, c):
        if not self.is_admin(u.effective_user.id): return
        k = self.db.create_key("2026-12-31")
        await u.message.reply_text(f"🔑 **KEY:** `{k}`", parse_mode=ParseMode.MARKDOWN)

# ================= SERVER & MAIN =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE")

async def main():
    db = Database(DB_PATH); api = SportsAPI(db); h = Handlers(db, api)
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever(), daemon=True).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", h.start))
    app.add_handler(MessageHandler(filters.Regex("^🔥"), h.top_games))
    app.add_handler(MessageHandler(filters.Regex("^💣"), h.multi_risk))
    app.add_handler(MessageHandler(filters.Regex("^🎫"), h.gen_key))
    
    await app.initialize(); await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)
    
    async def scheduler():
        while True:
            try:
                news = await api.get_hot_news()
                for n in news:
                    with db.get_conn() as conn:
                        if not conn.cursor().execute("SELECT 1 FROM sent_news WHERE news_url = ?", (n['url'],)).fetchone():
                            analise = await api.analyze_with_gemini(n['title'])
                            if "PULAR" not in analise:
                                msg = f"{n['tag']}\n🚨 **{n['title']}**\n\n💡 {analise}\n\n[🔗 Ler mais]({n['url']})"
                                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.MARKDOWN)
                                conn.cursor().execute("INSERT INTO sent_news VALUES (?, ?)", (n['url'], datetime.now()))
                                await asyncio.sleep(5)
                await asyncio.sleep(600)
            except: await asyncio.sleep(60)

    asyncio.create_task(scheduler())
    await app.updater.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
