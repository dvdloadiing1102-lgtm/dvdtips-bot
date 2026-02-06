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

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# CHAVES
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

SENT_LINKS = set()
LATEST_HEADLINES = []

TIER_1_LEAGUES = [
    "CHAMPIONS LEAGUE", "LIBERTADORES", "PREMIER LEAGUE", "LA LIGA", 
    "SERIE A", "BUNDESLIGA", "LIGUE 1", "BRASILEIRO SERIE A", 
    "COPA DO BRASIL", "SUDAMERICANA", "PAULISTA", "CARIOCA",
    "MINEIRO", "GAUCHO", "CATARINENSE", "SAUDI", "PRO LEAGUE", "CHAMPIONSHIP"
]

VIP_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO", "GREMIO", "INTERNACIONAL",
    "REAL MADRID", "BARCELONA", "MANCHESTER CITY", "LIVERPOOL", "ARSENAL", "CHELSEA",
    "PSG", "BAYERN", "INTER MIAMI", "AL NASSR", "AL ITTIHAD", "AL HILAL",
    "LAKERS", "CELTICS", "WARRIORS", "HEAT", "BUCKS", "SUNS", "MAVERICKS"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V116 - WEEKEND WARRIOR")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    global LATEST_HEADLINES
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação"]
        blacklist = ["bbb", "festa", "namorada"]
        LATEST_HEADLINES = [entry.title for entry in feed.entries[:20]]
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

# ================= MOTOR V116 =================
class SportsEngine:
    def __init__(self):
        self.headers_as = {"x-apisports-key": API_FOOTBALL_KEY}
        self.remaining_requests = 100

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def test_all_connections(self):
        report = "📊 **STATUS V116**\n\n"
        if API_FOOTBALL_KEY:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    r = await client.get("https://v3.football.api-sports.io/status", headers=self.headers_as)
                    data = r.json()
                    curr = data['response']['requests']['current']
                    limit = data['response']['requests']['limit_day']
                    self.remaining_requests = limit - curr
                    report += f"✅ API-Sports: {self.remaining_requests}/{limit}\n"
                except: report += "❌ API-Sports: Erro\n"
        
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: 
                        rem = r.headers.get("x-requests-remaining", "?")
                        report += f"✅ The Odds API: {rem} créditos\n"
                    else: report += "❌ The Odds API: Erro Key\n"
                except: report += "❌ The Odds API: Erro Conexão\n"
        return report

    async def get_ufc_fights(self):
        if not THE_ODDS_API_KEY: return [], "⚠️ API UFC Off"
        url = f"https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds?regions=us&oddsFormat=decimal&apiKey={THE_ODDS_API_KEY}"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(url)
                data = r.json()
                if not isinstance(data, list): return [], None
                
                fights = []
                now = datetime.now(timezone.utc)
                limit_time = now + timedelta(hours=36)
                
                for event in data[:6]:
                    try:
                        evt_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        if evt_time > limit_time: continue

                        time_str = evt_time.astimezone(timezone(timedelta(hours=-3))).strftime("%d/%m %H:%M")
                        h, a = event['home_team'], event['away_team']
                        odds_h, odds_a = 0, 0
                        
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = o['price']
                                        if o['name'] == a: odds_a = o['price']
                                    break
                            if odds_h > 0: break
                        
                        if odds_h > 0:
                            fights.append({"match": f"{h} x {a}", "time": time_str, "odd_h": odds_h, "odd_a": odds_a, "h": h, "a": a})
                    except: continue
                return fights, None
            except: return [], None

    async def get_matches(self, mode="soccer", limit=7):
        if self.remaining_requests < 5: return [], "⛔ Cota crítica."
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        url = f"https://{host}/fixtures?date={date_str}&timezone=America/Sao_Paulo"
        if mode == "nba": url += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url, headers=self.headers_as)
                if 'x-ratelimit-requests-remaining' in r.headers:
                    self.remaining_requests = int(r.headers['x-ratelimit-requests-remaining'])
                data = r.json().get("response", [])
            except: return [], "❌ Erro Conexão"
            
            games_list = []
            BLACKLIST = ["WOMEN", "FEMININO", "U21", "U20", "SUB-20", "RESERVE", "ESOCCER", "VIRTUAL"]
            for item in data:
                try:
                    h, a = item['teams']['home']['name'], item['teams']['away']['name']
                    full = normalize_name(f"{h} {a} {item['league']['name']}")
                    if any(b in full for b in BLACKLIST): continue
                    
                    score = 0 
                    if any(l in full for l in TIER_1_LEAGUES): score += 50000
                    if any(v in full for v in VIP_TEAMS): score += 20000
                    if mode == "nba": score += 10000
                    
                    has_news = False
                    for news in LATEST_HEADLINES:
                        if normalize_name(h) in normalize_name(news) or normalize_name(a) in normalize_name(news):
                            has_news = True; score += 5000

                    games_list.append({
                        "id": item['fixture']['id'], "match": f"{h} x {a}", "league": item['league']['name'], 
                        "time": datetime.fromisoformat(item['fixture']['date']).strftime("%H:%M"), 
                        "score": score, "home": h, "away": a, "has_news": has_news
                    })
                except: continue

            games_list.sort(key=lambda x: x['score'], reverse=True)
            top_games = games_list[:limit]
            
            final_list = []
            for i, game in enumerate(top_games):
                is_main_event = (i == 0 and mode == "soccer")
                report = await self._analyze_match(client, host, game, is_main_event)
                final_list.append({"match": game['match'], "league": game['league'], "time": game['time'], "report": report})
            return final_list, None

    async def _analyze_match(self, client, host, game, is_main_event):
        try:
            endpoint = "predictions" if is_main_event else "odds"
            url = f"https://{host}/{endpoint}?fixture={game['id']}"
            if not is_main_event: url += "&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers_as)
            data = r.json().get("response", [])
            if not data: return ["🔒 (Sem Dados)"]

            lines = []
            if game['has_news']: lines.append("📰 **Radar:** Notícias recentes detectadas no GE.")

            if is_main_event:
                pred = data[0]['predictions']
                lines.append(f"🧠 **IA:** {pred['advice']}")
                lines.append(f"⚔️ **Provável:** {pred['winner']['name']}")
                lines.append(f"📊 **Chance:** Casa {pred['percent']['home']} | Fora {pred['percent']['away']}")
                return lines

            bets = None
            for book in data[0]['bookmakers']: bets = book['bets']; break
            if not bets: return ["🔒 Fechado"]

            w = next((b for b in bets if b['id'] == 1), None)
            if w:
                oh = next((float(v['odd']) for v in w['values'] if v['value'] == 'Home'), 0)
                oa = next((float(v['odd']) for v in w['values'] if v['value'] == 'Away'), 0)
                if oh > 1 and oh < 1.65: lines.append(f"🟢 **Segura:** {game['home']} (@{oh})")
                elif oa > 1 and oa < 1.65: lines.append(f"🟢 **Segura:** {game['away']} (@{oa})")
                elif oh > 1 and oh < 2.5: lines.append(f"🟢 **Segura:** {game['home']} ou Empate (@{oh})")

            btts = next((b for b in bets if b['id'] == 8), None)
            if btts:
                yo = next((float(v['odd']) for v in btts['values'] if v['value'] == 'Yes'), 0)
                if 1 < yo < 1.95: lines.append(f"🟡 **Valor:** Ambas Marcam (@{yo})")
            
            if not lines: lines.append("🎲 Verificar Odds")
            return lines
        except: return ["⚠️ Erro Análise"]

engine = SportsEngine()

async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except: pass

# --- JOBS AGENDADOS ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games, _ = await engine.get_matches("soccer", limit=7)
    if not games: return
    msg = f"🔥 **DOSSIÊ V116** 🔥\n\n"
    for g in games:
        block = "\n".join(g['report'])
        msg += f"🏆 **{g['league'].upper()}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🔋 Cota: {engine.remaining_requests}/100"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games, _ = await engine.get_matches("nba", limit=3)
    if not games: return
    msg = f"🏀 **NBA PRIME V116** 🏀\n\n"
    for g in games:
        block = "\n".join(g['report'])
        msg += f"🏟 **{g['league'].upper()}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🔋 Cota: {engine.remaining_requests}/100"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

# JOB NOVO: UFC AUTOMÁTICO SÓ SEXTA E SÁBADO
async def daily_ufc_job(context: ContextTypes.DEFAULT_TYPE):
    fights, _ = await engine.get_ufc_fights()
    if not fights: return
    
    msg = "🥊 **UFC FIGHT DAY (V116)** 🥊\n\n"
    for f in fights:
        msg += f"⏰ {f['time']} | ⚔️ **{f['match']}**\n"
        msg += f"👊 {f['h']}: @{f['odd_h']}\n"
        msg += f"👊 {f['a']}: @{f['odd_a']}\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔥 Futebol", callback_data="top_jogos"), InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
          [InlineKeyboardButton("🥊 UFC Manual", callback_data="ufc_fights"), InlineKeyboardButton("🔧 Status", callback_data="test_api")]]
    await update.message.reply_text("🦁 **PAINEL V116 - WEEKEND WARRIOR**\nFiltro UFC Sexta/Sábado Ativado.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = q.data
    if data == "test_api":
        await q.edit_message_text("⏳ Check-up..."); rep = await engine.test_all_connections()
        kb = [[InlineKeyboardButton("Voltar", callback_data="top_jogos")]]
        await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN); return
    if data == "ufc_fights":
        await q.edit_message_text("🥊 Buscando Lutas..."); fights, _ = await engine.get_ufc_fights()
        if not fights: await q.message.reply_text("⚠️ Sem lutas hoje."); return
        msg = "🥊 **UFC MANUAL** 🥊\n\n"
        for f in fights: msg += f"⏰ {f['time']} | ⚔️ **{f['match']}**\n👊 {f['h']}: @{f['odd_h']}\n👊 {f['a']}: @{f['odd_a']}\n━━━━━━━━━━━━━━━━━━━━\n"
        await enviar(context, msg); await q.message.reply_text("✅ Postado!"); return
    
    await q.edit_message_text("🔎 Buscando..."); mode = "nba" if "nba" in data else "soccer"; limit_req = 7 if mode == "soccer" else 3
    games, err = await engine.get_matches(mode, limit=limit_req)
    if err: await q.message.reply_text(err); return
    msg = f"🔥 **GRADE MANUAL V116**\n\n"
    for g in games:
        blk = "\n".join(g['report']); msg += f"🏆 **{g['league'].upper()}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"🔋 Cota: {engine.remaining_requests}/100"
    await enviar(context, msg); await q.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=12, minute=0, tzinfo=timezone.utc))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=21, minute=0, tzinfo=timezone.utc))
        
        # AQUI É O SEGREDO: days=(4, 5) -> 4=Sexta, 5=Sábado
        app.job_queue.run_daily(daily_ufc_job, time=time(hour=15, minute=0, tzinfo=timezone.utc), days=(4, 5))
        
    app.run_polling()

if __name__ == "__main__":
    main()
