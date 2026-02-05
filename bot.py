import os
import asyncio
import logging
import random
import httpx
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

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

# ================= SERVER WEB =================
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V63 ONLINE")

# ================= API DE ESPORTES (FIX) =================
class SportsAPI:
    async def get_market_data(self, sport="soccer"):
        host = "v3.football.api-sports.io" if sport == "soccer" else "v1.basketball.api-sports.io"
        # Busca odds da Bet365 (ID 6)
        url = f"https://{host}/odds?bookmaker=6"
        if sport == "basketball": url += "&league=12&season=2025" 
        
        headers = {"x-rapidapi-host": host, "x-rapidapi-key": API_FOOTBALL_KEY}
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                r = await client.get(url, headers=headers)
                json_data = r.json()
                
                # Log de segurança para você ver no Render se a API bloqueou
                if json_data.get("errors"):
                    logger.error(f"Erro na API: {json_data['errors']}")
                    return None

                data = json_data.get("response", [])
                if not data: return []

                matches = []
                for item in data[:12]:
                    try:
                        odds = item['bookmakers'][0]['bets'][0]['values']
                        fav = sorted(odds, key=lambda x: float(x['odd']))[0]
                        matches.append({
                            "name": f"{item['teams']['home']['name']} x {item['teams']['away']['name']}",
                            "odd": float(fav['odd']), 
                            "tip": fav['value'],
                            "sport": "⚽" if sport == "soccer" else "🏀"
                        })
                    except: continue
                return matches
        except Exception as e:
            logger.error(f"Falha de conexão: {e}")
            return None

api = SportsAPI()

# ================= HANDLERS =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [
        ["🔥 Top Jogos", "🚀 Múltipla Segura"], 
        ["💣 Troco do Pão", "🏀 NBA"],
        ["✍️ Mensagem Livre"]
    ]
    await u.message.reply_text("🦁 **SISTEMA V63 - GESTÃO DE CANAL**", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def process_tips(u: Update, c, sport="soccer", type="top"):
    msg_status = await u.message.reply_text("🔎 Consultando Bet365...")
    games = await api.get_market_data(sport)
    
    if games is None:
        return await msg_status.edit_text("❌ Erro na API. Verifique se sua chave expirou ou atingiu o limite.")
    if not games:
        return await msg_status.edit_text("⚠️ Sem jogos com odds disponíveis no momento nesta liga.")
    
    if type == "risk":
        sel = random.sample(games, min(5, len(games)))
        odd_f = 1.0
        res = "💣 **MÚLTIPLA DE RISCO (ODD ALTA)**\n\n"
        for g in sel:
            odd_f *= g['odd']
            res += f"📍 {g['name']} (@{g['odd']})\n"
        res += f"\n💰 **ODD FINAL: @{odd_f:.2f}**"
    else:
        res = f"{'🔥' if sport=='soccer' else '🏀'} **ENTRADA SUGERIDA**\n\n"
        g = games[0] # Pega o melhor jogo
        res += f"🏆 Jogo: {g['name']}\n🎯 Entrada: {g['tip']}\n📈 Odd: @{g['odd']}\n\n🍀 Boa sorte!"

    # OS DOIS BOTÕES DE POSTAGEM
    kb = [
        [InlineKeyboardButton("📤 Postar no Canal", callback_data="post_now")],
        [InlineKeyboardButton("🗑️ Descartar", callback_data="delete")]
    ]
    await u.message.reply_text(res, reply_markup=InlineKeyboardMarkup(kb))
    await msg_status.delete()

async def free_message(u: Update, c):
    await u.message.reply_text("📝 Digite a mensagem que deseja formatar para o canal:")
    return 1

async def handle_text_free(u: Update, c):
    text = u.message.text
    kb = [[InlineKeyboardButton("📤 Enviar para o Canal", callback_data="post_now")]]
    await u.message.reply_text(f"📝 **PRÉVIA DA MENSAGEM:**\n\n{text}", reply_markup=InlineKeyboardMarkup(kb))

# ================= CALLBACKS =================
async def button_handler(u: Update, c):
    query = u.callback_query
    await query.answer()
    
    if query.data == "post_now":
        text_to_send = query.message.text.replace("📝 PRÉVIA DA MENSAGEM:\n\n", "")
        await c.bot.send_message(chat_id=CHANNEL_ID, text=text_to_send)
        await query.edit_message_text(text=text_to_send + "\n\n✅ **ENVIADO COM SUCESSO!**")
    elif query.data == "delete":
        await query.message.delete()

# ================= MAIN =================
async def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever(), daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("Top Jogos"), lambda u, c: process_tips(u, c, "soccer", "top")))
    app.add_handler(MessageHandler(filters.Regex("NBA"), lambda u, c: process_tips(u, c, "basketball", "top")))
    app.add_handler(MessageHandler(filters.Regex("Troco do Pão"), lambda u, c: process_tips(u, c, "soccer", "risk")))
    app.add_handler(MessageHandler(filters.Regex("Mensagem Livre"), lambda u, c: u.message.reply_text("Envie o texto abaixo:")))
    
    # Captura qualquer texto solto para a função de Mensagem Livre
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^(🔥|🚀|💣|🏀|✍️)"), handle_text_free))
    
    app.add_handler(CallbackQueryHandler(button_handler))

    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.initialize(); await app.start()
    await app.updater.start_polling()
    while True: await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
