import os
import logging
import asyncio
import feedparser
import httpx
import threading
import unicodedata
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- LOGS ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- VARIÁVEIS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# APIs
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

SENT_LINKS = set()
VIP_TEAMS_LIST = ["FLAMENGO", "PALMEIRAS", "REAL MADRID", "MANCHESTER CITY", "LAKERS", "CELTICS"]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVER WEB ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V94 ONLINE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

# --- NEWS JOB ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação"]
        blacklist = ["bbb", "festa", "namorada"]
        c = 0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            if any(w in entry.title.lower() for w in whitelist) and not any(b in entry.title.lower() for b in blacklist):
                await context.bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ **BOLETIM**\n\n📰 {entry.title}\n🔗 {entry.link}")
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break
        if len(SENT_LINKS)>500: SENT_LINKS.clear()
    except: pass

# ================= MOTOR V94 (DEBUG TOTAL) =================
class SportsEngine:
    def __init__(self):
        self.football_data_url = "https://api.football-data.org/v4"
        self.football_data_token = FOOTBALL_DATA_TOKEN
        self.theodds_url = "https://api.the-odds-api.com/v4"
        self.theodds_key = THE_ODDS_API_KEY

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_odds(self, home, away):
        """Busca odds genéricas de futebol na The Odds API"""
        if not self.theodds_key: return 0.0, 0.0
        try:
            # Tenta Premier League como base (é onde tem mais odds geralmente)
            url = f"{self.theodds_url}/sports/soccer_epl/odds"
            params = {"apiKey": self.theodds_key, "regions": "eu", "markets": "h2h"}
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(url, params=params)
                if r.status_code == 200:
                    data = r.json()
                    for e in data:
                        h_api = normalize_name(e['home_team'])
                        a_api = normalize_name(e['away_team'])
                        if (normalize_name(home) in h_api) or (normalize_name(away) in a_api):
                            try:
                                outcomes = e['bookmakers'][0]['markets'][0]['outcomes']
                                odd_h = next((x['price'] for x in outcomes if x['name'] == e['home_team']), 0)
                                odd_a = next((x['price'] for x in outcomes if x['name'] == e['away_team']), 0)
                                return odd_h, odd_a
                            except: return 0.0, 0.0
        except: pass
        return 0.0, 0.0

    async def get_matches(self):
        if not self.football_data_token:
            return [], "❌ Erro: FOOTBALL_DATA_TOKEN não configurado no Render."

        today = self.get_today_date()
        
        # V94: Pede TUDO (/matches), sem filtrar liga específica
        url = f"{self.football_data_url}/matches?dateFrom={today}&dateTo={today}"
        headers = {"X-Auth-Token": self.football_data_token}

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url, headers=headers)
                
                # DIAGNÓSTICO DE ERRO
                if r.status_code == 403:
                    return [], "❌ Erro 403: Chave Football-Data inválida ou sem permissão."
                if r.status_code == 429:
                    return [], "❌ Erro 429: Você fez muitas requisições. Espere um pouco."
                if r.status_code != 200:
                    return [], f"❌ Erro API: Código {r.status_code}"

                data = r.json()
                matches = data.get("matches", [])

                if not matches:
                    return [], f"⚠️ A API respondeu OK, mas disse que há 0 jogos agendados para {today} (Fuso BR)."

                # Processa os jogos encontrados
                games_list = []
                for m in matches:
                    if m['status'] not in ["SCHEDULED", "TIMED", "IN_PLAY"]: continue
                    
                    h = m['homeTeam']['name']
                    a = m['awayTeam']['name']
                    league = m['competition']['name']
                    
                    # Converte Hora
                    dt = datetime.fromisoformat(m['utcDate'].replace("Z", "+00:00"))
                    time_br = dt.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
                    
                    # Score simples
                    score = 10
                    if "Champions" in league or "Premier" in league or "Brasileir" in league: score += 1000
                    
                    games_list.append({
                        "match": f"{h} x {a}", "league": league, "time": time_br,
                        "home": h, "away": a, "score": score
                    })

                # Ordena
                games_list.sort(key=lambda x: x['score'], reverse=True)
                return games_list[:10], None # Retorna Top 10

            except Exception as e:
                return [], f"❌ Erro de Conexão: {e}"

engine = SportsEngine()

async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔥 Buscar Jogos (Debug)", callback_data="top_jogos")]]
    await update.message.reply_text("🦁 **PAINEL V94 - MODO DEBUG**\nVamos descobrir o problema.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    # PASSO 1: Feedback visual
    await q.edit_message_text(f"⏳ 1/3: Conectando à Football-Data.org ({engine.get_today_date()})...")
    
    games, error_msg = await engine.get_matches()

    # SE TIVER ERRO, MOSTRA NA TELA
    if error_msg:
        await q.edit_message_text(error_msg)
        # Se for lista vazia, avisa que pode ser limitação da API
        if "0 jogos" in error_msg:
            await q.message.reply_text("💡 Dica: A Football-Data Free **NÃO TEM** Estaduais do Brasil. Se você quer Carioca/Paulista, essa API não serve.")
        return

    # PASSO 2: Feedback de Odds
    await q.edit_message_text(f"⏳ 2/3: Encontrados {len(games)} jogos. Buscando odds...")
    
    msg = "🔥 **GRADE V94 (RESULTADO)**\n\n"
    for g in games:
        # Busca odd (simples)
        h_odd, a_odd = await engine.get_odds(g['home'], g['away'])
        odd_txt = f"Casa: @{h_odd:.2f}" if h_odd > 0 else "🚫 S/Odd"
        
        msg += f"⏰ {g['time']} | {g['match']}\n🏆 {g['league']}\n💰 {odd_txt}\n\n"

    # PASSO 3: Envio
    await q.edit_message_text("✅ 3/3: Enviando para o canal...")
    await enviar(context, msg)
    await q.message.reply_text("✅ Postado! Verifique o canal.")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    if app.job_queue: app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
    app.run_polling()

if __name__ == "__main__":
    main()
