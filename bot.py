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
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SERVER WEB (KEEP-ALIVE) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V68.1 ONLINE")

# ================= MOTOR DE ANÁLISE E COMPARAÇÃO =================
class OddsScanner:
    def __init__(self):
        self.odds_url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"

    async def get_best_market(self, mode="soccer"):
        sport_key = "soccer_brazil_campeonato_brasileiro_serie_a" if mode == "soccer" else "basketball_nba"
        
        params = {
            "apiKey": THE_ODDS_API_KEY,
            "regions": "br",
            "markets": "h2h",
            "oddsFormat": "decimal"
        }
        
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                r = await client.get(self.odds_url.format(sport=sport_key), params=params)
                data = r.json()
                
                if not data or isinstance(data, dict) and data.get("errors"):
                    return None

                results = []
                for event in data[:6]:
                    home = event['home_team']
                    away = event['away_team']
                    
                    # Coleta todas as odds para achar a melhor e a pior (para comparar lucro)
                    all_h = []
                    for b in event['bookmakers']:
                        for m in b['markets']:
                            for o in m['outcomes']:
                                if o['name'] == home:
                                    all_h.append({"price": o['price'], "book": b['title']})
                    
                    if not all_h: continue
                    
                    best = max(all_h, key=lambda x: x['price'])
                    worst = min(all_h, key=lambda x: x['price'])
                    
                    # Cálculo de lucro em R$ 100
                    profit_diff = (best['price'] - worst['price']) * 100

                    results.append({
                        "match": f"{home} x {away}",
                        "best_odd": best['price'],
                        "best_book": best['book'],
                        "worst_odd": worst['price'],
                        "profit_plus": round(profit_diff, 2),
                        "sport_icon": "⚽" if mode == "soccer" else "🏀"
                    })
                return results
            except Exception as e:
                logger.error(f"Erro Scanner: {e}")
                return None

scanner = OddsScanner()

# ================= HANDLERS =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [
        ["🔥 Top Jogos (Valor)", "🏀 NBA Scanner"],
        ["💣 Troco do Pão", "✍️ Mensagem Livre"]
    ]
    await u.message.reply_text("🦁 **SISTEMA SCANNER V68.1**\nMonitorando Bet365, Betano e Pinnacle...", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def handle_scanner(u: Update, c, mode="soccer"):
    msg_wait = await u.message.reply_text("🔎 Cruzando dados de múltiplas casas...")
    data = await scanner.get_best_market(mode)
    
    if not data:
        return await msg_wait.edit_text("❌ Sem dados. Verifique sua THE_ODDS_API_KEY no Render.")

    res = f"{data[0]['sport_icon']} **OPORTUNIDADES DE VALOR**\n\n"
    for opt in data:
        res += f"🏟 **{opt['match']}**\n"
        res += f"⭐ Melhor Odd: @{opt['best_odd']} ({opt['best_book']})\n"
        res += f"📉 Pior Odd: @{opt['worst_odd']}\n"
        res += f"💰 **Lucro extra: +R$ {opt['profit_plus']}** (em R$ 100)\n\n"

    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="post_channel")]]
    await u.message.reply_text(res, reply_markup=InlineKeyboardMarkup(kb))
    await msg_wait.delete()

async def handle_free_text(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    if u.message.text in ["🔥 Top Jogos (Valor)", "🏀 NBA Scanner", "💣 Troco do Pão", "✍️ Mensagem Livre"]: return
    
    kb = [[InlineKeyboardButton("📤 Enviar para o Canal", callback_data="post_channel")]]
    await u.message.reply_text(f"📝 **PRÉVIA PARA O CANAL:**\n\n{u.message.text}", reply_markup=InlineKeyboardMarkup(kb))

# ================= CALLBACKS =================
async def callback_handler(u: Update, c):
    query = u.callback_query
    await query.answer()
    
    if query.data == "post_channel":
        content = query.message.text.replace("📝 PRÉVIA PARA O CANAL:\n\n", "")
        try:
            await c.bot.send_message(chat_id=CHANNEL_ID, text=content, parse_mode=ParseMode.MARKDOWN)
            await query.edit_message_text(text=content + "\n\n✅ **POSTADO NO CANAL!**")
        except Exception as e:
            await query.edit_message_text(text=f"❌ Erro ao enviar: {e}")

# ================= MAIN =================
async def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever(), daemon=True).start()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("Top Jogos"), lambda u, c: handle_scanner(u, c, "soccer")))
    app.add_handler(MessageHandler(filters.Regex("NBA Scanner"), lambda u, c: handle_scanner(u, c, "nba")))
    app.add_handler(MessageHandler(filters.Regex("Troco do Pão"), lambda u, c: handle_scanner(u, c, "soccer")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
    app.add_handler(CallbackQueryHandler(callback_handler))

    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.initialize(); await app.start()
    await app.updater.start_polling()
    
    logger.info("🚀 BOT V68.1 OPERACIONAL!")
    while True: await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
