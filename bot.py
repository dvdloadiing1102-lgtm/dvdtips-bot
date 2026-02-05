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

# --- LISTAS DE FILTROS DE NOTÍCIAS (O SEGREDO ESTÁ AQUI) ---

# 1. A notícia TEM que ter uma dessas palavras para entrar
NEWS_WHITELIST = [
    "lesão", "lesionado", "machucou", "sentiu", "dores", "vetado", "fora do jogo",
    "desfalque", "cirurgia", "dm", "departamento médico", "fratura", "rompeu",
    "contratado", "reforço", "assina", "vendido", "emprestado", "transferência", "mercado",
    "banco", "reserva", "poupado", "titular", "escalação", "suspenso", "cartão",
    "rescinde", "demitido", "técnico", "multa"
]

# 2. Se a notícia tiver uma dessas, ela é LIXO (Fofoca)
NEWS_BLACKLIST = [
    "namorada", "esposa", "filho", "festa", "polêmica", "traição", "bbb",
    "reality", "visual", "cabelo", "tatuagem", "carro", "mansão", "luxo",
    "viraliza", "meme", "affair", "separação", "casamento", "influencer",
    "dança", "tiktok", "homenagem", "aniversário", "bastidores da festa"
]

# Feeds de Notícias (GE e ESPN para garantir volume de notícias sérias)
RSS_FEEDS = [
    "https://ge.globo.com/rss/ge/",
    "https://www.espn.com.br/espn/rss/news"
]

# --- FILTROS DE JOGOS E TIMES ---
BLACKLIST_TEAMS = [
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
    "SEVILLA", "WEST HAM", "FEYENOORD", "RB LEIPZIG", "PSV", "CHAPECOENSE", 
    "CORITIBA", "REAL BETIS", "CAPIVARIANO"
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVER WEB =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V77 ONLINE")

def run_web_server():
    server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
    server.serve_forever()

# ================= MOTOR DE BUSCA =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        if THE_ODDS_API_KEY:
            try:
                sport_key = "soccer_uefa_champs_league" if mode == "soccer" else "basketball_nba"
                data = await self._fetch_the_odds(sport_key)
                if data: return {"type": "premium", "data": data}
            except: pass 

        data = await self._fetch_from_fixtures(mode)
        return {"type": "standard", "data": data}

    async def _fetch_from_fixtures(self, mode):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        url_fixtures = f"https://{host}/fixtures?date={date_str}"
        if mode == "nba": url_fixtures += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=25) as client:
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
                    
                    h_norm = normalize_name(h_name)
                    a_norm = normalize_name(a_name)
                    full_name = f"{h_norm} {a_norm} {normalize_name(league_name)}"

                    if any(bad in full_name for bad in BLACKLIST_TEAMS): continue
                    
                    score = 0
                    if any(vip in h_norm for vip in VIP_TEAMS_LIST) or any(vip in a_norm for vip in VIP_TEAMS_LIST):
                        score += 5000
                    if "FLAMENGO" in h_norm or "FLAMENGO" in a_norm: score += 2000
                    
                    if score > 0:
                        relevant_games.append({
                            "id": fixture_id,
                            "match": f"{h_name} x {a_name}",
                            "league": league_name,
                            "score": score
                        })
                except: continue

            relevant_games.sort(key=lambda x: x['score'], reverse=True)
            final_list = []
            
            for game in relevant_games[:8]:
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
            return 0.0, "Aguardando"
        except:
            return 0.0, "Indisponível"

    async def _fetch_the_odds(self, sport_key):
        params = {"apiKey": THE_ODDS_API_KEY, "regions": "br,uk,eu", "markets": "h2h", "oddsFormat": "decimal"}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self.odds_base_url.format(sport=sport_key), params=params)
            data = r.json()
            if not data or (isinstance(data, dict) and data.get("errors")): return None
            results = []
            for event in data[:6]:
                home, away = event['home_team'], event['away_team']
                full = normalize_name(f"{home} {away}")
                if any(bad in full for bad in BLACKLIST_TEAMS): continue
                all_h = []
                for b in event['bookmakers']:
                    for m in b['markets']:
                        for o in m['outcomes']:
                            if o['name'] == home: all_h.append({"p": o['price'], "b": b['title']})
                if not all_h: continue
                best = max(all_h, key=lambda x: x['p'])
                results.append({"match": f"{home} x {away}", "odd": best['p'], "tip": "Melhor Odd", "league": "🏆 Lucro"})
            return results

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
         InlineKeyboardButton("🏥 Lesões & Mercado", callback_data="news")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🦁 **PAINEL V77 - NO GOSSIP**\nSistema de Notícias Filtradas.", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    msg = ""

    # === LÓGICA DE NOTÍCIAS FILTRADAS (V77) ===
    if data == "news":
        await query.edit_message_text("⏳ Analisando notícias (Filtrando fofocas)...")
        
        def fetch_filtered_news():
            all_entries = []
            # Busca em Múltiplas Fontes (GE e ESPN)
            for feed_url in RSS_FEEDS:
                parsed = feedparser.parse(feed_url)
                all_entries.extend(parsed.entries)
            
            clean_news = []
            for entry in all_entries:
                title = entry.title.lower()
                
                # 1. Verifica se tem palavra IMPORTANTE (Lesão, Contratação, etc)
                is_relevant = any(word in title for word in NEWS_WHITELIST)
                
                # 2. Verifica se tem palavra PROIBIDA (Fofoca)
                is_gossip = any(bad in title for bad in NEWS_BLACKLIST)
                
                if is_relevant and not is_gossip:
                    clean_news.append(entry)
            
            return clean_news[:5] # Retorna as top 5 limpas

        news_list = await asyncio.get_running_loop().run_in_executor(None, fetch_filtered_news)
        
        if not news_list:
            await query.edit_message_text("❌ Nenhuma notícia relevante sobre lesões ou mercado no momento.")
            return

        msg = "🏥 **BOLETIM DE LESÕES E MERCADO**\n\n"
        for entry in news_list:
            msg += f"⚠️ {entry.title}\n🔗 {entry.link}\n\n"
        
        await enviar_para_canal(context, msg)
        await query.message.reply_text("✅ Boletim enviado ao canal!")
        return

    # === LÓGICA DE JOGOS ===
    await query.message.reply_text("🔎 Buscando dados reais...")
    mode = "nba" if "nba" in data else "soccer"
    result = await engine.get_matches(mode)
    games = result["data"]

    if not games:
        await query.message.reply_text("❌ Nenhum jogo relevante encontrado na agenda.")
        return

    if data == "top_jogos" or data == "nba_hoje":
        emoji = "🏀" if mode == "nba" else "🔥"
        msg = f"{emoji} **GRADE DE ELITE**\n\n"
        for g in games:
            msg += f"🏟 {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | @{g['odd']}\n\n"

    elif data == "troco_pao":
        valid = [g for g in games if g['odd'] > 1.0]
        if len(valid) < 3:
            msg = "❌ Poucos jogos com odds."
        else:
            sel = valid[:3]
            total = 1.0
            msg = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
            for g in sel:
                total *= g['odd']
                msg += f"📍 {g['match']} (@{g['odd']})\n"
            msg += f"\n💰 **ODD TOTAL: @{total:.2f}**"

    elif data == "all_in":
        g = games[0]
        msg = "🦁 **ALL IN SUPREMO**\n\n"
        msg += f"⚔️ {g['match']}\n🎯 {g['tip']}\n📈 Odd: @{g['odd']}\n🔥 Confiança: **MÁXIMA**"

    elif data == "multi_odd":
        valid = [g for g in games if g['odd'] > 1.0]
        sel = valid[:5]
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
    print("✅ Bot V77 - Anti-Fofoca Rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
