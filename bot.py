import os
import logging
import asyncio
import feedparser
import httpx
import threading
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

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY") 
PORT = int(os.getenv("PORT", 10000))

# --- LISTA DE LIGAS REAIS PARA BUSCAR ---
# A The Odds API exige que a gente busque liga por liga.
# Infelizmente ela não cobre Estaduais (Carioca/Paulista) bem, mas cobre o resto do mundo.
TARGET_LEAGUES = [
    "basketball_nba",               # NBA
    "soccer_uefa_champs_league",    # Champions League
    "soccer_epl",                   # Premier League (Inglaterra)
    "soccer_spain_la_liga",         # La Liga (Espanha)
    "soccer_italy_serie_a",         # Serie A (Itália)
    "soccer_germany_bundesliga",    # Bundesliga (Alemanha)
    "soccer_france_ligue_one",      # Ligue 1 (França)
    "soccer_conmebol_libertadores", # Libertadores
    "soccer_brazil_campeonato_brasileiro_serie_a" # Brasileirão (Se tiver jogos)
]

# --- SERVER WEB ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V81 - REAL DATA ONLINE")

def run_web_server():
    HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()

# ================= MOTOR DE DADOS REAIS =================
class RealDataEngine:
    def __init__(self):
        self.base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    async def get_matches(self, mode="soccer"):
        """
        Busca APENAS dados reais. Se falhar, retorna vazio.
        Nada de simulação.
        """
        if not THE_ODDS_API_KEY:
            return []

        all_matches = []
        
        # Define quais ligas buscar baseado no modo
        if mode == "nba":
            leagues_to_check = ["basketball_nba"]
        else:
            # Filtra apenas ligas de futebol da lista
            leagues_to_check = [l for l in TARGET_LEAGUES if "soccer" in l]

        async with httpx.AsyncClient(timeout=10) as client:
            # Para cada liga, faz uma busca (Limitado a 3 ligas por vez para não ser lento)
            for league in leagues_to_check:
                try:
                    params = {
                        "apiKey": THE_ODDS_API_KEY,
                        "regions": "br,uk,eu",
                        "markets": "h2h",
                        "oddsFormat": "decimal"
                    }
                    r = await client.get(self.base_url.format(sport=league), params=params)
                    data = r.json()

                    # Verifica se a API retornou erro de cota ou chave
                    if isinstance(data, dict) and data.get("message"):
                        logger.error(f"Erro na liga {league}: {data['message']}")
                        continue

                    if not data: continue

                    # Processa os jogos encontrados
                    for event in data:
                        # Filtra jogos que já começaram (The Odds API as vezes manda live)
                        commence_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        if commence_time < datetime.now(timezone.utc): continue # Ignora jogos passados

                        home = event['home_team']
                        away = event['away_team']
                        
                        # Busca Odds
                        odds = []
                        bookie_name = "Bet"
                        for b in event['bookmakers']:
                            for m in b['markets']:
                                if m['key'] == 'h2h':
                                    odds = [o['price'] for o in m['outcomes']]
                                    bookie_name = b['title']
                                    break
                            if odds: break
                        
                        if not odds: continue
                        
                        # Lógica simples de Tip (Favorito)
                        best_odd = max(odds)
                        fav_odd = min(odds)
                        
                        tip = "Jogo Equilibrado"
                        if fav_odd < 1.50: tip = "Super Favorito"
                        elif fav_odd < 1.90: tip = "Favorito Vence"

                        # Formata o nome da liga para ficar bonito
                        league_display = league.replace("soccer_", "").replace("basketball_", "").replace("_", " ").title()

                        all_matches.append({
                            "match": f"{home} x {away}",
                            "odd": fav_odd, # Mostra a odd do favorito para segurança
                            "league": league_display,
                            "tip": tip,
                            "bookie": bookie_name
                        })

                except Exception as e:
                    logger.error(f"Erro ao buscar {league}: {e}")

        # Retorna os jogos encontrados
        return all_matches[:10] # Top 10 jogos reais

engine = RealDataEngine()

# --- HANDLERS ---
async def enviar_para_canal(context, text):
    if not CHANNEL_ID: return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except Exception as e:
        logger.error(f"Erro envio: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Top Jogos (Europa)", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Múltipla Real", callback_data="multi_odd"),
         InlineKeyboardButton("🏥 Notícias GE", callback_data="news")]
    ]
    await update.message.reply_text("🦁 **PAINEL V81 - DADOS REAIS**\nConectado à The Odds API (Europa/NBA).", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # === NOTÍCIAS (GE - Real) ===
    if data == "news":
        await query.edit_message_text("⏳ Buscando notícias no GE...")
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "titular"]
        relevant = [e for e in feed.entries if any(w in e.title.lower() for w in whitelist)]
        if not relevant: relevant = feed.entries[:3]
        
        msg = "🏥 **BOLETIM REAL (GE)**\n\n"
        for entry in relevant[:5]:
            msg += f"⚠️ {entry.title}\n🔗 {entry.link}\n\n"
        
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Notícias enviadas!")
        return

    # === JOGOS (The Odds API - Real) ===
    await query.message.reply_text("🔎 Varrendo ligas internacionais...")
    
    mode = "nba" if "nba" in data else "soccer"
    games = await engine.get_matches(mode)

    if not games:
        await query.message.reply_text("❌ Nenhum jogo encontrado nas ligas monitoradas (Europa/NBA) para hoje/amanhã.\n\n⚠️ Obs: Esta API não cobre Estaduais do Brasil.")
        return

    if data == "top_jogos" or data == "nba_hoje":
        emoji = "🏀" if mode == "nba" else "🔥"
        msg = f"{emoji} **GRADE REAL ({len(games)} Jogos)**\n\n"
        for g in games:
            msg += f"🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | @{g['odd']} ({g['bookie']})\n\n"

    elif data == "multi_odd":
        if len(games) < 4:
            msg = "❌ Poucos jogos reais encontrados para montar múltipla."
        else:
            sel = games[:4]
            total = 1.0
            msg = "🚀 **MÚLTIPLA REAL (EUROPA/NBA)**\n\n"
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
    print("✅ Bot V81 - Real Data Rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
