import os
import asyncio
import logging
import random
import httpx
import threading
import unicodedata
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= LISTA NEGRA (FILTRO ANTI-LIXO) =================
# Se tiver qualquer uma dessas palavras no nome do time ou da liga, o bot DESCARTA.
BLACKLIST_KEYWORDS = [
    "WOMEN", "FEMININO", "FEM", "(W)", "LADIES", "GIRLS", "MULLER",
    "U19", "U20", "U21", "U23", "U18", "U17", "SUB-20", "SUB 19", "SUB-19",
    "SUB 20", "YOUTH", "JUNIORES", "JUVENIL", "RESERVE", "RES.", "AMATEUR", 
    "REGIONAL", "SRL", "VIRTUAL", "SIMULATED", "ESOCCER", "BATTLE"
]

# ================= LISTA VIP DE TIMES =================
VIP_TEAMS_LIST = [
    "FLAMENGO", "PALMEIRAS", "BOTAFOGO", "FLUMINENSE", "SAO PAULO", "CORINTHIANS",
    "VASCO", "CRUZEIRO", "ATLETICO MINEIRO", "INTERNACIONAL", "GREMIO", "BAHIA",
    "FORTALEZA", "ATHLETICO", "SANTOS", "BRAGANTINO", "JUVENTUDE", "CUIABA", 
    "GOIAS", "ATLETICO GO", "AMERICA MG", "REAL MADRID", "MANCHESTER CITY", 
    "BAYERN", "INTER DE MILAO", "PSG", "CHELSEA", "ATLETICO DE MADRID", 
    "BORUSSIA DORTMUND", "BENFICA", "JUVENTUS", "PORTO", "ARSENAL", "BARCELONA", 
    "LIVERPOOL", "MILAN", "NAPOLI", "ROMA", "BOCA JUNIORS", "RIVER PLATE", 
    "AL HILAL", "AL AHLY", "MONTERREY", "LAFC", "LEVERKUSEN", "SPORTING",
    "SEVILLA", "WEST HAM", "FEYENOORD", "RB LEIPZIG", "PSV"
]

# IDs: 71(BR), 475(Carioca), 2(Champions), 39(Premier), 140(LaLiga), 13(Liberta)
VIP_LEAGUES_IDS = [71, 475, 2, 39, 140, 13, 135, 78, 61, 3, 848, 15, 11, 4]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# ================= SERVER WEB =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V73 - FILTRO LIMPO")

# ================= MOTOR DE ODDS =================
class SportsEngine:
    def __init__(self):
        self.apisports_headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    async def get_matches(self, mode="soccer"):
        # 1. The Odds API
        if THE_ODDS_API_KEY:
            try:
                sport_key = "soccer_uefa_champs_league" if mode == "soccer" else "basketball_nba"
                data = await self._fetch_the_odds(sport_key)
                if data: return {"type": "premium", "data": data}
            except: pass 
        
        # 2. API-Sports
        data = await self._fetch_api_sports(mode)
        return {"type": "standard", "data": data}

    async def _fetch_the_odds(self, sport_key):
        params = {"apiKey": THE_ODDS_API_KEY, "regions": "br,uk,eu", "markets": "h2h", "oddsFormat": "decimal"}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(self.odds_base_url.format(sport=sport_key), params=params)
            data = r.json()
            if not data or (isinstance(data, dict) and data.get("errors")): return None
            
            results = []
            for event in data[:8]:
                home, away = event['home_team'], event['away_team']
                
                # APLICA FILTRO NEGRO AQUI TAMBÉM
                full_str = normalize_name(f"{home} {away}")
                if any(bad in full_str for bad in BLACKLIST_KEYWORDS): continue

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

    async def _fetch_api_sports(self, mode):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        url = f"https://{host}/odds?bookmaker=6" # Bet365
        if mode == "nba": url += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.get(url, headers=self.apisports_headers)
            data = r.json().get("response", [])
            
            ranked_matches = []
            
            for item in data:
                try:
                    h_name = item['teams']['home']['name']
                    a_name = item['teams']['away']['name']
                    l_name = item['league']['name']
                    league_id = item['league']['id']
                    
                    h_norm = normalize_name(h_name)
                    a_norm = normalize_name(a_name)
                    l_norm = normalize_name(l_name)
                    
                    # === 🚫 FILTRO ANTI-LIXO (AQUI A MÁGICA ACONTECE) ===
                    # Se tiver "Women", "U19", "Reserve" no nome do time ou da liga, PULA.
                    check_str = f"{h_norm} {a_norm} {l_norm}"
                    if any(bad in check_str for bad in BLACKLIST_KEYWORDS):
                        continue

                    # === RANKING ===
                    score = 0
                    
                    # 1. Prioridade: Seus Times da Lista
                    if any(vip in h_norm for vip in VIP_TEAMS_LIST) or any(vip in a_norm for vip in VIP_TEAMS_LIST):
                        score += 5000
                    
                    # 2. Mengão
                    if "FLAMENGO" in h_norm or "FLAMENGO" in a_norm:
                        score += 2000 

                    # 3. Ligas VIP
                    if league_id in VIP_LEAGUES_IDS:
                        score += 1000
                    
                    if mode == "nba": score += 500

                    if score > 0 or len(ranked_matches) < 5:
                        odds = item['bookmakers'][0]['bets'][0]['values']
                        fav = sorted(odds, key=lambda x: float(x['odd']))[0]
                        
                        ranked_matches.append({
                            "match": f"{h_name} x {a_name}",
                            "odd": float(fav['odd']),
                            "tip": fav['value'],
                            "league": l_name,
                            "score": score
                        })
                except: continue

            ranked_matches.sort(key=lambda x: x['score'], reverse=True)
            return ranked_matches[:12]

engine = SportsEngine()

# ================= HANDLERS =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [["🔥 Top Jogos", "🏀 NBA"], ["💣 Troco do Pão", "✍️ Mensagem Livre"]]
    await u.message.reply_text("🦁 **PAINEL V73 - FILTRO PRO**\n(Apenas Futebol Masculino Profissional)", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def handle_request(u: Update, c, mode="soccer", is_multi=False):
    msg = await u.message.reply_text(f"🔎 Buscando jogos da Elite (Sem Lixo)...")
    
    api_mode = "nba" if mode == "nba" else "soccer"
    result = await engine.get_matches(api_mode)
    data = result["data"]
    
    if not data:
        return await msg.edit_text("❌ Nenhum jogo relevante encontrado hoje (Filtro Ativo).")

    if is_multi:
        sel = random.sample(data, min(5, len(data)))
        odd_t = 1.0
        txt = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
        for g in sel:
            odd_t *= g['odd']
            txt += f"📍 {g['match']} (@{g['odd']})\n"
        txt += f"\n💰 **ODD TOTAL: @{odd_t:.2f}**"
    
    elif result["type"] == "premium":
        txt = f"🏆 **OPORTUNIDADE DE VALOR**\n\n"
        for g in data:
            txt += f"⚔️ {g['match']}\n⭐ Odd: @{g['odd']} ({g['book']})\n💰 Lucro: +R$ {g['profit']}\n\n"
            
    else:
        txt = f"{'🏀' if mode=='nba' else '🔥'} **GRADE PROFISSIONAL**\n\n"
        for g in data:
            icon = "🔴⚫" if "FLAMENGO" in normalize_name(g['match']) else "⚽"
            if any(vip in normalize_name(g['match']) for vip in VIP_TEAMS_LIST) and icon != "🔴⚫":
                icon = "⭐"
            txt += f"{icon} {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | @{g['odd']}\n\n"

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
