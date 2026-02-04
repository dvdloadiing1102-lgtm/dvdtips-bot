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

# ================= FILTROS DE ELITE (V51) =================
# IDs das Ligas (Prioridade Absoluta)
# 71=Brasileirão, 39=Premier League, 140=La Liga, 135=Serie A, 12=NBA (Basquete)
VIP_LEAGUES_IDS = [71, 39, 140, 135, 78, 128, 61, 2, 3, 848, 143, 45, 48, 528] 

# Termos PROIBIDOS (Para não pegar Sub-19, Feminino, etc)
BLOCKLIST_TERMS = ["U19", "U20", "U21", "U23", "WOMEN", "FEMININO", "YOUTH", "RESERVES", "LADIES", "JUNIOR"]

# Times VIP (Para garantir que apareçam mesmo em copas)
VIP_TEAMS_NAMES = ["FLAMENGO", "PALMEIRAS", "SAO PAULO", "CORINTHIANS", "SANTOS", "GREMIO", "INTERNACIONAL", "ATLETICO MINEIRO", "BOTAFOGO", "FLUMINENSE", "VASCO", "CRUZEIRO", "BAHIA", "FORTALEZA", "MANCHESTER CITY", "REAL MADRID", "BARCELONA", "LIVERPOOL", "ARSENAL", "PSG", "INTER", "MILAN", "JUVENTUS", "BAYERN", "BOCA JUNIORS", "RIVER PLATE"]

def normalize_str(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVIDOR WEB FAKE =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT V51 ONLINE - ANTI-JUNK FILTER")

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
        try: with self.get_conn() as conn: conn.cursor().execute("DELETE FROM api_cache")
        except: pass

# ================= API DE ESPORTES (FILTRAGEM V51) =================
class SportsAPI:
    def __init__(self, db): self.db = db
    
    async def get_matches(self, force_debug=False):
        if not force_debug:
            cached = await asyncio.to_thread(self.db.get_cache, "top10_matches")
            if cached: return cached, "Cache"
        
        matches = []
        status_msg = "API Oficial"
        # DATA BRASIL (UTC-3) para pegar os jogos da noite corretamente
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
                            # Ignora cancelados
                            if g["fixture"]["status"]["short"] in ["CANC", "ABD", "PST", "FT"]: continue
                            
                            league_id = g["league"]["id"]
                            h_team = normalize_str(g["teams"]["home"]["name"])
                            a_team = normalize_str(g["teams"]["away"]["name"])
                            
                            # --- FILTRO ANTI-JUNK (V51) ---
                            # Se tiver "U19", "WOMEN" no nome, PULA FORA!
                            if any(bad in h_team for bad in BLOCKLIST_TERMS) or any(bad in a_team for bad in BLOCKLIST_TERMS):
                                continue

                            # PONTUAÇÃO DE PRIORIDADE
                            priority_score = 0
                            
                            # É Liga VIP? (Brasileirão, Premier, etc) -> +1000 pontos
                            if league_id in VIP_LEAGUES_IDS: 
                                priority_score += 1000
                            # É Time VIP? (Flamengo, Real Madrid) -> +500 pontos
                            elif any(vip in h_team for vip in VIP_TEAMS_NAMES) or any(vip in a_team for vip in VIP_TEAMS_NAMES): 
                                priority_score += 500
                            
                            # Se não for VIP nem tiver time VIP, ignora (Joga fora o lixo)
                            if priority_score == 0: continue

                            # GERA ODDS E TIPS (Simulado para Free Plan)
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

                # 2. NBA (BUSCA ESPECÍFICA)
                headers_nba = {"x-rapidapi-host": "v1.basketball.api-sports.io", "x-rapidapi-key": API_FOOTBALL_KEY}
                async with httpx.AsyncClient(timeout=15) as client:
                    # ID 12 = NBA
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
                                "score": 2000 # NBA TEM PRIORIDADE MÁXIMA
                            })
                    else:
                        logger.error(f"Erro NBA API: {r_nba.status_code} (Verifique se sua key cobre Basquete)")

            except Exception as e: logger.error(f"Erro API Geral: {e}")

        # BACKUP (Só ativa se não achou NADA)
        if not matches:
            status_msg = "Backup (API Vazia)"
            base_ts = datetime.now().timestamp()
            matches = [
                {"sport": "⚽", "match": "Flamengo x Palmeiras [Sim]", "league": "Brasileirão", "time": "21:30", "odd": 2.10, "tip": "Casa Vence", "ts": base_ts, "score": 200},
                {"sport": "🏀", "match": "Lakers x Celtics [Sim]", "league": "NBA", "time": "22:00", "odd": 1.90, "tip": "Over 220", "ts": base_ts, "score": 300}
            ]

        # ORDENAÇÃO: 1º Score (NBA/VIP), 2º Horário
        matches.sort(key=lambda x: (-x["score"], x["ts"]))
        
        # Pega o Top 15 para ter material para as múltiplas
        top_matches = matches[:15]
        await asyncio.to_thread(self.db.set_cache, "top10_matches", top_matches)
        return top_matches, status_msg

# ================= SISTEMA DE ENVIO P/ CANAL (FORMATO TIPSTER) =================
async def send_channel_report(app, db, api):
    if not CHANNEL_ID: return False, "Sem Channel ID"
    await asyncio.to_thread(db.clear_cache)
    m, source = await api.get_matches(force_debug=True)
    if not m: return False, "Sem jogos"

    today_str = datetime.now().strftime("%d/%m")
    
    # Separa por categoria
    nba_games = [g for g in m if g['sport'] == '🏀']
    foot_games = [g for g in m if g['sport'] == '⚽']
    
    post = f"🦁 **BOLETIM VIP - {today_str}**\n"
    post += f"━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # 1. JOGO DO DIA (O Top 1 do Futebol)
    if foot_games:
        best = foot_games[0]
        post += f"💎 **JOGO DE OURO (MAX)**\n"
        post += f"⚽ {best['match']}\n"
        post += f"🏆 {best['league']} | 🕒 {best['time']}\n"
        post += f"🔥 **Entrada:** {best['tip']}\n"
        post += f"📈 **Odd:** @{best['odd']}\n\n"
    
    # 2. NBA (Se tiver)
    if nba_games:
        post += f"🏀 **SESSÃO NBA**\n"
        for g in nba_games[:2]: # Mostra até 2 da NBA
            post += f"🇺🇸 {g['match']}\n"
            post += f"🎯 {g['tip']} (@{g['odd']})\n\n"

    # 3. LISTA DE FUTEBOL (Resumo)
    post += "📋 **GRADE DE ELITE**\n"
    count = 0
    for g in foot_games[1:]: # Pula o primeiro que já foi destaque
        if count >= 5: break # Max 5 jogos na lista
        post += f"⚔️ {g['match']}\n"
        post += f"   ↳ {g['tip']} (@{g['odd']})\n"
        count += 1
        
    post += "\n━━━━━━━━━━━━━━━━━━━━\n"
    post += f"💣 **TROCO DO PÃO (RISCO)**\n"
    
    # Monta uma múltipla fake de risco
    risk_odd = round(random.uniform(15.0, 25.0), 2)
    post += f"Combinação Secreta gerada pelo Bot.\n"
    post += f"💰 **Odd Total:** @{risk_odd}\n\n"
    
    post += f"⚠️ _Gestão de banca sempre!_ 🦁"

    try:
        await app.bot.send_message(chat_id=CHANNEL_ID, text=post, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return True, "Sucesso"
    except Exception as e: return False, str(e)

# ================= HANDLERS =================
class Handlers:
    def __init__(self, db, api): self.db, self.api = db, api
    
    def is_admin(self, uid): return str(uid) == str(ADMIN_ID)

    async def start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        uid = u.effective_user.id
        if not self.is_admin(uid):
            msg = (f"👋 **Bem-vindo ao DVD TIPS**\n\n"
                   f"⛔ Este bot é apenas para validação.\n"
                   f"O conteúdo VIP é enviado no nosso Canal Oficial.\n\n"
                   f"Se você adquiriu acesso, digite:\n"
                   f"`/ativar SUA-CHAVE`")
            return await u.message.reply_text(msg, reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.MARKDOWN)

        msg = f"🦁 **PAINEL DO DONO**\nCanal: `{CHANNEL_ID}`"
        kb = ReplyKeyboardMarkup([["📢 Publicar no Canal", "🎫 Gerar Key"], ["🔍 Debug API"]], resize_keyboard=True)
        await u.message.reply_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

    async def publish(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        msg = await u.message.reply_text("⏳ Gerando Boletim...")
        ok, info = await send_channel_report(c.application, self.db, self.api)
        await msg.edit_text("✅ Enviado!" if ok else f"❌ Erro: {info}")

    async def gen_key_btn(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        k = await asyncio.to_thread(self.db.create_key, (datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"))
        await u.message.reply_text(f"🔑 **NOVA CHAVE:**\n`{k}`", parse_mode=ParseMode.MARKDOWN)

    async def debug(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self.is_admin(u.effective_user.id): return
        await u.message.reply_text("🔎 Buscando...")
        await asyncio.to_thread(self.db.clear_cache)
        m, status = await self.api.get_matches(force_debug=True)
        
        report = f"📊 Status: {status}\nJogos: {len(m)}\n\n"
        for g in m:
            report += f"{g['sport']} {g['match']} (Score: {g['score']})\n"
            
        await u.message.reply_text(report[:4000]) # Limite Telegram

    async def active(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        try: 
            key_input = c.args[0]
            success = await asyncio.to_thread(self.db.use_key, key_input, u.effective_user.id)
            if success:
                invite_link = "Erro"
                try:
                    if CHANNEL_ID:
                        link = await c.bot.create_chat_invite_link(chat_id=CHANNEL_ID, member_limit=1, expire_date=datetime.now() + timedelta(hours=24))
                        invite_link = link.invite_link
                except: invite_link = "(Peça o link ao suporte)"

                msg = (f"✅ **VIP ATIVADO!**\n\n"
                       f"Entre no canal agora:\n👉 {invite_link}\n\n"
                       f"⚠️ _Link válido por 24h._")
                await u.message.reply_text(msg)
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
            logger.info("🔥 Iniciando Bot V51.0...")
            app = ApplicationBuilder().token(BOT_TOKEN).build()
            
            app.add_handler(CommandHandler("start", h.start))
            app.add_handler(CommandHandler("publicar", h.publish))
            app.add_handler(CommandHandler("ativar", h.active))
            app.add_handler(CommandHandler("debug", h.debug))
            
            app.add_handler(MessageHandler(filters.Regex("^📢"), h.publish))
            app.add_handler(MessageHandler(filters.Regex("^🎫"), h.gen_key_btn))
            app.add_handler(MessageHandler(filters.Regex("^🔍"), h.debug))
            
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
