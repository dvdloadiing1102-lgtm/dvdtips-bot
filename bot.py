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
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# --- VARIÁVEIS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
PORT = int(os.getenv("PORT", 10000))

# --- MEMÓRIA DE NOTÍCIAS ---
SENT_LINKS = set()

# --- LISTA VIP (Para dar destaque com ⭐) ---
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "JUVENTUDE", "CUIABA", 
    "GOIAS", "ATLETICO GO", "AMERICA MG", "REAL MADRID", "MANCHESTER CITY", 
    "BAYERN", "INTER DE MILAO", "PSG", "CHELSEA", "ATLETICO DE MADRID", 
    "BORUSSIA DORTMUND", "BENFICA", "JUVENTUS", "PORTO", "ARSENAL", "BARCELONA", 
    "LIVERPOOL", "MILAN", "NAPOLI", "ROMA", "BOCA JUNIORS", "RIVER PLATE", 
    "AL HILAL", "AL AHLY", "MONTERREY", "LAFC", "LEVERKUSEN", "SPORTING",
    "SEVILLA", "WEST HAM", "FEYENOORD", "RB LEIPZIG", "PSV", "REAL BETIS", "BILBAO"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVER WEB =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V87 ONLINE")

def run_web_server():
    HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()

# ================= JOB NOTÍCIAS (AUTOMÁTICO) =================
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular", "sentiu", "dúvida", "cirurgia"]
        blacklist = ["bbb", "festa", "namorada", "traição", "polêmica", "visual", "cabelo"]
        
        count = 0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            
            title_lower = entry.title.lower()
            if any(w in title_lower for w in whitelist) and not any(b in title_lower for b in blacklist):
                msg = f"⚠️ **BOLETIM AUTOMÁTICO**\n\n📰 {entry.title}\n🔗 {entry.link}"
                await context.bot.send_message(chat_id=CHANNEL_ID, text=msg)
                SENT_LINKS.add(entry.link)
                count += 1
                if count >= 2: break
                
        if len(SENT_LINKS) > 500: SENT_LINKS.clear()
    except Exception as e:
        logger.error(f"Erro News: {e}")

# ================= MOTOR DE JOGOS V87 (FAREJADOR) =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        # 1. Pega TODOS os jogos do dia (Fuso BR)
        url_fixtures = f"https://{host}/fixtures?date={date_str}&timezone=America/Sao_Paulo"
        if mode == "nba": url_fixtures += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url_fixtures, headers=self.headers)
                data = r.json()
                if isinstance(data, dict) and data.get("errors"): return []
                all_games = data.get("response", [])
            except: return []
            
            relevant_games = []
            
            for item in all_games:
                try:
                    h_name = item['teams']['home']['name']
                    a_name = item['teams']['away']['name']
                    fixture_id = item['fixture']['id']
                    league_name = item['league']['name']
                    
                    # Extrai Horário
                    raw_date = item['fixture']['date']
                    game_time = datetime.fromisoformat(raw_date).strftime("%H:%M")

                    # Filtro de Lixo (Feminino, Sub-20, etc)
                    full_name = normalize_name(f"{h_name} {a_name} {league_name}")
                    if "WOMEN" in full_name or "U20" in full_name or "VIRTUAL" in full_name: continue
                    
                    # Score (Todo jogo começa valendo 10 pontos)
                    score = 10 
                    if any(vip in normalize_name(h_name) for vip in VIP_TEAMS_LIST) or any(vip in normalize_name(a_name) for vip in VIP_TEAMS_LIST):
                        score += 5000
                    if "FLAMENGO" in normalize_name(h_name) or "FLAMENGO" in normalize_name(a_name): score += 10000
                    if mode == "nba": score += 2000

                    relevant_games.append({
                        "id": fixture_id,
                        "match": f"{h_name} x {a_name}",
                        "league": league_name,
                        "time": game_time,
                        "score": score,
                        "home": h_name,
                        "away": a_name
                    })
                except: continue

            relevant_games.sort(key=lambda x: x['score'], reverse=True)
            if not relevant_games: return []

            final_list = []
            # Busca Odds para os TOP 8 jogos
            for game in relevant_games[:8]:
                # AQUI ESTÁ A MÁGICA: Pega qualquer odd
                odd_val, tip_str = await self._get_any_odd(client, host, game['id'], game['home'], game['away'])
                
                # Se achou odd, adiciona
                if odd_val > 1.0:
                    final_list.append({
                        "match": game['match'],
                        "league": game['league'],
                        "time": game['time'],
                        "odd": odd_val,
                        "tip": tip_str
                    })
            return final_list

    async def _get_any_odd(self, client, host, fixture_id, home, away):
        try:
            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers)
            data = r.json().get("response", [])
            
            if not data: return 0.0, "Sem dados"
            
            bets = data[0]['bookmakers'][0]['bets']
            if not bets: return 0.0, "Fechado"

            # 1. Tenta achar Vencedor (ID 1)
            winner = next((b for b in bets if b['id'] == 1), None)
            if winner:
                vals = winner['values']
                odd_h = next((float(v['odd']) for v in vals if v['value'] == 'Home'), 0)
                odd_a = next((float(v['odd']) for v in vals if v['value'] == 'Away'), 0)
                
                if odd_h > 0 and odd_a > 0:
                    if odd_h < 1.60: return odd_h, f"✅ {home} Vence"
                    if odd_a < 1.60: return odd_a, f"✅ {away} Vence"
                    return odd_h, f"🛡️ {home} (Dupla Chance)"

            # 2. Se não tem Vencedor, pega Gols (ID 5)
            goals = next((b for b in bets if b['id'] == 5), None)
            if goals:
                val = next((float(v['odd']) for v in goals['values'] if v['value'] == 'Over 2.5'), 0)
                if val > 1: return val, "⚽ Over 2.5 Gols"

            # 3. ÚLTIMO RECURSO: Pega a PRIMEIRA odd que existir na lista
            first_bet = bets[0]['values'][0]
            return float(first_bet['odd']), f"📊 {first_bet['value']}"

        except: return 0.0, "Erro"

engine = SportsEngine()

# --- HANDLERS ---
async def enviar_manual(context, text):
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("🚀 Múltipla", callback_data="multi_odd")]
    ]
    await update.message.reply_text("🦁 **PAINEL V87 - FAREJADOR**\nCorreção de Odds aplicada.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    await query.message.reply_text("🔎 Buscando odds (Modo V87)...")
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ A API não retornou jogos com odds abertas. Tente mais tarde.")
        return

    emoji = "🏀" if mode == "nba" else "🔥"
    msg = ""

    if data == "top_jogos" or data == "nba_hoje":
        msg = f"{emoji} **GRADE DE HOJE**\n\n"
        for g in games:
            # Se for time VIP, coloca uma estrela
            icon = "⭐" if g['score'] > 1000 else "🏟"
            if "FLAMENGO" in g['match'].upper(): icon = "🔴⚫"
            
            msg += f"⏰ {g['time']} | {icon} {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | @{g['odd']}\n\n"

    elif data == "troco_pao":
        sel = [g for g in games if g['odd'] > 1.2][:3]
        if not sel: msg = "❌ Jogos insuficientes."
        else:
            total = 1.0
            msg = "💣 **TROCO DO PÃO**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"⏰ {g['time']} | 📍 {g['match']} (@{g['odd']})\n"
            msg += f"\n💰 **ODD TOTAL: @{total:.2f}**"
            
    elif data == "multi_odd":
        sel = [g for g in games if g['odd'] > 1.2][:5]
        if not sel: msg = "❌ Jogos insuficientes."
        else:
            total = 1.0
            msg = "🚀 **MÚLTIPLA**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"✅ {g['match']} (@{g['odd']})\n"
            msg += f"\n🤑 **ODD FINAL: @{total:.2f}**"

    if msg:
        await enviar_manual(context, msg)
        await query.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    
    # JOB QUEUE ATIVADO
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        print("✅ Auto-News Ativado")

    app.run_polling()

if __name__ == "__main__":
    main()
