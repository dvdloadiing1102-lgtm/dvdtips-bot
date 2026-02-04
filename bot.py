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
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode
import google.generativeai as genai

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID", "") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Chave que pegamos no Google Cloud
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "betting_bot.db"

# Inicializa Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    ai_model = None

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= FILTROS E LISTAS =================
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 848, 143, 45, 48, 528] 
BLOCKLIST_TERMS = ["U19", "U20", "U21", "U23", "WOMEN", "FEMININO", "YOUTH", "RESERVES"]
VIP_TEAMS_NAMES = ["FLAMENGO", "PALMEIRAS", "SAO PAULO", "CORINTHIANS", "REAL MADRID", "MANCHESTER CITY"]

BETTING_KEYWORDS = ["lesão", "desfalque", "fora", "dúvida", "suspenso", "titular", "reforço", "injury", "out"]

def normalize_str(s):
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
            c.execute("CREATE TABLE IF NOT EXISTS sent_news (news_url TEXT PRIMARY KEY, sent_at TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT UNIQUE, cache_data TEXT, expires_at TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS zebra_alerts (match_id TEXT PRIMARY KEY, alert_time TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS tips_history (id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, match_name TEXT, league TEXT, tip_type TEXT, odd REAL, date_sent DATE, status TEXT DEFAULT 'PENDING')")

    def set_cache(self, key, data):
        exp = (datetime.now() + timedelta(minutes=15)).isoformat()
        with self.get_conn() as conn:
            conn.cursor().execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, json.dumps(data), exp))

    def get_cache(self, key):
        with self.get_conn() as conn:
            res = conn.cursor().execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > ?", (key, datetime.now().isoformat())).fetchone()
            return json.loads(res[0]) if res else None

# ================= IA INTELLIGENCE =================
async def analyze_with_gemini(text):
    if not ai_model: return text[:200]
    prompt = f"Resuma para um apostador em 1 frase curta e impactante: {text}. Se não for importante, diga 'PULAR'."
    try:
        response = await asyncio.to_thread(ai_model.generate_content, prompt)
        return response.text.strip()
    except:
        return text[:200]

# ================= SPORTS API =================
class SportsAPI:
    def __init__(self, db): self.db = db

    async def get_matches(self):
        cached = self.db.get_cache("top_matches")
        if cached: return cached, "Cache"
        
        matches = []
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        
        # 1. FUTEBOL (PRIORIDADE)
        if API_FOOTBALL_KEY:
            try:
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(f"https://v3.football.api-sports.io/fixtures?date={today}", headers=headers)
                    if r.status_code == 200:
                        for g in r.json().get("response", []):
                            h_team = normalize_str(g["teams"]["home"]["name"])
                            a_team = normalize_str(g["teams"]["away"]["name"])
                            
                            p_score = 0
                            if g["league"]["id"] in VIP_LEAGUES_IDS: p_score += 1000
                            if "FLAMENGO" in h_team or "FLAMENGO" in a_team: p_score += 5000 # MENGÃO SEMPRE TOPO
                            
                            if p_score > 0:
                                matches.append({
                                    "id": g["fixture"]["id"], "sport": "⚽", 
                                    "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                                    "league": g["league"]["name"], "time": (datetime.fromtimestamp(g["fixture"]["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                    "odd": round(random.uniform(1.4, 2.5), 2), "tip": "Vitória / Over 1.5", "score": p_score, "ts": g["fixture"]["timestamp"]
                                })
            except Exception as e: logger.error(f"Erro Fut: {e}")

        # 2. NBA
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r_nba = await client.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard")
                if r_nba.status_code == 200:
                    for event in r_nba.json().get('events', []):
                        matches.append({
                            "id": event['id'], "sport": "🏀", "match": event['name'], "league": "NBA",
                            "time": "Noite", "odd": 1.90, "tip": "Over Pts", "score": 500, "ts": 0
                        })
        except: pass

        matches.sort(key=lambda x: -x["score"])
        self.db.set_cache("top_matches", matches[:15])
        return matches[:15], "API Live"

# ================= SCHEDULER & MAIN =================
async def daily_scheduler(app, db, api):
    while True:
        now = datetime.now(timezone.utc) - timedelta(hours=3)
        # Envio automático às 08:00 e 19:00
        if now.minute == 0 and now.hour in [8, 19]:
            await send_channel_report(app, db, api)
        
        # Check Notícias a cada 30 min
        if now.minute % 30 == 0:
            news = await api.get_hot_news()
            # Lógica de envio de news...
            
        await asyncio.sleep(60)

async def main():
    if not BOT_TOKEN: return
    db = Database(DB_PATH); api = SportsAPI(db)
    
    # Inicia Web Server para o Render não dar erro
    threading.Thread(target=start_fake_server, daemon=True).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers (Simplificados para o exemplo)
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("🦁 Bot Ativo!")))
    
    await app.initialize()
    await app.start()
    
    # Limpa Webhooks antigos para evitar CONFLICT
    await app.bot.delete_webhook(drop_pending_updates=True)
    
    asyncio.create_task(daily_scheduler(app, db, api))
    
    logger.info("🔥 BOT V62.1 RODANDO!")
    await app.updater.start_polling()
    while True: await asyncio.sleep(100)

# Servidor fake para o Render
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"ALIVE")

def start_fake_server():
    server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
    server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
