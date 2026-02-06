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

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY") # Sua chave nova aqui!
PORT = int(os.getenv("PORT", 10000))

SENT_LINKS = set()

# Times VIP (Para ordenar a lista)
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "REAL MADRID", "MANCHESTER CITY", 
    "BAYERN", "PSG", "CHELSEA", "LIVERPOOL", "ARSENAL", "BARCELONA", "LAKERS", "CELTICS"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVER (MANTÉM O BOT ONLINE) ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V98 ONLINE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

# --- NOTÍCIAS AUTOMÁTICAS ---
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

# ================= MOTOR V98 (API-SPORTS + TESTE) =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    # --- FUNÇÃO DE TESTE DA API ---
    async def test_connection(self):
        url = "https://v3.football.api-sports.io/status"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(url, headers=self.headers)
                data = r.json()
                if "errors" in data and data["errors"]:
                    return f"❌ Erro: {data['errors']}"
                
                # Pega informações da conta
                account = data.get("response", {}).get("account", {})
                reqs = data.get("response", {}).get("requests", {})
                
                name = account.get("firstname", "Usuário")
                current = reqs.get("current", 0)
                limit = reqs.get("limit_day", 100)
                
                return f"✅ **API Conectada!**\n👤 Conta: {name}\n📊 Uso Hoje: {current}/{limit} requisições."
            except Exception as e:
                return f"❌ Erro de Conexão: {e}"

    # --- BUSCA DE JOGOS ---
    async def get_matches(self, mode="soccer"):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        url = f"https://{host}/fixtures?date={date_str}&timezone=America/Sao_Paulo"
        if mode == "nba": url += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url, headers=self.headers)
                data = r.json()
                
                if data.get("errors"): return [], f"❌ Erro API: {data['errors']}"
                response_list = data.get("response", [])
            except Exception as e:
                return [], f"❌ Erro Conexão: {e}"
            
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
            top_games = games_list[:10]

            if not top_games: return [], "⚠️ API conectada, mas sem jogos principais hoje."

            final_list = []
            # Busca Odds (Scanner)
            for game in top_games:
                odd_val, tip_str = await self._scan_markets(client, host, game['id'], game['home'], game['away'])
                final_list.append({
                    "match": game['match'], "league": game['league'], "time": game['time'],
                    "odd": odd_val, "tip": tip_str
                })
            
            return final_list, None

    # --- SCANNER DE MERCADOS ---
    async def _scan_markets(self, client, host, fid, h, a):
        try:
            url = f"https://{host}/odds?fixture={fid}&bookmaker=6&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers)
            data = r.json().get("response", [])
            
            if not data: return 0.0, "🔒 (S/ Odd)"
            bets = data[0]['bookmakers'][0]['bets']
            if not bets: return 0.0, "🔒 Fechado"

            # 1. VENCEDOR
            w = next((b for b in bets if b['id'] == 1), None)
            if w:
                oh = next((float(v['odd']) for v in w['values'] if v['value'] == 'Home'), 0)
                oa = next((float(v['odd']) for v in w['values'] if v['value'] == 'Away'), 0)
                if oh > 0 and oa > 0:
                    if oh < 1.65: return oh, f"✅ {h} Vence"
                    if oa < 1.65: return oa, f"✅ {a} Vence"
                    if oh < 2.5: return oh, f"🛡️ {h} (DC)"

            # 2. GOLS
            g = next((b for b in bets if b['id'] == 5), None)
            if g:
                ov = next((float(v['odd']) for v in g['values'] if 'Over' in v['value']), 0)
                if ov > 1: return ov, f"⚽ {g['values'][0]['value']} Gols"

            # 3. SCANNER DE TEXTO (CANTOS/CARTÕES)
            for b in bets:
                nl = b['name'].lower()
                if "corner" in nl or "escanteio" in nl:
                    return float(b['values'][0]['odd']), f"⛳ {b['name']}"
                if "card" in nl or "cartão" in nl:
                    return float(b['values'][0]['odd']), f"🟨 {b['name']}"

            return float(bets[0]['values'][0]['odd']), "🎲 Aposta"

        except: return 0.0, "Erro Odd"

engine = SportsEngine()

# --- HANDLERS ---
async def enviar(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top Jogos (Canal)", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
        [InlineKeyboardButton("🔧 Testar API", callback_data="test_api")]
    ]
    await update.message.reply_text("🦁 **PAINEL V98 - COMPLETO**\nSelecione uma opção:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # --- BOTÃO DE TESTE ---
    if data == "test_api":
        await q.edit_message_text("⏳ Testando chave nova...")
        status = await engine.test_connection()
        
        # Recria os botões para você não ficar preso
        kb = [
            [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
             InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
             [InlineKeyboardButton("🔧 Testar Novamente", callback_data="test_api")]
        ]
        await q.edit_message_text(status, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    # --- BOTÕES DE JOGOS ---
    await q.edit_message_text("🔎 Analisando agenda e mercados...")
    mode = "nba" if "nba" in data else "soccer"
    games, error = await engine.get_matches(mode)

    if error:
        await q.message.reply_text(f"{error}\n\nVerifique o Environment no Render.")
        return

    msg = f"🔥 **GRADE DE JOGOS V98**\n\n"
    if mode == "nba": msg = "🏀 **NBA V98**\n\n"
    
    for g in games:
        txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "🚫"
        msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {txt_odd}\n\n"

    await enviar(context, msg)
    await q.message.reply_text("✅ Enviado para o Canal!")

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
