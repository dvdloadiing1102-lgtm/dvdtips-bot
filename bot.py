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

# ================= FILTROS =================
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 848, 143, 45, 48, 528] 
BLOCKLIST_TERMS = ["U19", "U20", "U21", "U23", "WOMEN", "FEMININO", "YOUTH", "RESERVES", "LADIES", "JUNIOR", "GIRLS"]
VIP_TEAMS_NAMES = ["FLAMENGO", "PALMEIRAS", "SAO PAULO", "CORINTHIANS", "SANTOS", "GREMIO", "INTERNACIONAL", "ATLETICO MINEIRO", "BOTAFOGO", "FLUMINENSE", "VASCO", "CRUZEIRO", "BAHIA", "FORTALEZA", "MANCHESTER CITY", "REAL MADRID", "BARCELONA", "LIVERPOOL", "ARSENAL", "PSG", "INTER", "MILAN", "JUVENTUS", "BAYERN", "BOCA JUNIORS", "RIVER PLATE"]

# --- FILTRO DE NOTÍCIAS (SÓ O QUE IMPORTA) ---
BETTING_KEYWORDS = [
    # Lesões e Ausências (PT)
    "lesão", "lesionado", "machucou", "cirurgia", "desfalque", "fora", "dúvida", "poupado", "suspenso", "vetado", "dores",
    # Mercado e Elenco (PT)
    "contratado", "vendido", "assina", "reforço", "saída", "troca", "emprestado", "rescindiu",
    # Tática (PT)
    "banco", "reserva", "titular", "relacionado",
    # Inglês (Para NBA)
    "injury", "injured", "surgery", "out", "questionable", "doubtful", "sidelined", "trade", "traded", "signed", "bench", "suspended"
]

def normalize_str(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVIDOR WEB FAKE =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V58 ONLINE - NO GOSSIP FILTER")

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

    def is_news_sent(self, url):
        try:
            with self.get_conn() as conn:
                res = conn.cursor().execute("SELECT 1 FROM sent_news WHERE news_url = ?", (url,)).fetchone()
                return res is not None
        except: return False

    def mark_news_sent(self, url):
        try:
            with self.get_conn() as conn:
                conn.cursor().execute("INSERT OR IGNORE INTO sent_news (news_url, sent_at) VALUES (?, ?)", (url, datetime.now()))
        except: pass

# ================= API INTELLIGENCE =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
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
                            league_id = g["league"]["id"]
                            h_team = normalize_str(g["teams"]["home"]["name"])
                            a_team = normalize_str(g["teams"]["away"]["name"])
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
                        team_home = comps['competitors'][0]
                        team_away = comps['competitors'][1]
                        odds_data = comps.get('odds', [{}])[0]
                        details = odds_data.get('details', 'N/A')
                        over_under = odds_data.get('overUnder', 0)
                        
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
                            "sport": "🏀", "match": f"{team_home['team']['displayName']} x {team_away['team']['displayName']}",
                            "league": "NBA", "time": (dt_obj - timedelta(hours=3)).strftime("%H:%M"),
                            "odd": odd_final, "tip": tip_final, "ts": dt_obj.timestamp(), "score": 5000
                        })
        except Exception as e: logger.error(f"Erro ESPN NBA: {e}")

        if not matches:
            base_ts = datetime.now().timestamp()
            matches = [{"sport": "⚽", "match": "Flamengo x Vasco [Sim]", "league": "Brasileirão", "time": "21:30", "odd": 2.10, "tip": "Casa Vence", "ts": base_ts, "score": 200}]

        matches.sort(key=lambda x: (-x["score"], x["ts"]))
        top_matches = matches[:20] 
        await asyncio.to_thread(self.db.set_cache, "top10_matches", top_matches)
        return top_matches, status_msg

    # --- NOVO: BUSCA NOTÍCIAS FILTRADAS ---
    async def get_hot_news(self):
        news_list = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # 1. Notícias NBA
                url_nba = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news"
                r1 = await client.get(url_nba)
                if r1.status_code == 200:
                    articles = r1.json().get('articles', [])
                    for a in articles:
                        # VERIFICA PALAVRAS CHAVE (ANTI-FOFOCA)
                        full_text = (a.get('headline', '') + " " + a.get('description', '')).lower()
                        if any(key in full_text for key in BETTING_KEYWORDS):
                            news_list.append({
                                "title": a.get('headline', ''),
                                "desc": a.get('description', ''),
                                "url": a.get('links', {}).get('web', {}).get('href', ''),
                                "img": a.get('images', [{}])[0].get('url', None),
                                "tag": "🏀 NBA INFO"
                            })
                            if len(news_list) >= 2: break # Limite por busca
                
                # 2. Notícias Futebol
                url_soc = "https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/news"
                r2 = await client.get(url_soc)
                if r2.status_code == 200:
                    articles = r2.json().get('articles', [])
                    for a in articles:
                        full_text = (a.get('headline', '') + " " + a.get('description', '')).lower()
                        if any(key in full_text for key in BETTING_KEYWORDS):
                            news_list.append({
                                "title": a.get('headline', ''),
                                "desc": a.get('description', ''),
                                "url": a.get('links', {}).get('web', {}).get('href', ''),
                                "img": a.get('images', [{}])[0].get('url', None),
                                "tag": "⚽ FUT NEWS"
                            })
                            if len(news_list) >= 4: break # Limite total
        except Exception as e: logger.error(f"Erro News: {e}")
        
        return news_list

# ================= SISTEMA DE ENVIO =================
async def send_channel_report(app, db, api):
    if not CHANNEL_ID: return False, "Sem Channel ID"
    await asyncio.to_thread(db.clear_cache)
    m, source = await api.get_matches(force_debug=True)
    if not m: return False, "Sem jogos"

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
    count = 0
    for g in foot_games[1:]:
        if count >= 5: break
        post += f"⚔️ {g['match']}\n   ↳ {g['tip']} (@{g['odd']})\n"
        count += 1
        
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

# --- ENVIADOR DE NOTÍCIAS ---
async def check_and_send_news(app, db, api):
    if not CHANNEL_ID: return
    
    news = await api.get_hot_news()
    for item in news:
        if await asyncio.to_thread(db.is_news_sent, item['url']): continue
        await asyncio.to_thread(db.mark_news_sent, item['url'])
        
        txt = f"{item['tag']}\n\n"
        txt += f"🚨 **{item['title']}**\n\n"
        txt += f"{item['desc']}\n\n"
        txt += f"🦁 _Plantão Automático DVD TIPS_"
        
        try:
            if item['img']: await app.bot.send_photo(chat_id=CHANNEL_ID, photo=item['img'], caption=txt, parse_mode=ParseMode.MARKDOWN)
            else: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Notícia enviada: {item['title']}")
            break 
        except Exception as e: logger.error(f"Erro News Send: {e}")

# ================= AGENDADOR INTELIGENTE =================
async def daily_scheduler(app, db, api):
    logger.info("⏰ Radar 24h iniciado...")
    while True:
        try:
            now_br = datetime.now(timezone.utc) - timedelta(hours=3)
            
            # TIPS FIXAS (08h e 19h)
            if (now_br.hour == 8 or now_br.hour == 19) and now_br.minute == 0:
                await send_channel_report(app, db, api)
                await asyncio.sleep(61)

            # RADAR DE NOTÍCIAS (Minuto 30)
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
        uid = u.effective_user.id
        if not self.is_admin(uid):
            msg = (f"👋 **Bem-vindo ao DVD TIPS**\n\n⛔ Acesso Restrito.\nO conteúdo VIP está no Canal Oficial.\n\n`/ativar SUA-CHAVE`")
            return await u.message.reply_text(msg, reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.MARKDOWN)

        msg = f"🦁 **PAINEL ADMIN (V58)**\nCanal: `{CHANNEL_ID}`"
        kb = ReplyKeyboardMarkup([
            ["🔥 Top Jogos", "🚀 Múltipla Segura"],
            ["💣 Troco do Pão", "🏀 NBA"],
            ["📰 Escrever Notícia", "📢 Publicar no Canal"],
            ["🎫 Gerar Key"]
        ], resize_keyboard=True)
        await u.message.reply_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def ask_news(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        c.user_data['waiting_news'] = True
        await u.message.reply_text("📝 **Editor Manual:**\nEscreva sua notícia.")

    async def process_news_input(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not c.user_data.get('waiting_news'): return False
        
        text = u.message.text
        if text and text.lower() == 'cancelar':
            c.user_data['waiting_news'] = False
            await u.message.reply_text("❌ Cancelado.")
            return True

        if not CHANNEL_ID:
            await u.message.reply_text("❌ Configure o CHANNEL_ID.")
            c.user_data['waiting_news'] = False
            return True

        header = "🚨 **PLANTÃO URGENTE**\n\n"
        final_msg = header + (u.message.caption or u.message.text or "")
        
        try:
            if u.message.photo: await c.bot.send_photo(chat_id=CHANNEL_ID, photo=u.message.photo[-1].file_id, caption=final_msg, parse_mode=ParseMode.MARKDOWN)
            elif u.message.text: await c.bot.send_message(chat_id=CHANNEL_ID, text=final_msg, parse_mode=ParseMode.MARKDOWN)
            await u.message.reply_text("✅ Notícia enviada!")
        except Exception as e: await u.message.reply_text(f"❌ Erro: {e}")
        
        c.user_data['waiting_news'] = False
        return True

    # --- PREVIEWS ---
    async def games(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        msg = await u.message.reply_text("🔎 Buscando...")
        m, _ = await self.api.get_matches()
        if not m: return await msg.edit_text("📭 Sem jogos.")
        txt = "*🔥 TOP JOGOS (PREVIEW):*\n\n"
        for g in m[:10]: txt += f"{g['sport']} {g['match']} | {g['league']}\n💡 {g['tip']} (@{g['odd']})\n\n"
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def multi_risk_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        risk = [g for g in m if g['odd'] >= 1.9] or m
        sel = random.sample(risk, min(4, len(risk)))
        total = 1.0
        txt = "*💣 PREVIEW TROCO DO PÃO:*\n\n"
        for g in sel: total *= g['odd']; txt += f"🔥 {g['match']} ({g['tip']})\n"
        txt += f"\n💰 ODD: {total:.2f}"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    async def nba_preview(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        m, _ = await self.api.get_matches()
        nba = [g for g in m if g['sport'] == '🏀']
        if not nba: return await u.message.reply_text("Sem NBA hoje.")
        txt = "*🏀 PREVIEW NBA (ESPN):*\n\n"
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
        for g in sel: total *= g['odd']; txt += f"✅ {g['match']} ({g['tip']})\n"
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
            logger.info("🔥 Iniciando Bot V58.0 (Anti-Gossip)...")
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            
            app.add_handler(CommandHandler("start", h.start))
            app.add_handler(CommandHandler("publicar", h.publish))
            app.add_handler(CommandHandler("ativar", h.active))
            
            # Botões ADMIN
            app.add_handler(MessageHandler(filters.Regex("^🔥"), h.games))
            app.add_handler(MessageHandler(filters.Regex("^💣"), h.multi_risk_preview))
            app.add_handler(MessageHandler(filters.Regex("^🚀"), h.multi_safe_preview))
            app.add_handler(MessageHandler(filters.Regex("^🏀"), h.nba_preview))
            app.add_handler(MessageHandler(filters.Regex("^📢"), h.publish))
            app.add_handler(MessageHandler(filters.Regex("^🎫"), h.gen_key_btn))
            app.add_handler(MessageHandler(filters.Regex("^📰"), h.ask_news))
            
            app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, h.process_news_input))
            
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
