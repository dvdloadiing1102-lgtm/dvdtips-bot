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
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

SENT_LINKS = set()

TIER_1_LEAGUES = [
    "CHAMPIONS LEAGUE", "LIBERTADORES", "PREMIER LEAGUE", "LA LIGA", 
    "SERIE A", "BUNDESLIGA", "LIGUE 1", "BRASILEIRO SERIE A", 
    "COPA DO BRASIL", "SUDAMERICANA", "PAULISTA", "CARIOCA"
]

VIP_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO",
    "REAL MADRID", "BARCELONA", "MANCHESTER CITY", "LIVERPOOL", "ARSENAL",
    "PSG", "BAYERN", "INTER MIAMI", "AL NASSR", "LAKERS", "CELTICS", "WARRIORS", "HEAT", "BUCKS", "SUNS"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVER ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V110 - DESIGNER MODE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

# --- NOTÍCIAS ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação"]
        blacklist = ["bbb", "festa", "namorada"]
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

# ================= MOTOR V110 (LAYOUT PREMIUM) =================
class SportsEngine:
    def __init__(self):
        self.headers_as = {"x-apisports-key": API_FOOTBALL_KEY}
        self.remaining_requests = 100

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def test_all_connections(self):
        report = "📊 **STATUS V110**\n\n"
        if API_FOOTBALL_KEY:
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    r = await client.get("https://v3.football.api-sports.io/status", headers=self.headers_as)
                    data = r.json()
                    if "errors" in data and data["errors"]: report += f"❌ API-Sports: Erro\n"
                    else:
                        curr = data['response']['requests']['current']
                        limit = data['response']['requests']['limit_day']
                        self.remaining_requests = limit - curr
                        report += f"✅ API-Sports: OK ({self.remaining_requests}/{limit})\n"
                except: report += "❌ API-Sports: Falha\n"
        else: report += "⚠️ API-Sports: N/A\n"
        return report

    async def get_matches(self, mode="soccer", limit=5):
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
                    if any(l in full for l in TIER_1_LEAGUES): score += 20000
                    if any(v in full for v in VIP_TEAMS): score += 5000
                    if mode == "nba": score += 2000

                    games_list.append({"id": fid, "match": f"{h} x {a}", "league": league, "time": time_match, "score": score, "home": h, "away": a})
                except: continue

            games_list.sort(key=lambda x: x['score'], reverse=True)
            top_games = games_list[:limit]

            if not top_games: return [], "⚠️ Nenhum jogo top hoje."

            final_list = []
            for game in top_games:
                report = await self._analyze_full_package(client, host, game['id'], game['home'], game['away'], mode)
                final_list.append({
                    "match": game['match'], "league": game['league'], "time": game['time'],
                    "report": report
                })
            
            return final_list, None

    async def _analyze_full_package(self, client, host, fid, h, a, mode):
        try:
            url = f"https://{host}/odds?fixture={fid}&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers_as)
            if 'x-ratelimit-requests-remaining' in r.headers:
                self.remaining_requests = int(r.headers['x-ratelimit-requests-remaining'])

            data = r.json().get("response", [])
            if not data: return ["🔒 (Sem Odds)"]
            
            bets = None
            for book in data[0]['bookmakers']:
                bets = book['bets']
                break
            if not bets: return ["🔒 Fechado"]

            lines = []

            # ================= FUTEBOL =================
            if mode == "soccer":
                # 1. SEGURANÇA (Verde)
                w = next((b for b in bets if b['id'] == 1), None)
                if w:
                    oh = next((float(v['odd']) for v in w['values'] if v['value'] == 'Home'), 0)
                    oa = next((float(v['odd']) for v in w['values'] if v['value'] == 'Away'), 0)
                    if oh > 1 and oh < 1.65: lines.append(f"🟢 **Segura:** {h} Vence (@{oh})")
                    elif oa > 1 and oa < 1.65: lines.append(f"🟢 **Segura:** {a} Vence (@{oa})")
                    elif oh > 1 and oh < 2.5: lines.append(f"🟢 **Segura:** {h} ou Empate (@{oh})")

                # 2. VALOR (Amarelo)
                btts = next((b for b in bets if b['id'] == 8), None)
                if btts:
                    yes_odd = next((float(v['odd']) for v in btts['values'] if v['value'] == 'Yes'), 0)
                    if yes_odd > 1 and yes_odd < 1.95: lines.append(f"🟡 **Valor:** Ambas Marcam (@{yes_odd})")
                
                if len(lines) < 2:
                    g = next((b for b in bets if b['id'] == 5), None)
                    if g:
                        ov = next((float(v['odd']) for v in g['values'] if 'Over 2.5' in v['value']), 0)
                        if ov > 1.5: lines.append(f"🟡 **Valor:** +2.5 Gols (@{ov})")

                # 3. OUSADA (Vermelho)
                found_bold = False
                for b in bets:
                    if "scorer" in b['name'].lower() or "marcar" in b['name'].lower():
                        vals = sorted(b['values'], key=lambda x: float(x['odd']))
                        best = vals[0]
                        if float(best['odd']) < 3.2:
                            lines.append(f"🔴 **Ousada:** {best['value']} Marca (@{best['odd']})")
                            found_bold = True
                            break
                if not found_bold:
                    cs = next((b for b in bets if b['id'] == 10), None)
                    if cs:
                        vals = sorted(cs['values'], key=lambda x: float(x['odd']))
                        lines.append(f"🔴 **Fezinha:** Placar {vals[0]['value']} (@{vals[0]['odd']})")

            # ================= NBA =================
            elif mode == "nba":
                # 1. SEGURANÇA
                w = next((b for b in bets if b['id'] == 1), None)
                if w:
                    oh = next((float(v['odd']) for v in w['values'] if v['value'] == 'Home'), 0)
                    oa = next((float(v['odd']) for v in w['values'] if v['value'] == 'Away'), 0)
                    if oh < oa: lines.append(f"🟢 **Segura:** {h} Vence (@{oh})")
                    else: lines.append(f"🟢 **Segura:** {a} Vence (@{oa})")
                
                # 2. VALOR
                totals = next((b for b in bets if b['id'] == 5), None)
                if totals:
                     ov = next((v for v in totals['values'] if 'Over' in v['value']), None)
                     if ov: lines.append(f"🟡 **Valor:** Total {ov['value']} (@{ov['odd']})")

                # 3. OUSADA
                for b in bets:
                    if "points" in b['name'].lower() and "player" in b['name'].lower():
                        stars = ["LeBron", "Curry", "Tatum", "Doncic", "Giannis", "Jokic", "Durant", "Davis"]
                        for val in b['values']:
                            if "Over" in val['value'] and any(s in val['value'] for s in stars):
                                lines.append(f"🔴 **Player:** {val['value']} Pts (@{val['odd']})")
                                if len(lines) >= 3: break
                        if len(lines) >= 3: break

            if not lines: lines.append("🎲 Verificar Odds no Site")
            return lines

        except Exception as e: return [f"⚠️ Erro: {e}"]

engine = SportsEngine()

async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except: pass

async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games, error = await engine.get_matches("soccer", limit=7)
    if error or not games: return
    
    msg = f"🔥 **DOSSIÊ DE ELITE** 🔥\n\n"
    for g in games:
        block = "\n".join(g['report'])
        # LAYOUT NOVO AQUI
        msg += f"🏆 **{g['league'].upper()}** • ⏰ {g['time']}\n"
        msg += f"⚔️ **{g['match']}**\n\n"
        msg += f"{block}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        
    msg += f"🔋 Cota Restante: {engine.remaining_requests}/100"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games, error = await engine.get_matches("nba", limit=3)
    if error or not games: return
    
    msg = f"🏀 **NBA PRIME** 🏀\n\n"
    for g in games:
        block = "\n".join(g['report'])
        # LAYOUT NOVO AQUI
        msg += f"🏟 **{g['league'].upper()}** • ⏰ {g['time']}\n"
        msg += f"⚔️ **{g['match']}**\n\n"
        msg += f"{block}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"

    msg += f"🔋 Cota Restante: {engine.remaining_requests}/100"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top 7 (Futebol)", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA (Prime)", callback_data="nba_hoje")],
        [InlineKeyboardButton("🔧 Testar APIs", callback_data="test_api")]
    ]
    await update.message.reply_text("🦁 **PAINEL V110 - DESIGNER**\nNovo layout ativado.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "test_api":
        await q.edit_message_text("⏳ Diagnóstico...")
        report = await engine.test_all_connections()
        kb = [[InlineKeyboardButton("🔥 Voltar", callback_data="top_jogos")]]
        await q.edit_message_text(report, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    await q.edit_message_text("🔎 Caprichando no visual...")
    mode = "nba" if "nba" in data else "soccer"
    games, error = await engine.get_matches(mode, limit=7)

    if error:
        await q.message.reply_text(error)
        return

    msg = f"🔥 **GRADE MANUAL V110**\n\n"
    if mode == "nba": msg = "🏀 **NBA PRIME V110**\n\n"
    
    for g in games:
        block = "\n".join(g['report'])
        # LAYOUT NOVO AQUI
        msg += f"🏆 **{g['league'].upper()}** • ⏰ {g['time']}\n"
        msg += f"⚔️ **{g['match']}**\n\n"
        msg += f"{block}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    
    msg += f"🔋 Cota: {engine.remaining_requests}/100"
    await enviar(context, msg)
    await q.message.reply_text("✅ Postado com estilo!")

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
        
    app.run_polling()

if __name__ == "__main__":
    main()
