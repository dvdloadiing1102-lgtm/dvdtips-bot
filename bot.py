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
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY") # A NOVA CHAVE ESTÁ AQUI
PORT = int(os.getenv("PORT", 10000))

# --- FILTROS ---
BLACKLIST_KEYWORDS = [
    "WOMEN", "FEMININO", "FEM", "(W)", "LADIES", "GIRLS", "MULLER",
    "U19", "U20", "U21", "U23", "U18", "U17", "SUB-20", "SUB 19", "SUB-19",
    "SUB 20", "YOUTH", "JUNIORES", "JUVENIL", "RESERVE", "RES.", "AMATEUR", 
    "REGIONAL", "SRL", "VIRTUAL", "SIMULATED", "ESOCCER", "BATTLE"
]

# Lista VIP (Seus times preferidos ganham prioridade na busca de odds)
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

# ================= SERVER WEB (KEEP ALIVE) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V82 ONLINE")

def run_web_server():
    HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()

# ================= MOTOR API-SPORTS (V82) =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_today_date(self):
        # Garante o fuso horário correto do Brasil (UTC-3)
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        # 1. Busca AGENDA COMPLETA (Gasta 1 requisição)
        url_fixtures = f"https://{host}/fixtures?date={date_str}"
        if mode == "nba": url_fixtures += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url_fixtures, headers=self.headers)
                data = r.json()
                
                # Verifica erros da API (como chave expirada)
                if isinstance(data, dict) and data.get("errors"):
                    logger.error(f"Erro API: {data['errors']}")
                    return []
                    
                all_games = data.get("response", [])
            except Exception as e:
                logger.error(f"Erro conexão: {e}")
                return []
            
            relevant_games = []
            
            for item in all_games:
                try:
                    h_name = item['teams']['home']['name']
                    a_name = item['teams']['away']['name']
                    fixture_id = item['fixture']['id']
                    league_name = item['league']['name']
                    
                    h_norm = normalize_name(h_name)
                    a_norm = normalize_name(a_name)
                    full_name = f"{h_norm} {a_norm} {normalize_name(league_name)}"

                    # Filtro Anti-Lixo
                    if any(bad in full_name for bad in BLACKLIST_KEYWORDS): continue
                    
                    # Sistema de Pontos para Priorizar a Busca de Odds
                    score = 10 # Pontuação base
                    
                    # Se for time VIP (Cruzeiro, Flu, Real Madrid...), prioridade ALTA
                    if any(vip in h_norm for vip in VIP_TEAMS_LIST) or any(vip in a_norm for vip in VIP_TEAMS_LIST):
                        score += 5000
                    
                    # Flamengo sempre no topo
                    if "FLAMENGO" in h_norm or "FLAMENGO" in a_norm: score += 10000
                    
                    if mode == "nba": score += 2000

                    relevant_games.append({
                        "id": fixture_id,
                        "match": f"{h_name} x {a_name}",
                        "league": league_name,
                        "score": score
                    })
                except: continue

            # Ordena pelos mais importantes
            relevant_games.sort(key=lambda x: x['score'], reverse=True)
            
            # Se não achou nada
            if not relevant_games: return []

            # 2. Busca ODDS apenas para os TOP 6 (Para economizar a chave)
            final_list = []
            top_games = relevant_games[:6] 
            
            for game in top_games:
                odd_val, odd_tip = await self._get_odds_for_fixture(client, host, game['id'])
                final_list.append({
                    "match": game['match'],
                    "league": game['league'],
                    "odd": odd_val,
                    "tip": odd_tip
                })
            
            return final_list

    async def _get_odds_for_fixture(self, client, host, fixture_id):
        try:
            # Gasta +1 requisição por jogo
            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6" # Bet365
            r = await client.get(url, headers=self.headers)
            data = r.json().get("response", [])
            
            if data:
                odds = data[0]['bookmakers'][0]['bets'][0]['values']
                fav = sorted(odds, key=lambda x: float(x['odd']))[0]
                return float(fav['odd']), fav['value']
            
            return 0.0, "Aguardando Odd"
        except:
            return 0.0, "Indisponível"

engine = SportsEngine()

# --- ENVIO ---
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
         InlineKeyboardButton("🚀 Múltipla", callback_data="multi_odd")],
        [InlineKeyboardButton("🏥 Notícias (No Fake)", callback_data="news")]
    ]
    await update.message.reply_text("🦁 **PAINEL V82 - CHAVE NOVA**\nBuscando Estaduais e Elite Europeia.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    msg = ""

    # === NOTÍCIAS (V77 - Filtradas) ===
    if data == "news":
        await query.edit_message_text("⏳ Filtrando Notícias...")
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular", "dúvida", "sentiu"]
        blacklist = ["bbb", "festa", "namorada", "traição", "polêmica", "cabelo"]
        
        relevant = []
        for e in feed.entries:
            title_lower = e.title.lower()
            if any(w in title_lower for w in whitelist) and not any(b in title_lower for b in blacklist):
                relevant.append(e)
                
        if not relevant: relevant = feed.entries[:3]
        
        msg = "🏥 **BOLETIM DO MERCADO & DM**\n\n"
        for entry in relevant[:5]:
            msg += f"⚠️ {entry.title}\n🔗 {entry.link}\n\n"
        
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Boletim enviado!")
        return

    # === JOGOS ===
    await query.message.reply_text("🔎 Buscando na API (Modo Econômico)...")
    
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ Nenhum jogo relevante encontrado (ou a Chave Nova falhou). Tente mais tarde.")
        return

    if data == "top_jogos" or data == "nba_hoje":
        emoji = "🏀" if mode == "nba" else "🔥"
        msg = f"{emoji} **GRADE DE HOJE**\n\n"
        for g in games:
            txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "⏳ Aguardando"
            msg += f"🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {txt_odd}\n\n"

    elif data == "troco_pao":
        valid = [g for g in games if g['odd'] > 1.2]
        sel = valid[:3]
        if not sel:
            msg = "❌ Sem odds suficientes para múltipla."
        else:
            total = 1.0
            msg = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"📍 {g['match']} (@{g['odd']})\n"
            msg += f"\n💰 **ODD TOTAL: @{total:.2f}**"

    elif data == "multi_odd":
        valid = [g for g in games if g['odd'] > 1.2]
        sel = valid[:5]
        if len(sel) < 4:
            msg = "❌ Sem jogos suficientes para All-In."
        else:
            total = 1.0
            msg = "🚀 **MÚLTIPLA DE VALOR**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"✅ {g['match']} (@{g['odd']})\n"
            msg += f"\n🤑 **ODD FINAL: @{total:.2f}**"

    if msg:
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Postado!")

# --- MAIN ---
def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    print("✅ Bot V82 - Chave Nova Rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
