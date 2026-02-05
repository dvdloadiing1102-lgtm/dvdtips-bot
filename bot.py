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

# Telegram Imports
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
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000))

SENT_LINKS = set()

VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "REAL MADRID", "MANCHESTER CITY", 
    "BAYERN", "PSG", "CHELSEA", "LIVERPOOL", "ARSENAL", "BARCELONA", "BOCA JUNIORS", "RIVER PLATE"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

#Server
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V90 ONLINE")
def run_web_server(): HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()

#News Job
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular"]
        blacklist = ["bbb", "festa", "namorada", "traição"]
        c=0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            if any(w in entry.title.lower() for w in whitelist) and not any(b in entry.title.lower() for b in blacklist):
                await context.bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ **BOLETIM REAL**\n\n📰 {entry.title}\n🔗 {entry.link}")
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break
        if len(SENT_LINKS)>500: SENT_LINKS.clear()
    except: pass

# ================= MOTOR V90 (VARREDURA COMPLETA) =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        # 1. Busca AGENDA
        url = f"https://{host}/fixtures?date={date_str}&timezone=America/Sao_Paulo"
        if mode == "nba": url += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url, headers=self.headers)
                data = r.json()
                if data.get("errors"): return []
                response_list = data.get("response", [])
            except: return []
            
            games_list = []
            for item in response_list:
                try:
                    h = item['teams']['home']['name']
                    a = item['teams']['away']['name']
                    fid = item['fixture']['id']
                    league = item['league']['name']
                    time = datetime.fromisoformat(item['fixture']['date']).strftime("%H:%M")
                    full = normalize_name(f"{h} {a} {league}")
                    if "WOMEN" in full or "U20" in full: continue
                    
                    score = 10
                    if any(v in normalize_name(h) for v in VIP_TEAMS_LIST) or any(v in normalize_name(a) for v in VIP_TEAMS_LIST): score += 5000
                    if "FLAMENGO" in full: score += 10000
                    if mode == "nba": score += 2000

                    games_list.append({"id": fid, "match": f"{h} x {a}", "league": league, "time": time, "score": score, "home": h, "away": a})
                except: continue

            games_list.sort(key=lambda x: x['score'], reverse=True)
            top_games = games_list[:8] # Top 8 para não estourar

            if not top_games: return []

            final_list = []
            # 2. Busca QUALQUER ODD (Vencedor -> Gols -> Escanteio -> Cartão)
            for game in top_games:
                odd_val, tip_str = await self._get_any_market(client, host, game['id'], game['home'], game['away'])
                
                final_list.append({
                    "match": game['match'], 
                    "league": game['league'], 
                    "time": game['time'],
                    "odd": odd_val, 
                    "tip": tip_str
                })
            
            return final_list

    async def _get_any_market(self, client, host, fid, h, a):
        try:
            url = f"https://{host}/odds?fixture={fid}&bookmaker=6&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers)
            data = r.json().get("response", [])
            
            if not data: return 0.0, "🔒 Aguardando Odd"
            
            bets = data[0]['bookmakers'][0]['bets']
            if not bets: return 0.0, "🔒 Mercado Fechado"

            # --- PRIORIDADE 1: VENCEDOR ---
            w = next((b for b in bets if b['id'] == 1), None)
            if w:
                oh = next((float(v['odd']) for v in w['values'] if v['value'] == 'Home'), 0)
                oa = next((float(v['odd']) for v in w['values'] if v['value'] == 'Away'), 0)
                if oh > 0 and oa > 0:
                    if oh < 1.65: return oh, f"✅ {h} Vence"
                    if oa < 1.65: return oa, f"✅ {a} Vence"

            # --- PRIORIDADE 2: GOLS (Over 1.5 ou 2.5) ---
            g = next((b for b in bets if b['id'] == 5), None)
            if g:
                ov = next((float(v['odd']) for v in g['values'] if 'Over' in v['value']), 0)
                if ov > 1: return ov, f"⚽ {g['values'][0]['value']} Gols"

            # --- PRIORIDADE 3: DUPLA CHANCE ---
            dc = next((b for b in bets if b['id'] == 12), None)
            if dc:
                return float(dc['values'][0]['odd']), f"🛡️ {dc['values'][0]['value']}"

            # --- PRIORIDADE 4: ESCANTEIOS (CORNERS) ---
            # Procura qualquer aposta que tenha 'Corner' no nome
            corners = next((b for b in bets if "Corner" in b['name'] or "Escanteio" in b['name']), None)
            if corners:
                val = corners['values'][0]
                return float(val['odd']), f"⛳ {corners['name']} ({val['value']})"

            # --- PRIORIDADE 5: CARTÕES (CARDS) ---
            cards = next((b for b in bets if "Card" in b['name'] or "Cartão" in b['name']), None)
            if cards:
                val = cards['values'][0]
                return float(val['odd']), f"🟨 {cards['name']} ({val['value']})"

            # --- PRIORIDADE 6: DESESPERO (PEGA A PRIMEIRA DA LISTA) ---
            # Se não achou nada acima, pega literalmente a primeira aposta disponível
            first = bets[0]
            val = first['values'][0]
            return float(val['odd']), f"🎲 {first['name']} ({val['value']})"

        except: 
            return 0.0, "🔒 Indisponível"

engine = SportsEngine()

async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔥 Top Jogos (Scanner)", callback_data="top_jogos"), InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")]]
    await update.message.reply_text("🦁 **PAINEL V90 - SCANNER TOTAL**\nBusca: Vencedor > Gols > Escanteios > Cartões.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    
    await q.message.reply_text("🔎 Varrendo TODOS os mercados...")
    
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await q.message.reply_text("❌ Lista vazia. A API não retornou jogos para hoje.")
        return

    msg = "🔥 **GRADE COMPLETA (V90)**\n\n"
    for g in games:
        txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "⏳ (S/ Odd)"
        msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {txt_odd}\n\n"

    await enviar(context, msg)
    await q.message.reply_text("✅ Postado!")

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
