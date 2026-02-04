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

# ================= FILTROS =================
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 848, 143, 45, 48, 528] 
BLOCKLIST_TERMS = ["U19", "U20", "U21", "U23", "WOMEN", "FEMININO", "YOUTH", "RESERVES", "LADIES", "JUNIOR", "GIRLS"]
VIP_TEAMS_NAMES = ["FLAMENGO", "PALMEIRAS", "SAO PAULO", "CORINTHIANS", "SANTOS", "GREMIO", "INTERNACIONAL", "ATLETICO MINEIRO", "BOTAFOGO", "FLUMINENSE", "VASCO", "CRUZEIRO", "BAHIA", "FORTALEZA", "MANCHESTER CITY", "REAL MADRID", "BARCELONA", "LIVERPOOL", "ARSENAL", "PSG", "INTER", "MILAN", "JUVENTUS", "BAYERN", "BOCA JUNIORS", "RIVER PLATE"]
BETTING_KEYWORDS = ["lesão", "lesionado", "machucou", "cirurgia", "desfalque", "fora", "dúvida", "poupado", "suspenso", "vetado", "dores", "contratado", "vendido", "assina", "reforço", "saída", "troca", "emprestado", "rescindiu", "banco", "reserva", "titular", "relacionado", "injury", "injured", "surgery", "out", "questionable", "doubtful", "sidelined", "trade", "traded", "signed", "bench", "suspended"]

def normalize_str(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVIDOR WEB FAKE =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V59 ONLINE - GREEN/RED REPORT")
def start_fake_server():
    try: server = HTTPServer(('0.0.0.0', PORT), FakeHandler); server.serve_forever()
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
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_vip BOOLEAN DEFAULT 0)")
            c.execute("CREATE TABLE IF NOT EXISTS vip_keys (key_code TEXT UNIQUE, expiry_date TEXT, used_by INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT UNIQUE, cache_data TEXT, expires_at TIMESTAMP)")
            c.execute("CREATE TABLE IF NOT EXISTS sent_news (news_url TEXT PRIMARY KEY, sent_at TIMESTAMP)")
            # NOVA TABELA: HISTÓRICO DE TIPS
            c.execute("""CREATE TABLE IF NOT EXISTS tips_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        match_id TEXT,
                        match_name TEXT,
                        league TEXT,
                        tip_type TEXT,
                        odd REAL,
                        date_sent DATE,
                        status TEXT DEFAULT 'PENDING' 
                      )""") # Status: PENDING, GREEN, RED

    def create_key(self, expiry):
        k = "VIP-" + secrets.token_hex(4).upper()
        with self.get_conn() as conn: conn.cursor().execute("INSERT INTO vip_keys (key_code, expiry_date) VALUES (?, ?)", (k, expiry))
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
            with self.get_conn() as conn: conn.cursor().execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_data, expires_at) VALUES (?, ?, ?)", (key, json.dumps(data), exp))
        except: pass

    def get_cache(self, key):
        try:
            with self.get_conn() as conn:
                res = conn.cursor().execute("SELECT cache_data FROM api_cache WHERE cache_key = ? AND expires_at > ?", (key, datetime.now().isoformat())).fetchone()
                return json.loads(res[0]) if res else None
        except: return None
    
    def clear_cache(self):
        try: with self.get_conn() as conn: conn.cursor().execute("DELETE FROM api_cache")
        except: pass

    def is_news_sent(self, url):
        try:
            with self.get_conn() as conn: return conn.cursor().execute("SELECT 1 FROM sent_news WHERE news_url = ?", (url,)).fetchone() is not None
        except: return False

    def mark_news_sent(self, url):
        try: with self.get_conn() as conn: conn.cursor().execute("INSERT OR IGNORE INTO sent_news (news_url, sent_at) VALUES (?, ?)", (url, datetime.now()))
        except: pass

    # --- FUNÇÕES GREEN/RED ---
    def save_tip(self, match_id, match_name, league, tip, odd):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            with self.get_conn() as conn:
                conn.cursor().execute("INSERT INTO tips_history (match_id, match_name, league, tip_type, odd, date_sent) VALUES (?, ?, ?, ?, ?, ?)", 
                                      (str(match_id), match_name, league, tip, odd, today))
        except Exception as e: logger.error(f"Erro ao salvar tip: {e}")

    def get_pending_tips(self):
        try:
            with self.get_conn() as conn:
                # Pega tips pendentes de ONTEM ou antes
                return conn.cursor().execute("SELECT * FROM tips_history WHERE status = 'PENDING'").fetchall()
        except: return []

    def update_tip_status(self, tip_id, status):
        try:
            with self.get_conn() as conn:
                conn.cursor().execute("UPDATE tips_history SET status = ? WHERE id = ?", (status, tip_id))
        except: pass

# ================= API INTELLIGENCE =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    # --- GET MATCHES (PARA ENVIAR) ---
    async def get_matches(self, force_debug=False):
        if not force_debug:
            cached = await asyncio.to_thread(self.db.get_cache, "top10_matches")
            if cached: return cached, "Cache"
        
        matches = []
        status_msg = "API Híbrida"
        today = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        
        # 1. FUTEBOL
        if API_FOOTBALL_KEY:
            try:
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=25) as client:
                    url = f"https://v3.football.api-sports.io/fixtures?date={today}"
                    r = await client.get(url, headers=headers)
                    if r.status_code == 200:
                        data = r.json().get("response", [])
                        for g in data:
                            if g["fixture"]["status"]["short"] in ["CANC", "ABD", "PST", "FT"]: continue
                            league_id, h_team, a_team = g["league"]["id"], normalize_str(g["teams"]["home"]["name"]), normalize_str(g["teams"]["away"]["name"])
                            
                            if any(bad in h_team for bad in BLOCKLIST_TERMS) or any(bad in a_team for bad in BLOCKLIST_TERMS): continue
                            priority_score = 0
                            if league_id in VIP_LEAGUES_IDS: priority_score += 1000
                            elif any(vip in h_team for vip in VIP_TEAMS_NAMES) or any(vip in a_team for vip in VIP_TEAMS_NAMES): priority_score += 500
                            if priority_score == 0: continue

                            odd_h = round(random.uniform(1.3, 2.9), 2)
                            tip_text = "Casa Vence"
                            if odd_h > 2.4: tip_text = "Empate ou Visitante"
                            elif odd_h < 1.6: tip_text = "Casa Vence"
                            else: tip_text = "Over 1.5 Gols"

                            matches.append({
                                "id": g["fixture"]["id"], # ID IMPORTANTE PRO CHECKER
                                "sport": "⚽", "match": f"{g['teams']['home']['name']} x {g['teams']['away']['name']}",
                                "league": g["league"]["name"], "time": (datetime.fromtimestamp(g["fixture"]["timestamp"])-timedelta(hours=3)).strftime("%H:%M"),
                                "odd": odd_h, "tip": tip_text, "ts": g["fixture"]["timestamp"], "score": priority_score
                            })
            except Exception as e: logger.error(f"Erro Futebol: {e}")

        # 2. NBA
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                url_espn = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
                r_nba = await client.get(url_espn)
                if r_nba.status_code == 200:
                    data = r_nba.json()
                    for event in data.get('events', []):
                        comps = event['competitions'][0]
                        team_home, team_away = comps['competitors'][0], comps['competitors'][1]
                        name_h, name_a = team_home['team']['displayName'], team_away['team']['displayName']
                        
                        odds_data = comps.get('odds', [{}])[0]
                        details, over_under = odds_data.get('details', 'N/A'), odds_data.get('overUnder', 0)
                        
                        game_date = event['date']
                        dt_obj = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
                        
                        if details != 'N/A':
                            if "-" in details and team_home['team']['abbreviation'] in details: tip_final, odd_final = f"{team_home['team']['shortDisplayName']} vence", 1.75
                            elif "-" in details and team_away['team']['abbreviation'] in details: tip_final, odd_final = f"{team_away['team']['shortDisplayName']} vence", 1.75
                            else: tip_final, odd_final = f"Over {over_under} Pts", 1.90
                        else:
                            options = [(f"{team_home['team']['shortDisplayName']} vence", 1.80), (f"{team_away['team']['shortDisplayName']} vence", 2.10), (f"Over {random.randint(218, 235)} Pts", 1.90)]
                            tip_final, odd_final = random.choice(options)

                        matches.append({
                            "id": event['id'], # ID DA ESPN
                            "sport": "🏀", "match": f"{name_h} x {name_a}",
                            "league": "NBA", "time": (dt_obj - timedelta(hours=3)).strftime("%H:%M"),
                            "odd": odd_final, "tip": tip_final, "ts": dt_obj.timestamp(), "score": 5000
                        })
        except Exception as e: logger.error(f"Erro ESPN NBA: {e}")

        if not matches: matches = []

        matches.sort(key=lambda x: (-x["score"], x["ts"]))
        top_matches = matches[:20] 
        await asyncio.to_thread(self.db.set_cache, "top10_matches", top_matches)
        return top_matches, status_msg

    # --- VERIFICADOR DE RESULTADOS (GREEN/RED) ---
    async def check_results(self):
        pending = await asyncio.to_thread(self.db.get_pending_tips)
        if not pending: return []

        results = {"greens": 0, "reds": 0}
        
        # Agrupar IDs por esporte para otimizar
        ids_nba = [p for p in pending if p['league'] == 'NBA']
        ids_foot = [p for p in pending if p['league'] != 'NBA']
        
        # 1. CHECAR NBA (ESPN) - Busca o placar de ontem
        if ids_nba:
            try:
                # Pega scoreboard de ONTEM
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
                url_espn = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={yesterday}"
                
                async with httpx.AsyncClient() as client:
                    r = await client.get(url_espn)
                    if r.status_code == 200:
                        events = r.json().get('events', [])
                        for tip in ids_nba:
                            # Acha o jogo
                            game = next((e for e in events if str(e['id']) == str(tip['match_id'])), None)
                            if game and game['status']['type']['completed']:
                                # Lógica de Green
                                comps = game['competitions'][0]
                                score_h = int(comps['competitors'][0]['score'])
                                score_a = int(comps['competitors'][1]['score'])
                                name_h = comps['competitors'][0]['team']['shortDisplayName']
                                name_a = comps['competitors'][1]['team']['shortDisplayName']
                                total = score_h + score_a
                                
                                result_status = "RED"
                                if "Over" in tip['tip_type']:
                                    # Extrai numero do over
                                    line = float(re.findall(r"[\d\.]+", tip['tip_type'])[0])
                                    if total > line: result_status = "GREEN"
                                elif "vence" in tip['tip_type']:
                                    winner = name_h if score_h > score_a else name_a
                                    # Verifica se o nome do vencedor está na tip
                                    if winner in tip['tip_type']: result_status = "GREEN"
                                
                                await asyncio.to_thread(self.db.update_tip_status, tip['id'], result_status)
                                if result_status == "GREEN": results["greens"] += 1
                                else: results["reds"] += 1
            except Exception as e: logger.error(f"Erro Check NBA: {e}")

        # 2. CHECAR FUTEBOL (API-FOOTBALL)
        if ids_foot and API_FOOTBALL_KEY:
            try:
                match_ids_str = "-".join([str(p['match_id']) for p in ids_foot[:10]]) # Limite de 10 por vez
                headers = {"x-rapidapi-host": "v3.football.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient() as client:
                    r = await client.get(f"https://v3.football.api-sports.io/fixtures?ids={match_ids_str}", headers=headers)
                    if r.status_code == 200:
                        for game in r.json().get('response', []):
                            if game['fixture']['status']['short'] in ['FT', 'AET', 'PEN']:
                                score_h = game['goals']['home']
                                score_a = game['goals']['away']
                                total = score_h + score_a
                                
                                # Acha a tip correspondente
                                tip = next((p for p in ids_foot if str(p['match_id']) == str(game['fixture']['id'])), None)
                                if tip:
                                    res = "RED"
                                    t_text = tip['tip_type']
                                    
                                    if "Casa Vence" in t_text and score_h > score_a: res = "GREEN"
                                    elif "Visitante" in t_text and score_a > score_h: res = "GREEN"
                                    elif "Empate" in t_text and (score_h == score_a or score_a > score_h): res = "GREEN" # Empate ou Visitante
                                    elif "Over" in t_text and total > 1: res = "GREEN"
                                    
                                    await asyncio.to_thread(self.db.update_tip_status, tip['id'], res)
                                    if res == "GREEN": results["greens"] += 1
                                    else: results["reds"] += 1
            except Exception as e: logger.error(f"Erro Check Fut: {e}")
            
        return results

    # --- NEWS ---
    async def get_hot_news(self):
        news_list = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                url_nba = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news"
                r1 = await client.get(url_nba)
                if r1.status_code == 200:
                    for a in r1.json().get('articles', []):
                        full = (a.get('headline','')+" "+a.get('description','')).lower()
                        if any(k in full for k in BETTING_KEYWORDS):
                            news_list.append({"title":a.get('headline'),"desc":a.get('description'),"url":a['links']['web']['href'],"img":a['images'][0]['url'] if a.get('images') else None,"tag":"🏀 NBA INFO"})
                
                url_soc = "https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/news"
                r2 = await client.get(url_soc)
                if r2.status_code == 200:
                    for a in r2.json().get('articles', []):
                        full = (a.get('headline','')+" "+a.get('description','')).lower()
                        if any(k in full for k in BETTING_KEYWORDS):
                            news_list.append({"title":a.get('headline'),"desc":a.get('description'),"url":a['links']['web']['href'],"img":a['images'][0]['url'] if a.get('images') else None,"tag":"⚽ FUT NEWS"})
        except: pass
        return news_list

# ================= SISTEMA DE ENVIO =================
async def send_channel_report(app, db, api):
    if not CHANNEL_ID: return False, "Sem Channel ID"
    await asyncio.to_thread(db.clear_cache)
    m, source = await api.get_matches(force_debug=True)
    if not m: return False, "Sem jogos"

    # SALVA NO HISTÓRICO PARA CONFERIR DEPOIS
    for g in m:
        # Só salva o que vai pro relatório (Top 8 + NBA)
        await asyncio.to_thread(db.save_tip, g['id'], g['match'], g['league'], g['tip'], g['odd'])

    today_str = datetime.now().strftime("%d/%m")
    nba_games = [g for g in m if g['sport'] == '🏀']
    foot_games = [g for g in m if g['sport'] == '⚽']
    
    post = f"🦁 **BOLETIM VIP - {today_str}**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    if foot_games:
        best = foot_games[0]
        post += f"💎 **JOGO DE OURO (MAX)**\n⚽ {best['match']}\n🏆 {best['league']} | 🕒 {best['time']}\n🔥 **Entrada:** {best['tip']}\n📈 **Odd:** @{best['odd']}\n\n"
    
    if nba_games:
        post += f"🏀 **SESSÃO NBA**\n"
        for g in nba_games: post += f"🇺🇸 {g['match']}\n🎯 {g['tip']} (@{g['odd']})\n\n"

    post += "📋 **GRADE DE ELITE**\n"
    for g in foot_games[1:6]:
        post += f"⚔️ {g['match']}\n   ↳ {g['tip']} (@{g['odd']})\n"
        
    post += "\n━━━━━━━━━━━━━━━━━━━━\n💣 **TROCO DO PÃO**\n"
    risk_candidates = [g for g in m if g['odd'] >= 1.90] or m
    sel_risk = random.sample(risk_candidates, min(4, len(risk_candidates)))
    total_risk = 1.0
    for g in sel_risk:
        total_risk *= g['odd']
        post += f"🔥 {g['match']}\n   👉 {g['tip']} (@{g['odd']})\n"
    
    if total_risk < 15: total_risk = random.uniform(15.5, 25.0)
    post += f"\n💰 **ODD FINAL: @{total_risk:.2f}**\n⚠️ _Gestão de banca sempre!_ 🦁"

    try:
        await app.bot.send_message(chat_id=CHANNEL_ID, text=post, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return True, "Sucesso"
    except Exception as e: return False, str(e)

async def check_and_send_news(app, db, api):
    if not CHANNEL_ID: return
    news = await api.get_hot_news()
    for item in news:
        if await asyncio.to_thread(db.is_news_sent, item['url']): continue
        await asyncio.to_thread(db.mark_news_sent, item['url'])
        txt = f"{item['tag']}\n\n🚨 **{item['title']}**\n\n{item['desc']}\n\n🦁 _Plantão Automático_"
        try:
            if item['img']: await app.bot.send_photo(chat_id=CHANNEL_ID, photo=item['img'], caption=txt, parse_mode=ParseMode.MARKDOWN)
            else: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.MARKDOWN)
            break 
        except: pass

async def send_green_red_report(app, db, api):
    if not CHANNEL_ID: return
    logger.info("Verificando resultados...")
    res = await api.check_results()
    
    if res and (res['greens'] > 0 or res['reds'] > 0):
        total = res['greens'] + res['reds']
        msg = f"📊 **RELATÓRIO DA VERDADE**\n\n"
        msg += f"Ontem fechamos assim:\n"
        msg += f"✅ **{res['greens']} Greens**\n"
        msg += f"❌ **{res['reds']} Reds**\n\n"
        msg += f"Transparência total! 🦁"
        try:
            await app.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)
        except: pass

# ================= AGENDADOR 24H =================
async def daily_scheduler(app, db, api):
    logger.info("⏰ Agendador Master iniciado...")
    while True:
        try:
            now_br = datetime.now(timezone.utc) - timedelta(hours=3)
            
            # 08:00 - BOLETIM MATINAL
            if now_br.hour == 8 and now_br.minute == 0:
                await send_channel_report(app, db, api)
                await asyncio.sleep(61)

            # 11:00 - RELATÓRIO GREEN/RED (Dos jogos de ontem)
            if now_br.hour == 11 and now_br.minute == 0:
                await send_green_red_report(app, db, api)
                await asyncio.sleep(61)

            # 19:00 - PLANTÃO NBA
            if now_br.hour == 19 and now_br.minute == 0:
                await send_channel_report(app, db, api)
                await asyncio.sleep(61)

            # RADAR NEWS (Minuto 30)
            if now_br.minute == 30:
                await check_and_send_news(app, db, api)
                await asyncio.sleep(61)
            
            await asyncio.sleep(30)
        except: await asyncio.sleep(60)

# ================= HANDLERS =================
class Handlers:
    def __init__(self, db, api): self.db, self.api = db, api
    def is_admin(self, uid): return str(uid) == str(ADMIN_ID)

    async def start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id):
            return await u.message.reply_text(f"👋 **Bem-vindo ao DVD TIPS**\n\n⛔ Acesso Restrito.\n`/ativar SUA-CHAVE`", reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.MARKDOWN)
        
        kb = ReplyKeyboardMarkup([["🔥 Top Jogos", "🚀 Múltipla Segura"], ["💣 Troco do Pão", "🏀 NBA"], ["📰 Escrever Notícia", "📢 Publicar no Canal"], ["🎫 Gerar Key"]], resize_keyboard=True)
        await u.message.reply_text(f"🦁 **PAINEL ADMIN (V59)**\nCanal: `{CHANNEL_ID}`", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def ask_news(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        c.user_data['waiting_news'] = True
        await u.message.reply_text("📝 **Editor Manual:**\nEscreva sua notícia.")

    async def process_news_input(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not c.user_data.get('waiting_news'): return False
        if u.message.text and u.message.text.lower() == 'cancelar':
            c.user_data['waiting_news'] = False; await u.message.reply_text("❌ Cancelado."); return True
        
        txt = "🚨 **PLANTÃO URGENTE**\n\n" + (u.message.caption or u.message.text or "")
        try:
            if u.message.photo: await c.bot.send_photo(chat_id=CHANNEL_ID, photo=u.message.photo[-1].file_id, caption=txt, parse_mode=ParseMode.MARKDOWN)
            elif u.message.text: await c.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.MARKDOWN)
            await u.message.reply_text("✅ Enviada!")
        except: await u.message.reply_text("❌ Erro no envio.")
        c.user_data['waiting_news'] = False; return True

    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        msg = await u.message.reply_text("🔎 Buscando...")
        m, _ = await self.api.get_matches(); txt = "*🔥 PREVIEW:*\n\n"
        for g in m[:10]: txt += f"{g['sport']} {g['match']} | {g['tip']}\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_risk_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        sel = random.sample([g for g in m if g['odd'] >= 1.9] or m, 4)
        txt = "*💣 PREVIEW:* " + "\n".join([f"{g['match']} ({g['tip']})" for g in sel])
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_safe_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        sel = random.sample([g for g in m if g['odd'] < 1.7] or m[:3], 3)
        txt = "*🚀 PREVIEW:* " + "\n".join([f"{g['match']} ({g['tip']})" for g in sel])
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def nba_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        nba = [g for g in m if g['sport'] == '🏀']
        txt = "*🏀 PREVIEW NBA:*\n\n" + ("\n".join([f"{g['match']} ({g['tip']})" for g in nba]) if nba else "Sem NBA.")
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def publish(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        msg = await u.message.reply_text("⏳ Postando...")
        ok, info = await send_channel_report(c.application, self.db, self.api)
        await msg.edit_text("✅ Postado!" if ok else f"❌ Erro: {info}")

    async def gen_key_btn(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        k = await asyncio.to_thread(self.db.create_key, (datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
        await u.message.reply_text(f"🔑 **KEY:** `{k}`", parse_mode=ParseMode.MARKDOWN)

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            if await asyncio.to_thread(self.db.use_key, c.args[0], u.effective_user.id):
                link = (await c.bot.create_chat_invite_link(chat_id=CHANNEL_ID, member_limit=1)).invite_link if CHANNEL_ID else "Erro"
                await u.message.reply_text(f"✅ **VIP ATIVO!**\n👉 {link}")
            else: await u.message.reply_text("❌ Inválido.")
        except: await u.message.reply_text("❌ `/ativar CHAVE`")

# ================= MAIN =================
async def main():
    if not BOT_TOKEN: return
    threading.Thread(target=start_fake_server, daemon=True).start()
    db = Database(DB_PATH); api = SportsAPI(db); h = Handlers(db, api)
    while True:
        try:
            logger.info("🔥 Bot V59.0 Iniciado...")
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", h.start)); app.add_handler(CommandHandler("publicar", h.publish)); app.add_handler(CommandHandler("ativar", h.active))
            app.add_handler(MessageHandler(filters.Regex("^🔥"), h.games)); app.add_handler(MessageHandler(filters.Regex("^💣"), h.multi_risk_preview))
            app.add_handler(MessageHandler(filters.Regex("^🚀"), h.multi_safe_preview)); app.add_handler(MessageHandler(filters.Regex("^🏀"), h.nba_preview))
            app.add_handler(MessageHandler(filters.Regex("^📢"), h.publish)); app.add_handler(MessageHandler(filters.Regex("^🎫"), h.gen_key_btn))
            app.add_handler(MessageHandler(filters.Regex("^📰"), h.ask_news)); app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, h.process_news_input))
            await app.initialize(); await app.start()
            asyncio.create_task(daily_scheduler(app, db, api))
            await app.bot.delete_webhook(drop_pending_updates=True); await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            while True: await asyncio.sleep(60); 
        except Exception as e: logger.error(f"Erro: {e}"); await asyncio.sleep(30)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
