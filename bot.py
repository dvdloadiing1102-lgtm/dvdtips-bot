import os
import asyncio
import logging
import random
import httpx
import threading
import unicodedata
import time
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ================= CONFIG EXTRA =================
DEBUG_MODE = os.getenv("DEBUG_MODE", "0") == "1"
ZOEIRA_MODE = os.getenv("ZOEIRA_MODE", "1") == "1"

# ================= LOGGING =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('/tmp/bot_v76.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

if ADMIN_ID:
    ADMIN_ID = int(ADMIN_ID)

REQUIRED_VARS = {
    "BOT_TOKEN": BOT_TOKEN,
    "ADMIN_ID": ADMIN_ID,
    "CHANNEL_ID": CHANNEL_ID
}

for k, v in REQUIRED_VARS.items():
    if not v:
        raise ValueError(f"❌ ENV obrigatória ausente: {k}")

logger.info("✅ ENV OK")

# ================= RATE LIMIT =================
class RateLimiter:
    def __init__(self, cps=2):
        self.min_interval = 1.0 / cps
        self.last_call = 0
    
    async def wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

rate_limiter = RateLimiter(2)

# ================= FILTERS =================
BLACKLIST_KEYWORDS = [
    "WOMEN","FEM","U19","U20","U21","SUB","YOUTH","RESERVE","VIRTUAL","ESOCCER"
]

VIP_TEAMS_LIST = [
    "FLAMENGO","PALMEIRAS","BOTAFOGO","FLUMINENSE","SAO PAULO","CORINTHIANS",
    "VASCO","REAL MADRID","MANCHESTER CITY","BARCELONA","LIVERPOOL","PSG",
    "BAYERN","JUVENTUS","MILAN","CHELSEA","ARSENAL"
]

ZOEIRA_PHRASES = [
    "🔥 Hoje é dia de caçar odd igual leão faminto",
    "💀 Se perder hoje, foi culpa do Mercúrio retrógrado",
    "😈 A banca treme quando esse bot acorda",
    "🍞 Troco do pão nível HARD",
    "⚡ Odd selecionada na base da maldade"
]

def normalize_name(name):
    if not name:
        return ""
    try:
        return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()
    except:
        return ""

# ================= HTTP SERVER =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"BOT V76 ULTRA ONLINE")
        except:
            pass
    def log_message(self, format, *args):
        pass

# ================= ENGINE =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
        logger.info("✅ Engine pronta")

    def get_today_date(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        # Premium primeiro
        if THE_ODDS_API_KEY:
            data = await self._fetch_the_odds("soccer_uefa_champs_league")
            if data:
                return {"type": "premium", "data": data}

        # Fixtures fallback
        data = await self._fetch_from_fixtures(mode)

        # Se nada encontrado → fallback agressivo
        if not data:
            logger.warning("⚠️ VIP vazio → fallback geral")
            data = await self._fetch_from_fixtures(mode, force_all=True)

        return {"type": "standard", "data": data}

    async def _fetch_from_fixtures(self, mode, force_all=False):
        try:
            host = "v3.football.api-sports.io"
            date_str = self.get_today_date()
            url = f"https://{host}/fixtures?date={date_str}"

            async with httpx.AsyncClient(timeout=20) as client:
                await rate_limiter.wait()
                r = await client.get(url, headers=self.headers)
                raw = r.json().get("response", [])

                relevant_games = []

                for item in raw:
                    h = item.get('teams', {}).get('home', {}).get('name', '')
                    a = item.get('teams', {}).get('away', {}).get('name', '')
                    league = item.get('league', {}).get('name', '')
                    fixture_id = item.get('fixture', {}).get('id')

                    full = normalize_name(f"{h} {a} {league}")

                    if any(b in full for b in BLACKLIST_KEYWORDS):
                        continue

                    score = 1
                    if any(v in full for v in VIP_TEAMS_LIST):
                        score += 5000

                    if force_all or score > 1:
                        relevant_games.append({
                            "id": fixture_id,
                            "match": f"{h} x {a}",
                            "league": league,
                            "score": score
                        })

                relevant_games.sort(key=lambda x: x['score'], reverse=True)

                final_list = []

                for game in relevant_games[:10]:
                    odd, tip = await self._get_odds_for_fixture(client, host, game["id"])
                    final_list.append({
                        "match": game["match"],
                        "league": game["league"],
                        "odd": odd,
                        "tip": tip
                    })

                if DEBUG_MODE:
                    logger.info(f"📊 API: {len(raw)} | Filtrados: {len(final_list)}")

                return final_list
        
        except Exception as e:
            logger.error(f"Fixtures error: {e}")
            return []

    async def _get_odds_for_fixture(self, client, host, fixture_id):
        try:
            if not fixture_id:
                return 0.0, "Indisponível"

            url = f"https://{host}/odds?fixture={fixture_id}&bookmaker=6"
            await rate_limiter.wait()
            r = await client.get(url, headers=self.headers)

            data = r.json().get("response", [])

            if data:
                odds = data[0].get('bookmakers', [{}])[0].get('bets', [{}])[0].get('values', [])
                if odds:
                    fav = sorted(odds, key=lambda x: float(x.get('odd', 0)))[0]
                    return float(fav.get('odd', 0)), fav.get('value', 'Aguardando Odd')

            return 0.0, "Aguardando Odd"
        except:
            return 0.0, "Indisponível"

    async def _fetch_the_odds(self, sport_key):
        try:
            params = {
                "apiKey": THE_ODDS_API_KEY,
                "regions": "br,uk,eu",
                "markets": "h2h",
                "oddsFormat": "decimal"
            }

            async with httpx.AsyncClient(timeout=10) as client:
                await rate_limiter.wait()
                r = await client.get(self.odds_base_url.format(sport=sport_key), params=params)
                data = r.json()

                results = []

                for event in data[:6]:
                    home = event.get('home_team')
                    away = event.get('away_team')

                    if not home or not away:
                        continue

                    best_price = 0
                    book = "Unknown"

                    for b in event.get('bookmakers', []):
                        for m in b.get('markets', []):
                            for o in m.get('outcomes', []):
                                if o.get('name') == home:
                                    try:
                                        price = float(o.get('price', 0))
                                        if price > best_price:
                                            best_price = price
                                            book = b.get('title')
                                    except:
                                        pass

                    if best_price > 0:
                        results.append({
                            "match": f"{home} x {away}",
                            "odd": best_price,
                            "book": book,
                            "profit": round(best_price * 10, 2),
                            "league": "🏆 Valor"
                        })

                return results
        
        except:
            return None

engine = SportsEngine()

# ================= HANDLERS =================
async def start(u: Update, c):
    if u.effective_user.id != ADMIN_ID:
        return

    kb = [["🔥 Top Jogos", "🏀 NBA"], ["💣 Troco do Pão", "✍️ Mensagem Livre"]]

    await u.message.reply_text(
        "🦁 **PAINEL V76 ULTRA ONLINE**",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def handle_request(u: Update, c, mode="soccer", is_multi=False):
    msg = await u.message.reply_text("🔎 Escaneando jogos...")

    result = await engine.get_matches(mode)
    data = result["data"]

    if not data:
        return await msg.edit_text("❌ Hoje até o universo conspirou. Nenhum jogo encontrado.")

    zoeira_line = f"\n\n😈 {random.choice(ZOEIRA_PHRASES)}" if ZOEIRA_MODE else ""

    if is_multi:
        valid = [g for g in data if g['odd'] > 1.0]
        if len(valid) < 2:
            return await msg.edit_text("❌ Poucos jogos pra múltipla hoje.")

        sel = random.sample(valid, min(5, len(valid)))
        odd_total = 1.0
        txt = "💣 **TROCO DO PÃO (INSANO)**\n\n"

        for g in sel:
            odd_total *= g['odd']
            txt += f"📍 {g['match']} (@{g['odd']})\n"

        txt += f"\n💰 **ODD TOTAL: @{odd_total:.2f}**{zoeira_line}"

    elif result["type"] == "premium":
        txt = "🏆 **SCANNER DE VALOR**\n\n"
        for g in data:
            txt += f"⚔️ {g['match']}\n⭐ @{g['odd']} ({g['book']})\n💰 +R$ {g['profit']}\n\n"
        txt += zoeira_line

    else:
        txt = "🔥 **GRADE ELITE**\n\n"
        for g in data:
            odd_txt = f"@{g['odd']}" if g['odd'] > 0 else "⏳ Aguardando"
            txt += f"⭐ {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {odd_txt}\n\n"
        txt += zoeira_line

    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="send")]]
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    await msg.delete()

async def handle_free_text(u: Update, c):
    if u.effective_user.id != ADMIN_ID:
        return

    if any(k in u.message.text for k in ["Top", "NBA", "Troco"]):
        return

    kb = [[InlineKeyboardButton("📤 Enviar Canal", callback_data="send")]]
    await u.message.reply_text(f"📝 **PRÉVIA:**\n\n{u.message.text}", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u: Update, c):
    q = u.callback_query
    await q.answer()

    if q.data == "send":
        txt = q.message.text.replace("📝 **PRÉVIA:**\n\n", "")
        await c.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.MARKDOWN)
        await q.edit_message_text(txt + "\n\n✅ POSTADO")

# ================= MAIN SAFE =================
async def main():
    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever(),
        daemon=True
    ).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)top jogos"), lambda u,c: handle_request(u,c,"soccer")))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)nba"), lambda u,c: handle_request(u,c,"nba")))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)troco"), lambda u,c: handle_request(u,c,"soccer", True)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
    app.add_handler(CallbackQueryHandler(callback_handler))

    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.start()

    logger.info("🚀 BOT V76 ULTRA ONLINE")

    await asyncio.Event().wait()

# ================= BOOT =================
if __name__ == "__main__":
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except:
        pass

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())