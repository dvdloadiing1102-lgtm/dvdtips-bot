import os
import asyncio
import logging
import random
import httpx
import threading
import unicodedata
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY") # Sua Key da API-Sports
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= FILTRO ANTI-LIXO =================
BLACKLIST_KEYWORDS = [
    "WOMEN", "FEMININO", "FEM", "(W)", "LADIES", "GIRLS", "MULLER",
    "U19", "U20", "U21", "U23", "U18", "U17", "SUB-20", "SUB 19", "SUB-19",
    "SUB 20", "YOUTH", "JUNIORES", "JUVENIL", "RESERVE", "RES.", "AMATEUR", 
    "REGIONAL", "SRL", "VIRTUAL", "SIMULATED", "ESOCCER", "BATTLE"
]

# ================= LISTA VIP (SEUS TIMES) =================
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
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V75 - CAÇADOR DE JOGOS ONLINE")

# ================= MOTOR INTELIGENTE (FIXTURES FIRST) =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    def get_today_date(self):
        # Data de HOJE no Brasil
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        # 1. Tenta The Odds API primeiro (Scanner de Valor)
        if THE_ODDS_API_KEY:
            try:
                sport_key = "soccer_uefa_champs_league" if mode == "soccer" else "basketball_nba"
                data = await self._fetch_the_odds(sport_key)
                if data: return {"type": "premium", "data": data}
            except: pass 

        # 2. Backup Robusto: Busca na API-Sports pelo MÉTODO FIXTURES (Agenda)
        data = await self._fetch_from_fixtures(mode)
        return {"type": "standard", "data": data}

    async def _fetch_from_fixtures(self, mode):
        """Busca primeiro a agenda, depois as odds. Garante que o jogo apareça."""
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        date_str = self.get_today_date()
        
        # Passo 1: Buscar TODOS os jogos do dia
        url_fixtures = f"https://{host}/fixtures?date={date_str}"
        if mode == "nba": url_fixtures += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(url_fixtures, headers=self.headers)
            all_games = r.json().get("response", [])
            
            relevant_games = []
            
            # Passo 2: Filtrar apenas os VIPs na memória
            for item in all_games:
                h_name = item['teams']['home']['name']
                a_name = item['teams']['away']['name']
                fixture_id = item['fixture']['id']
                league_name = item['league']['name']
                
                h_norm = normalize_name(h_name)
                a_norm = normalize_name(a_name)
                full_name = f"{h_norm} {a_norm} {normalize_name(league_name)}"

                # Filtro Anti-Lixo
                if any(bad in full_name for bad in BLACKLIST_KEYWORDS): continue
                
                # Verifica se é time VIP
                score = 0
                if any(vip in h_norm for vip in VIP_TEAMS_LIST) or any(vip in a_norm for vip in VIP_TEAMS_LIST):
                    score += 5000
                if "FLAMENGO" in h_norm or "FLAMENGO" in a_norm: score += 2000
                
                if score > 0:
                    relevant_games.append({
                        "id": fixture_id,
                        "match": f"{h_name} x {a_name}",
                        "league": league_name,
                        "score": score,
                        "home": h_name,
                        "away": a_name
                    })

            # Ordena e pega os top 8 para buscar odds
            relevant_games.sort(key=lambda x: x['score'], reverse=True)
            final_list = []
            
            # Passo 3: Buscar Odds APENAS para esses jogos escolhidos
            # Isso economiza requisições e garante que pegamos a odd certa
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
        """Busca odd específica de um jogo."""
        try:
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

    async def _fetch_the_odds(self, sport_key):
        # (Código The Odds API mantido igual para scanner de lucro)
        params = {"apiKey": THE_ODDS_API_KEY, "regions": "br,uk,eu", "markets": "h2h", "oddsFormat": "decimal"}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self.odds_base_url.format(sport=sport_key), params=params)
            data = r.json()
            if not data or (isinstance(data, dict) and data.get("errors")): return None
            results = []
            for event in data[:6]:
                home, away = event['home_team'], event['away_team']
                full = normalize_name(f"{home} {away}")
                if any(bad in full for bad in BLACKLIST_KEYWORDS): continue
                all_h = []
                for b in event['bookmakers']:
                    for m in b['markets']:
                        for o in m['outcomes']:
                            if o['name'] == home: all_h.append({"p": o['price'], "b": b['title']})
                if not all_h: continue
                best = max(all_h, key=lambda x: x['p'])
                worst = min(all_h, key=lambda x: x['p'])
                profit = (best['p'] - worst['p']) * 100
                results.append({"match": f"{home} x {away}", "odd": best['p'], "book": best['b'], "profit": round(profit, 2), "league": "🏆 Lucro"})
            return results

engine = SportsEngine()

# ================= HANDLERS =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [["🔥 Top Jogos", "🏀 NBA"], ["💣 Troco do Pão", "✍️ Mensagem Livre"]]
    await u.message.reply_text("🦁 **PAINEL V75 - CAÇADOR DE JOGOS**\nAgora buscando pela agenda oficial do dia.", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def handle_request(u: Update, c, mode="soccer", is_multi=False):
    msg = await u.message.reply_text(f"🔎 Varrendo a agenda de jogos...")
    
    api_mode = "nba" if mode == "nba" else "soccer"
    result = await engine.get_matches(api_mode)
    data = result["data"]
    
    if not data:
        return await msg.edit_text("❌ Não encontrei jogos dos seus times VIP na agenda de hoje.")

    if is_multi:
        # Filtra jogos que têm odds válidas
        valid_games = [g for g in data if g['odd'] > 1.0]
        if len(valid_games) < 2: return await msg.edit_text("❌ Poucos jogos com odds para múltipla.")
        
        sel = random.sample(valid_games, min(5, len(valid_games)))
        odd_t = 1.0
        txt = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
        for g in sel:
            odd_t *= g['odd']
            txt += f"📍 {g['match']} (@{g['odd']})\n"
        txt += f"\n💰 **ODD TOTAL: @{odd_t:.2f}**"
    
    elif result["type"] == "premium":
        txt = f"🏆 **SCANNER DE VALOR**\n\n"
        for g in data:
            txt += f"⚔️ {g['match']}\n⭐ Odd: @{g['odd']} ({g['book']})\n💰 Lucro: +R$ {g['profit']}\n\n"
            
    else:
        txt = f"{'🏀' if mode=='nba' else '🔥'} **GRADE DE ELITE (V75)**\n\n"
        for g in data:
            icon = "🔴⚫" if "FLAMENGO" in normalize_name(g['match']) else "⭐"
            odd_txt = f"@{g['odd']}" if g['odd'] > 0 else "⏳ Aguardando"
            txt += f"{icon} {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {odd_txt}\n\n"

    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="send")]]
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    await msg.delete()

async def handle_free_text(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    if any(k in u.message.text for k in ["Top", "NBA", "Troco", "Livre"]): return
    kb = [[InlineKeyboardButton("📤 Enviar para o Canal", callback_data="send")]]
    await u.message.reply_text(f"📝 **PRÉVIA:**\n\n{u.message.text}", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u: Update, c):
    q = u.callback_query
    await q.answer()
    if q.data == "send":
        txt = q.message.text.replace("📝 PRÉVIA:\n\n", "")
        await c.bot.send_message(chat_id=CHANNEL_ID, text=txt)
        await q.edit_message_text(txt + "\n\n✅ **POSTADO!**")

# ================= MAIN =================
async def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever(), daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("Top Jogos"), lambda u,c: handle_request(u,c,"soccer")))
    app.add_handler(MessageHandler(filters.Regex("NBA"), lambda u,c: handle_request(u,c,"nba")))
    app.add_handler(MessageHandler(filters.Regex("Troco do Pão"), lambda u,c: handle_request(u,c,"soccer", True)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
    app.add_handler(CallbackQueryHandler(callback_handler))

    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.initialize(); await app.start(); await app.updater.start_polling()
    while True: await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
