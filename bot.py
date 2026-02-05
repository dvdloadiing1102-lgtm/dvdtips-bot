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

# --- MEMÓRIA DE NOTÍCIAS (Para não repetir) ---
# O bot guarda os links enviados aqui.
SENT_LINKS = set()

# --- FILTROS ---
BLACKLIST_KEYWORDS = [
    "WOMEN", "FEMININO", "FEM", "(W)", "LADIES", "GIRLS", "MULLER",
    "U19", "U20", "U21", "U23", "U18", "U17", "SUB-20", "SUB 19", "SUB-19",
    "SUB 20", "YOUTH", "JUNIORES", "JUVENIL", "RESERVE", "RES.", "AMATEUR", 
    "REGIONAL", "SRL", "VIRTUAL", "SIMULATED", "ESOCCER", "BATTLE"
]

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
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V85 ONLINE")

def run_web_server():
    HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()

# ================= TAREFA AUTOMÁTICA DE NOTÍCIAS =================
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Esta função roda sozinha a cada 30 minutos.
    """
    logger.info("🤖 Iniciando varredura automática de notícias...")
    
    try:
        # 1. Baixa o RSS sem travar o bot
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular", "sentiu", "dúvida", "cirurgia"]
        blacklist = ["bbb", "festa", "namorada", "traição", "polêmica", "visual", "cabelo", "tatuagem"]
        
        count = 0
        
        # 2. Analisa as notícias
        for entry in feed.entries:
            title_lower = entry.title.lower()
            link = entry.link
            
            # Se já enviamos esse link, pula
            if link in SENT_LINKS:
                continue

            # Aplica Filtros
            is_relevant = any(w in title_lower for w in whitelist)
            is_gossip = any(b in title_lower for b in blacklist)
            
            if is_relevant and not is_gossip:
                # 3. Envia para o Canal
                msg = f"⚠️ **BOLETIM AUTOMÁTICO**\n\n📰 {entry.title}\n🔗 {entry.link}"
                await context.bot.send_message(chat_id=CHANNEL_ID, text=msg)
                
                # Marca como enviada
                SENT_LINKS.add(link)
                count += 1
                
                # Limite de segurança: Manda no máximo 2 notícias por vez para não floodar
                if count >= 2: break
        
        # Limpeza de memória (Opcional, mantém o set leve)
        if len(SENT_LINKS) > 500:
            SENT_LINKS.clear()
            
    except Exception as e:
        logger.error(f"Erro no Auto-News: {e}")

# ================= MOTOR DE JOGOS =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
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
                    raw_date = item['fixture']['date']
                    game_time = datetime.fromisoformat(raw_date).strftime("%H:%M")

                    full_name = normalize_name(f"{h_name} {a_name} {league_name}")
                    if any(bad in full_name for bad in BLACKLIST_KEYWORDS): continue
                    
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
            for game in relevant_games[:6]:
                odd_val, tip_str = await self._get_smart_tip(client, host, game['id'], game['home'], game['away'])
                if odd_val > 1.0:
                    final_list.append({
                        "match": game['match'],
                        "league": game['league'],
                        "time": game['time'],
                        "odd": odd_val,
                        "tip": tip_str
                    })
            return final_list

    async def _get_smart_tip(self, client, host, fixture_id, home_team, away_team):
        try:
            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6&timezone=America/Sao_Paulo"
            r = await client.get(url, headers=self.headers)
            data = r.json().get("response", [])
            
            if not data: return 0.0, "Sem dados"
            bets = data[0]['bookmakers'][0]['bets']
            winner_market = next((b for b in bets if b['id'] == 1), None)
            
            if winner_market:
                values = winner_market['values']
                home_odd = next((float(v['odd']) for v in values if v['value'] == 'Home'), 0.0)
                away_odd = next((float(v['odd']) for v in values if v['value'] == 'Away'), 0.0)
                
                if 1.01 < home_odd < 1.65: return home_odd, f"✅ {home_team} Vence"
                if 1.01 < away_odd < 1.65: return away_odd, f"✅ {away_team} Vence"

                goals_market = next((b for b in bets if b['id'] == 5), None)
                if goals_market:
                    over_25 = next((float(v['odd']) for v in goals_market['values'] if v['value'] == 'Over 2.5'), 0.0)
                    if 1.50 < over_25 < 2.00: return over_25, "⚽ Over 2.5 Gols"
                
                if 1.65 <= home_odd < 2.50: return 1.40, f"🛡️ {home_team} ou Empate"
                
                if home_odd > 0 and away_odd > 0:
                    return (home_odd, f"⚠️ {home_team} (Risco)") if home_odd < away_odd else (away_odd, f"⚠️ {away_team} (Risco)")

            return 0.0, "Aguardando"
        except: return 0.0, "Indisponível"

engine = SportsEngine()

# --- ENVIO MANUAL ---
async def enviar_para_canal(context, text):
    if not CHANNEL_ID: return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except: pass

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("🚀 Múltipla", callback_data="multi_odd")]
    ]
    await update.message.reply_text("🦁 **PAINEL V85 - AUTOMÁTICO**\nNotícias automáticas ativadas (30min).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    msg = ""

    await query.message.reply_text("🔎 Analisando odds e horários...")
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ Nenhum jogo com odds abertas encontrado agora.")
        return

    if data == "top_jogos" or data == "nba_hoje":
        emoji = "🏀" if mode == "nba" else "🔥"
        msg = f"{emoji} **GRADE DE HOJE**\n\n"
        for g in games:
            msg += f"⏰ {g['time']} | 🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | @{g['odd']}\n\n"

    elif data == "troco_pao":
        valid = [g for g in games if g['odd'] > 1.2]
        sel = valid[:3]
        if not sel: msg = "❌ Sem jogos suficientes."
        else:
            total = 1.0
            msg = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"⏰ {g['time']} | 📍 {g['match']} (@{g['odd']})\n"
            msg += f"\n💰 **ODD TOTAL: @{total:.2f}**"

    elif data == "multi_odd":
        valid = [g for g in games if g['odd'] > 1.2]
        sel = valid[:5]
        if len(sel) < 4: msg = "❌ Sem jogos suficientes."
        else:
            total = 1.0
            msg = "🚀 **MÚLTIPLA**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"⏰ {g['time']} | ✅ {g['match']} (@{g['odd']})\n"
            msg += f"\n🤑 **ODD FINAL: @{total:.2f}**"

    if msg:
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # Cria o App
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    
    # === ATIVAÇÃO DO ROBÔ AUTOMÁTICO ===
    # Roda a função auto_news_job a cada 1800 segundos (30 minutos)
    # A primeira execução acontece após 10 segundos que o bot ligar
    job_queue = app.job_queue
    job_queue.run_repeating(auto_news_job, interval=1800, first=10)
    
    print("✅ Bot V85 - Modo Jornalista Automático Ativado...")
    app.run_polling()

if __name__ == "__main__":
    main()
