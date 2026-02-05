import os
import asyncio
import logging
import sqlite3
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
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SERVIDOR WEB (PRO RENDER NÃO CAIR) =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V62 ONLINE")

def start_server():
    server = HTTPServer(('0.0.0.0', PORT), FakeHandler)
    server.serve_forever()

# ================= SPORTS API (ODDS REAIS) =================
class SportsAPI:
    async def get_market_data(self, sport="soccer"):
        host = "v3.football.api-sports.io" if sport == "soccer" else "v1.basketball.api-sports.io"
        url = f"https://{host}/odds?date={datetime.now().strftime('%Y-%m-%d')}&bookmaker=6"
        if sport == "basketball": url += "&league=12" # NBA
        
        headers = {"x-rapidapi-host": host, "x-rapidapi-key": API_FOOTBALL_KEY}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(url, headers=headers)
                data = r.json().get("response", [])
                matches = []
                for item in data[:15]:
                    odds = item['bookmakers'][0]['bets'][0]['values']
                    fav = sorted(odds, key=lambda x: float(x['odd']))[0]
                    matches.append({
                        "name": f"{item['teams']['home']['name']} x {item['teams']['away']['name']}",
                        "odd": float(fav['odd']), "tip": fav['value'],
                        "sport": "⚽" if sport == "soccer" else "🏀"
                    })
                return matches
        except: return []

api = SportsAPI()

# ================= HANDLERS (ADMIN & TIPS) =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [["🔥 Top Jogos", "🚀 Múltipla Segura"], ["💣 Troco do Pão", "🏀 NBA"], ["🎫 Gerar Key"]]
    await u.message.reply_text("🦁 **SISTEMA DE GESTÃO DE TIPS**", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def process_tips(u: Update, c, sport="soccer", type="top"):
    msg_status = await u.message.reply_text("🔎 Buscando mercado real...")
    games = await api.get_market_data(sport)
    
    if not games: return await msg_status.edit_text("❌ Sem dados da API no momento.")
    
    if type == "risk": # Múltipla @20
        sel = random.sample(games, min(5, len(games)))
        odd_f = 1.0
        res = "💣 **TROCO DO PÃO (ODD ALTA)**\n\n"
        for g in sel:
            odd_f *= g['odd']
            res += f"📍 {g['name']} (@{g['odd']})\n"
        res += f"\n💰 **ODD FINAL: @{odd_f:.2f}**"
    else:
        res = f"{'🔥' if sport=='soccer' else '🏀'} **TIPS DE HOJE**\n\n"
        for g in games[:6]:
            res += f"{g['sport']} {g['name']}\n🎯 {g['tip']} | @{g['odd']}\n\n"

    # BOTÃO PARA ENVIAR AO CANAL
    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="post")]]
    await u.message.reply_text(res, reply_markup=InlineKeyboardMarkup(kb))
    await msg_status.delete()

# ================= CALLBACK PARA POSTAR =================
async def callback_post(u: Update, c):
    query = u.callback_query
    await query.answer("Enviando ao canal...")
    try:
        await c.bot.send_message(chat_id=CHANNEL_ID, text=query.message.text)
        await query.edit_message_text(text=query.message.text + "\n\n✅ **POSTADO NO CANAL!**")
    except Exception as e:
        await query.edit_message_text(text=f"❌ Erro ao postar: {e}")

# ================= MOTOR PRINCIPAL (COM TRAVA DE CONFLITO) =================
async def main():
    # 1. Inicia o servidor web em paralelo
    threading.Thread(target=start_server, daemon=True).start()

    # 2. Configura o Aplicativo
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # 3. Adiciona os Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("Top Jogos"), lambda u, c: process_tips(u, c, "soccer", "top")))
    app.add_handler(MessageHandler(filters.Regex("NBA"), lambda u, c: process_tips(u, c, "basketball", "top")))
    app.add_handler(MessageHandler(filters.Regex("Troco do Pão"), lambda u, c: process_tips(u, c, "soccer", "risk")))
    app.add_handler(CallbackQueryHandler(callback_post, pattern="post"))

    # 4. A SOLUÇÃO PARA O ERRO DE CONFLITO:
    # Remove qualquer conexão aberta antes de começar
    await app.bot.delete_webhook(drop_pending_updates=True)
    
    print("🚀 Bot V62 online e protegido contra conflitos!")
    
    # 5. Inicia o Polling
    await app.updater.initialize()
    await app.updater.start_polling()
    await app.initialize()
    await app.start()

    # Mantém rodando
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
