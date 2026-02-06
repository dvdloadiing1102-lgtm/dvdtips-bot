import os
import logging
import asyncio
import feedparser
import httpx
import threading
import unicodedata
from datetime import datetime, timezone, timedelta, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- LOGS ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- CONFIGURAÇÕES E CHAVES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# AS 3 CHAVES (Se alguma faltar, o bot avisa no teste, mas não para)
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")     # Principal (Estaduais/NBA)
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN") # Reserva Agenda
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")     # Reserva Odds

SENT_LINKS = set()

# Times VIP
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "REAL MADRID", "MANCHESTER CITY", 
    "BAYERN", "PSG", "CHELSEA", "LIVERPOOL", "ARSENAL", "BARCELONA", "LAKERS", "CELTICS",
    "WARRIORS", "HEAT", "BUCKS", "SUNS"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVER ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V104 ONLINE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

# --- NOTÍCIAS ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação"]
        blacklist = ["bbb", "festa", "namorada", "traição"]
        c=0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            if any(w in entry.title.lower() for w in whitelist) and not any(b in entry.title.lower() for b in blacklist):
                await context.bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ **BOLETIM**\n\n📰 {entry.title}\n🔗 {entry.link}")
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break
        if len(SENT_LINKS)>500: SENT_LINKS.clear()
    except: pass

# ================= MOTOR V104 (PAINEL DE CONTROLE) =================
class SportsEngine:
    def __init__(self):
        self.headers_as = {"x-apisports-key": API_FOOTBALL_KEY}
        self.headers_fd = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
        self.remaining_requests = 100

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    # --- NOVO: TESTA AS 3 APIS ---
    async def test_all_connections(self):
        report = "📊 **RELATÓRIO DE CONEXÃO**\n\n"

        # 1. API-SPORTS (A Principal)
        if API_FOOTBALL_KEY:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    r = await client.get("https://v3.football.api-sports.io/status", headers=self.headers_as)
                    data = r.json()
                    if "errors" in data and data["errors"]:
                        report += f"❌ **API-Sports:** Erro ({data['errors']})\n"
                    else:
                        curr = data['response']['requests']['current']
                        limit = data['response']['requests']['limit_day']
                        self.remaining_requests = limit - curr
                        report += f"✅ **API-Sports:** Conectada ({self.remaining_requests}/{limit} Restantes)\n"
                except: report += "❌ **API-Sports:** Falha na Conexão\n"
        else:
            report += "⚠️ **API-Sports:** Não Configurada\n"

        # 2. FOOTBALL-DATA (Reserva)
        if FOOTBALL_DATA_TOKEN:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    r = await client.get("https://api.football-data.org/v4/competitions", headers=self.headers_fd)
                    if r.status_code == 200: report += "✅ **Football-Data:** Conectada\n"
                    else: report += f"❌ **Football-Data:** Erro {r.status_code}\n"
                except: report += "❌ **Football-Data:** Falha na Conexão\n"
        else:
            report += "⚠️ **Football-Data:** Não Configurada\n"

        # 3. THE ODDS API (Reserva)
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: report += "✅ **The Odds API:** Conectada\n"
                    else: report += f"❌ **The Odds API:** Erro {r.status_code}\n"
                except: report += "❌ **The Odds API:** Falha na Conexão\n"
        else:
            report += "⚠️ **The Odds API:** Não Configurada\n"

        return report

    async def get_matches(self, mode="soccer"):
        # Segurança da Cota
        if self.remaining_requests < 5: return [], "⛔ Cota crítica. Automação pausada."

        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        url = f"https://{host}/fixtures?date={date_str}&timezone=America/Sao_Paulo"
        if mode == "nba": url += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url, headers=self.headers_as)
                if 'x-ratelimit-requests-remaining' in r.headers:
                    self.remaining_requests = int(r.headers['x-ratelimit-requests-remaining'])
                
                data = r.json()
                if data.get("errors"): return [], f"❌ Erro API: {data['errors']}"
                response_list = data.get("response", [])
            except: return [], "❌ Erro Conexão"
            
            games_list = []
            BLACKLIST = ["ESOCCER", "SIMULATED", "SRL", "BATTLE", "VIRTUAL"]
            
            for item in response_list:
                try:
                    h = item['teams']['home']['name']
                    a = item['teams']['away']['name']
                    fid = item['fixture']['id']
                    league = item['league']['name']
                    if any(b in league.upper() for b in BLACKLIST): continue
                    
                    full = normalize_name(f"{h} {a} {league}")
                    if "WOMEN" in full or "U20" in full: continue

                    time_match = datetime.fromisoformat(item['fixture']['date']).strftime("%H:%M")
                    
                    score = 10
                    if any(v in normalize_name(h) for v in VIP_TEAMS_LIST) or any(v in normalize_name(a) for v in VIP_TEAMS_LIST): score += 5000
                    if "FLAMENGO" in full: score += 10000
                    if "SERIE A" in full: score += 5000
                    if "CARIOCA" in full or "PAULISTA" in full: score += 3000

                    games_list.append({"id": fid, "match": f"{h} x {a}", "league": league, "time": time_match, "score": score, "home": h, "away": a})
                except: continue

            games_list.sort(key=lambda x: x['score'], reverse=True)
            top_games = games_list[:5] # Top 5 Economia

            if not top_games: return [], "⚠️ Nenhum jogo top hoje."

            final_list = []
            for game in top_games:
                odd_val, tip_str = await self._scan_any_bookmaker(client, host, game['id'], game['home'], game['away'])
                final_list.append({
                    "match": game['match'], "league": game['league'], "time": game['time'],
                    "odd": odd_val, "tip": tip_str
                })
            
            return final_list, None

    async def _scan_any_bookmaker(self, client, host, fid, h, a):
        try:
            url = f"https://{host}/odds?fixture={fid}&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers_as)
            if 'x-ratelimit-requests-remaining' in r.headers:
                self.remaining_requests = int(r.headers['x-ratelimit-requests-remaining'])

            data = r.json().get("response", [])
            if not data: return 0.0, "🔒 (S/ Odd)"
            bets = data[0]['bookmakers'][0]['bets']
            if not bets: return 0.0, "🔒 Fechado"

            w = next((b for b in bets if b['id'] == 1), None)
            if w:
                oh = next((float(v['odd']) for v in w['values'] if v['value'] == 'Home'), 0)
                oa = next((float(v['odd']) for v in w['values'] if v['value'] == 'Away'), 0)
                if oh > 0 and oa > 0:
                    if oh < 1.65: return oh, f"✅ {h}"
                    if oa < 1.65: return oa, f"✅ {a}"
                    if oh < 2.5: return oh, f"🛡️ {h} (DC)"

            g = next((b for b in bets if b['id'] == 5), None)
            if g:
                ov = next((float(v['odd']) for v in g['values'] if 'Over' in v['value']), 0)
                if ov > 1: return ov, f"⚽ {g['values'][0]['value']} Gols"

            for b in bets:
                nl = b['name'].lower()
                if "corner" in nl or "escanteio" in nl: return float(b['values'][0]['odd']), f"⛳ {b['name']}"
                if "card" in nl or "cartão" in nl: return float(b['values'][0]['odd']), f"🟨 {b['name']}"

            return float(bets[0]['values'][0]['odd']), "🎲 Aposta"
        except: return 0.0, "Erro Odd"

engine = SportsEngine()

async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except: pass

# --- JOBS AGENDADOS (9H e 18H) ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games, error = await engine.get_matches("soccer")
    if error or not games: return
    msg = f"🌞 **BOM DIA! GRADE V104**\n\n"
    for g in games:
        txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "🚫"
        msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {txt_odd}\n\n"
    msg += f"__________________\n🔋 Cota: {engine.remaining_requests}/100"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games, error = await engine.get_matches("nba")
    if error or not games: return
    msg = f"🏀 **NBA NIGHT - V104**\n\n"
    for g in games:
        txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "🚫"
        msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🎯 {g['tip']} | {txt_odd}\n\n"
    msg += f"__________________\n🔋 Cota: {engine.remaining_requests}/100"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top 5 Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
        [InlineKeyboardButton("🔧 Testar APIs", callback_data="test_api")]
    ]
    await update.message.reply_text("🦁 **PAINEL V104 - DASHBOARD**\nVeja o status das 3 APIs.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "test_api":
        await q.edit_message_text("⏳ Conectando aos 3 satélites...")
        report = await engine.test_all_connections()
        kb = [[InlineKeyboardButton("🔥 Voltar", callback_data="top_jogos")]]
        await q.edit_message_text(report, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    await q.edit_message_text("🔎 Buscando...")
    mode = "nba" if "nba" in data else "soccer"
    games, error = await engine.get_matches(mode)

    if error:
        await q.message.reply_text(error)
        return

    msg = f"🔥 **GRADE MANUAL V104**\n\n"
    if mode == "nba": msg = "🏀 **NBA V104**\n\n"
    
    for g in games:
        txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "🚫"
        msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {txt_odd}\n\n"
    
    msg += f"__________________\n🔋 Cota: {engine.remaining_requests}/100"
    await enviar(context, msg)
    await q.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        # 9h e 18h BRT
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=12, minute=0, tzinfo=timezone.utc))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=21, minute=0, tzinfo=timezone.utc))
        
    app.run_polling()

if __name__ == "__main__":
    main()
