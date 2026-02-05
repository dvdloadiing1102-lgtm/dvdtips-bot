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

# ================= LOGGING =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("V75")

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN or not ADMIN_ID or not CHANNEL_ID:
    raise ValueError("❌ ENV obrigatória faltando")

logger.info("✅ ENV OK")

# ================= RATE LIMIT =================
class RateLimiter:
    def __init__(self, cps=2):
        self.min_interval = 1 / cps
        self.last = 0

    async def wait(self):
        elapsed = time.time() - self.last
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self.last = time.time()

rate = RateLimiter(2)

# ================= FILTER =================
BLACKLIST = ["WOMEN","U19","U20","U21","SUB","YOUTH","VIRTUAL","RESERVE","ESOCCER"]
VIP_TEAMS = ["FLAMENGO","PALMEIRAS","BOTAFOGO","FLUMINENSE","VASCO","CORINTHIANS",
             "REAL MADRID","BARCELONA","PSG","LIVERPOOL","MANCHESTER CITY"]

def norm(txt):
    return ''.join(c for c in unicodedata.normalize('NFD', txt or "")
                   if unicodedata.category(c) != 'Mn').upper()

# ================= KEEP ALIVE =================
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"V75.4 ONLINE")

    def log_message(self, format, *args):
        pass

def keep_alive():
    HTTPServer(("0.0.0.0", PORT), PingHandler).serve_forever()

# ================= ENGINE =================
class SportsEngine:
    def __init__(self):
        self.headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_api = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    def today(self):
        return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")

    async def get_matches(self, mode="soccer"):
        premium = await self._fetch_odds(mode)
        if premium:
            return {"type":"premium","data":premium}

        return {"type":"standard","data": await self._fetch_fixtures(mode)}

    async def _fetch_fixtures(self, mode):
        try:
            host = "v3.football.api-sports.io" if mode=="soccer" else "v1.basketball.api-sports.io"
            url = f"https://{host}/fixtures?date={self.today()}"

            async with httpx.AsyncClient(timeout=20) as client:
                await rate.wait()
                r = await client.get(url, headers=self.headers)
                fixtures = r.json().get("response", [])

                games = []
                for f in fixtures:
                    h = f.get("teams",{}).get("home",{}).get("name","")
                    a = f.get("teams",{}).get("away",{}).get("name","")
                    league = f.get("league",{}).get("name","")
                    fid = f.get("fixture",{}).get("id")

                    full = norm(f"{h} {a} {league}")
                    if any(b in full for b in BLACKLIST):
                        continue

                    score = 5000 if any(v in full for v in VIP_TEAMS) else 0
                    if score:
                        games.append({"id":fid,"match":f"{h} x {a}","league":league,"score":score})

                games.sort(key=lambda x:x["score"], reverse=True)
                final = []

                for g in games[:8]:
                    odd, tip = await self._get_odds(client, host, g["id"])
                    final.append({**g,"odd":odd,"tip":tip})

                return final
        except:
            return []

    async def _get_odds(self, client, host, fid):
        try:
            url = f"https://{host}/odds?fixture={fid}&bookmaker=6"
            await rate.wait()
            r = await client.get(url, headers=self.headers)
            data = r.json().get("response", [])

            if data:
                values = data[0].get("bookmakers",[{}])[0].get("bets",[{}])[0].get("values",[])
                if values:
                    fav = sorted(values, key=lambda x: float(x.get("odd",0)))[0]
                    return float(fav.get("odd",0)), fav.get("value","Odd pendente")

            return 0.0, "Odd pendente"
        except:
            return 0.0, "Erro"

    async def _fetch_odds(self, mode):
        if not THE_ODDS_API_KEY:
            return None

        try:
            sport = "soccer_uefa_champs_league" if mode=="soccer" else "basketball_nba"

            async with httpx.AsyncClient(timeout=10) as client:
                await rate.wait()
                r = await client.get(
                    self.odds_api.format(sport=sport),
                    params={"apiKey":THE_ODDS_API_KEY,"regions":"br,uk,eu","markets":"h2h"}
                )
                events = r.json()
                res = []

                for e in events[:6]:
                    h = e.get("home_team")
                    a = e.get("away_team")
                    if not h or not a:
                        continue

                    prices = []
                    for b in e.get("bookmakers",[]):
                        for m in b.get("markets",[]):
                            for o in m.get("outcomes",[]):
                                if o.get("name")==h:
                                    prices.append({"p":float(o.get("price",0)), "b":b.get("title")})

                    if prices:
                        best = max(prices, key=lambda x:x["p"])
                        worst = min(prices, key=lambda x:x["p"])
                        profit = round((best["p"]-worst["p"])*100,2)

                        res.append({
                            "match":f"{h} x {a}",
                            "odd":best["p"],
                            "book":best["b"],
                            "profit":profit,
                            "league":"VALUE BET"
                        })

                return res
        except:
            return None

engine = SportsEngine()

# ================= HANDLERS =================
async def start(u: Update, c):
    if u.effective_user.id != ADMIN_ID:
        return

    kb = [["🔥 Top Jogos","🏀 NBA"],["💣 Troco do Pão","📊 ROI"],["✍️ Mensagem Livre"]]
    await u.message.reply_text(
        "🦁 **PAINEL V75.4 ALL IN**",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="Markdown"
    )

async def games(u, c, mode="soccer", multi=False):
    msg = await u.message.reply_text("🔎 Escaneando mercado...")

    result = await engine.get_matches(mode)
    data = result["data"]

    if not data:
        return await msg.edit_text("❌ Nada VIP hoje.")

    if multi:
        valid = [x for x in data if x["odd"] > 1]
        sel = random.sample(valid, min(len(valid),5))
        odd_total = 1

        txt = "💣 **MÚLTIPLA AGRESSIVA**\n\n"
        for g in sel:
            odd_total *= g["odd"]
            txt += f"📍 {g['match']} @{g['odd']}\n"

        txt += f"\n🔥 **ODD TOTAL @{odd_total:.2f}**"

    elif result["type"]=="premium":
        txt = "🏆 **VALUE BET SCANNER**\n\n"
        for g in data:
            txt += f"⚔️ {g['match']}\n⭐ @{g['odd']} ({g['book']})\n💰 ROI +{g['profit']}%\n\n"

    else:
        txt = "🔥 **GRADE ELITE**\n\n"
        for g in data:
            odd = f"@{g['odd']}" if g["odd"] else "⏳"
            txt += f"⭐ {g['match']}\n🏆 {g['league']}\n🎯 {g['tip']} | {odd}\n\n"

    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="send")]]
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    await msg.delete()

async def roi_panel(u, c):
    await u.message.reply_text("📊 ROI Tracker ativo — histórico em breve 💰")

async def free_text(u, c):
    if u.effective_user.id != ADMIN_ID:
        return

    if any(k in u.message.text.lower() for k in ["top","nba","troco"]):
        return

    kb = [[InlineKeyboardButton("📤 Enviar Canal", callback_data="send")]]
    await u.message.reply_text(f"📝 **PRÉVIA:**\n\n{u.message.text}",
                               reply_markup=InlineKeyboardMarkup(kb),
                               parse_mode="Markdown")

async def callback(u: Update, c):
    q = u.callback_query
    await q.answer()

    if q.data == "send":
        txt = q.message.text.replace("📝 **PRÉVIA:**\n\n","")
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode="Markdown")
        await q.edit_message_text(txt + "\n\n✅ POSTADO")

# ================= MAIN =================
def main():
    threading.Thread(target=keep_alive, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)top jogos"), lambda u,c: games(u,c,"soccer")))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)nba"), lambda u,c: games(u,c,"nba")))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)troco"), lambda u,c: games(u,c,"soccer",True)))
    app.add_handler(MessageHandler(filters.Regex(r"(?i)roi"), roi_panel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    app.add_handler(CallbackQueryHandler(callback))

    logger.info("🚀 V75.4 ALL IN ONLINE")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()