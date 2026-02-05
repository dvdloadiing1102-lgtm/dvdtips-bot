import os
import asyncio
import logging
import random
import httpx
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Telegram Imports
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY") # Backup (API-Sports)
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY") # Principal (The Odds API)
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SERVER WEB (RENDER) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V70 - ODDS API ATIVA")

# ================= MOTOR DE ODDS HÍBRIDO =================
class SportsEngine:
    def __init__(self):
        self.apisports_headers = {"x-apisports-key": API_FOOTBALL_KEY}
        self.odds_base_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    async def get_matches(self, mode="soccer"):
        # TENTATIVA 1: The Odds API (Scanner de Lucro)
        # Fevereiro: Brasileirão parado. Usando Premier League (EPL) para teste.
        sport_key = "soccer_epl" if mode == "soccer" else "basketball_nba"
        
        if THE_ODDS_API_KEY:
            try:
                logger.info(f"Tentando The Odds API com a liga: {sport_key}")
                data = await self._fetch_the_odds(sport_key)
                if data: 
                    return {"type": "premium", "data": data}
                else:
                    logger.warning("The Odds API retornou vazio (pode ser falta de jogos hoje).")
            except Exception as e:
                logger.error(f"Erro na The Odds API: {e}")
        
        # TENTATIVA 2: Backup API-Sports
        logger.info("Ativando Backup: API-Sports")
        data = await self._fetch_api_sports(mode)
        return {"type": "standard", "data": data}

    async def _fetch_the_odds(self, sport_key):
        params = {
            "apiKey": THE_ODDS_API_KEY,
            "regions": "br,uk,eu", # Regiões expandidas para garantir odds
            "markets": "h2h",
            "oddsFormat": "decimal"
        }
        
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(self.odds_base_url.format(sport=sport_key), params=params)
            json_data = r.json()
            
            # Log para você ver no Render se a chave deu erro
            if isinstance(json_data, dict) and json_data.get("message"):
                logger.error(f"ERRO DE CHAVE: {json_data['message']}")
                return None

            results = []
            # Pega até 8 jogos
            for event in json_data[:8]:
                home, away = event['home_team'], event['away_team']
                all_h = []
                
                # Varre todas as casas de aposta
                for b in event['bookmakers']:
                    for m in b['markets']:
                        for o in m['outcomes']:
                            if o['name'] == home: 
                                all_h.append({"p": o['price'], "b": b['title']})
                
                if not all_h: continue
                
                # Acha a melhor odd e calcula lucro
                best = max(all_h, key=lambda x: x['p'])
                worst = min(all_h, key=lambda x: x['p'])
                profit = (best['p'] - worst['p']) * 100
                
                results.append({
                    "match": f"{home} x {away}",
                    "odd": best['p'],
                    "book": best['b'],
                    "profit": round(profit, 2),
                    "sport": "⚽" if "soccer" in sport_key else "🏀"
                })
            return results

    async def _fetch_api_sports(self, mode):
        host = "v3.football.api-sports.io" if mode == "soccer" else "v1.basketball.api-sports.io"
        url = f"https://{host}/odds?bookmaker=6" # Bet365
        if mode == "nba": url += "&league=12&season=2025"
        
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, headers=self.apisports_headers)
            data = r.json().get("response", [])
            results = []
            for item in data[:8]:
                try:
                    odds = item['bookmakers'][0]['bets'][0]['values']
                    fav = sorted(odds, key=lambda x: float(x['odd']))[0]
                    results.append({
                        "match": f"{item['teams']['home']['name']} x {item['teams']['away']['name']}",
                        "odd": float(fav['odd']),
                        "tip": fav['value'],
                        "sport": "⚽" if mode == "soccer" else "🏀"
                    })
                except: continue
            return results

engine = SportsEngine()

# ================= HANDLERS =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [["🔥 Top Jogos (Lucro)", "🏀 NBA Scanner"], ["💣 Troco do Pão", "✍️ Mensagem Livre"]]
    await u.message.reply_text("🦁 **SISTEMA V70 - SCANNER ATIVO**\nBuscando Premier League e NBA.", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def handle_request(u: Update, c, mode="soccer", is_multi=False):
    msg = await u.message.reply_text("🔎 Analisando oportunidades de lucro...")
    
    api_mode = "nba" if mode == "nba" else "soccer"
    result = await engine.get_matches(api_mode)
    data = result["data"]
    
    if not data:
        return await msg.edit_text("❌ Sem jogos encontrados agora nas duas APIs.")

    # FORMATAÇÃO DA MENSAGEM
    if is_multi: # Múltipla
        sel = random.sample(data, min(5, len(data)))
        odd_t = 1.0
        txt = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
        for g in sel:
            # Tratamento para formatos diferentes de dados
            odd_val = g.get('odd', 1.0)
            odd_t *= odd_val
            txt += f"📍 {g['match']} (@{odd_val})\n"
        txt += f"\n💰 **ODD TOTAL: @{odd_t:.2f}**"
    
    elif result["type"] == "premium": # THE ODDS API (SCANNER)
        txt = f"{data[0]['sport']} **SCANNER DE ARBITRAGEM**\n\n"
        for g in data:
            txt += f"🏟 **{g['match']}**\n"
            txt += f"⭐ Melhor Odd: @{g['odd']} ({g['book']})\n"
            txt += f"💰 **Lucro Extra: +R$ {g['profit']}** (para R$ 100)\n\n"
            
    else: # API SPORTS (BACKUP)
        txt = f"{data[0]['sport']} **TIPS DO DIA**\n\n"
        for g in data:
            txt += f"⚔️ {g['match']}\n🎯 Palpite: {g['tip']} | @{g['odd']}\n\n"

    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="send")]]
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    await msg.delete()

async def handle_free_text(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    # Filtra comandos do teclado
    if any(cmd in u.message.text for cmd in ["Top Jogos", "NBA", "Troco", "Livre"]): return
    
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
