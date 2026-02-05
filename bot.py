import os
import logging
import asyncio
import feedparser
import httpx
import random
import threading
import unicodedata
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

# Telegram Imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIGURAÇÃO DE LOGS ---
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
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
PORT = int(os.getenv("PORT", 10000))

# --- FILTRO NEGRO (O QUE É LIXO É DESCARTADO) ---
BLACKLIST_KEYWORDS = [
    "WOMEN", "FEMININO", "FEM", "(W)", "LADIES", "GIRLS", "MULLER",
    "U19", "U20", "U21", "U23", "U18", "U17", "SUB-20", "SUB 19", "SUB-19",
    "SUB 20", "YOUTH", "JUNIORES", "JUVENIL", "RESERVE", "RES.", "AMATEUR", 
    "REGIONAL", "SRL", "VIRTUAL", "SIMULATED", "ESOCCER", "BATTLE"
]

# --- LISTA VIP (APENAS PARA DAR PRIORIDADE, NÃO PARA EXCLUIR) ---
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

# IDs DE LIGAS IMPORTANTES (PARA GARANTIR QUE ENTREM)
# 475: Carioca | 476: Paulista | 143: Copa do Rei | 39: Premier League | 140: La Liga | 2: Champions
IMPORTANT_LEAGUES = [475, 476, 143, 39, 140, 2, 13, 61, 71, 135, 78]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVER WEB =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V78 ONLINE")

def run_web_server():
    server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
    server.serve_forever()

# ================= MOTOR DE BUSCA (V78 - PORTEIRA ABERTA) =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        # Busca Agenda COMPLETA do Dia
        url_fixtures = f"https://{host}/fixtures?date={date_str}"
        if mode == "nba": url_fixtures += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                r = await client.get(url_fixtures, headers=self.headers)
                all_games = r.json().get("response", [])
            except: return []
            
            relevant_games = []
            
            for item in all_games:
                try:
                    h_name = item['teams']['home']['name']
                    a_name = item['teams']['away']['name']
                    fixture_id = item['fixture']['id']
                    league_name = item['league']['name']
                    league_id = item['league'].get('id', 0)
                    
                    h_norm = normalize_name(h_name)
                    a_norm = normalize_name(a_name)
                    full_name = f"{h_norm} {a_norm} {normalize_name(league_name)}"

                    # 1. FILTRO DE LIXO (Se for feminino ou sub-20, tchau)
                    if any(bad in full_name for bad in BLACKLIST_KEYWORDS): continue
                    
                    # 2. SISTEMA DE PONTOS (Para ordenar, não para excluir)
                    score = 100 # Todo jogo profissional ganha 100 pontos (antes era 0 e era excluído)
                    
                    # Se for time VIP, ganha muito ponto
                    if any(vip in h_norm for vip in VIP_TEAMS_LIST) or any(vip in a_norm for vip in VIP_TEAMS_LIST):
                        score += 5000
                    
                    # Se for Flamengo, prioridade máxima
                    if "FLAMENGO" in h_norm or "FLAMENGO" in a_norm: score += 10000
                    
                    # Se for Liga Importante, ganha ponto
                    if league_id in IMPORTANT_LEAGUES: score += 2000
                    
                    if mode == "nba": score += 500

                    # ADICIONA TUDO (Antes tinha filtro aqui, agora passa tudo que não é lixo)
                    relevant_games.append({
                        "id": fixture_id,
                        "match": f"{h_name} x {a_name}",
                        "league": league_name,
                        "score": score
                    })
                except: continue

            # Ordena: Os VIPs ficam no topo, o resto vem embaixo
            relevant_games.sort(key=lambda x: x['score'], reverse=True)
            
            # Pega os Top 10 para buscar odds
            final_list = []
            if not relevant_games: return []

            # Busca odds apenas para os 10 primeiros da fila
            for game in relevant_games[:10]:
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
            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6"
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

# --- FUNÇÃO DE ENVIO ---
async def enviar_para_canal(context, text):
    if not CHANNEL_ID: return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except Exception as e:
        logger.error(f"Erro canal: {e}")

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("🦁 All In", callback_data="all_in")],
        [InlineKeyboardButton("🚀 Múltipla", callback_data="multi_odd"),
         InlineKeyboardButton("🏥 Notícias", callback_data="news")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🦁 **PAINEL V78 - PORTEIRA ABERTA**\nBuscando todos os jogos disponíveis.", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    msg = ""

    # === NOTÍCIAS (Mantido V77) ===
    if data == "news":
        await query.edit_message_text("⏳ Buscando notícias...")
        # (Lógica simplificada de RSS aqui para focar no erro dos jogos)
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        msg = "🏥 **NOTÍCIAS DO MUNDO DA BOLA**\n\n"
        for entry in feed.entries[:5]:
            msg += f"⚠️ {entry.title}\n🔗 {entry.link}\n\n"
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Notícias enviadas!")
        return

    # === JOGOS (Lógica V78) ===
    await query.message.reply_text("🔎 Varrendo a agenda completa...")
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ CRÍTICO: A API não retornou NENHUM jogo. Verifique se sua KEY expirou no dashboard da API-Sports.")
        return

    if data == "top_jogos" or data == "nba_hoje":
        emoji = "🏀" if mode == "nba" else "🔥"
        msg = f"{emoji} **GRADE DE HOJE**\n\n"
        for g in games:
            txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "⏳ Aguardando"
            msg += f"🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {txt_odd}\n\n"

    elif data == "troco_pao":
        # Pega jogos com odd válida > 1.20
        valid = [g for g in games if g['odd'] > 1.20]
        sel = valid[:3]
        if not sel: 
            msg = "❌ Sem jogos com odds disponíveis para múltipla agora."
        else:
            total = 1.0
            msg = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"📍 {g['match']} (@{g['odd']})\n"
            msg += f"\n💰 **ODD TOTAL: @{total:.2f}**"

    elif data == "all_in":
        g = games[0]
        txt_odd = f"@{g['odd']}" if g['odd'] > 0 else "⏳ Aguardando"
        msg = "🦁 **ALL IN SUPREMO**\n\n"
        msg += f"⚔️ {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']}\n📈 Odd: {txt_odd}\n🔥 Confiança: **MÁXIMA**"

    elif data == "multi_odd":
        valid = [g for g in games if g['odd'] > 1.20]
        sel = valid[:5]
        if not sel:
            msg = "❌ Sem jogos suficientes para múltipla longa."
        else:
            total = 1.0
            msg = "🚀 **MÚLTIPLA DE VALOR**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"✅ {g['match']} (@{g['odd']})\n"
            msg += f"\n🤑 **ODD FINAL: @{total:.2f}**"

    if msg:
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Postado no canal!")

# --- MAIN ---
def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    print("✅ Bot V78 - Porteira Aberta Rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
