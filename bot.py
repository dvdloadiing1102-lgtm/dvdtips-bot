import os
import asyncio
import logging
import sqlite3
import random
import httpx
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

# ================= CONFIGURAÇÕES =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") 
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID") # O ID do seu canal (ex: -100...)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SPORTS API =================
class SportsAPI:
    async def get_odds(self, sport="soccer"):
        host = "v3.football.api-sports.io" if sport == "soccer" else "v1.basketball.api-sports.io"
        url = f"https://{host}/odds?date={datetime.now().strftime('%Y-%m-%d')}&bookmaker=6"
        if sport == "basketball": url += "&league=12"
        
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
                        "odd": float(fav['odd']),
                        "tip": fav['value']
                    })
                return matches
        except: return []

api = SportsAPI()

# ================= HANDLERS =================
async def start(u: Update, c):
    if str(u.effective_user.id) != str(ADMIN_ID): return
    kb = [["🔥 Top Jogos", "🚀 Múltipla Segura"], ["💣 Troco do Pão", "🏀 NBA"], ["🎫 Gerar Key"]]
    await u.message.reply_text("🦁 **SISTEMA DE GESTÃO DE TIPS**", 
                               reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def handle_top_games(u: Update, c):
    msg_status = await u.message.reply_text("🔎 Buscando melhores odds...")
    games = await api.get_odds("soccer")
    if not games: return await msg_status.edit_text("❌ Sem dados no momento.")
    
    res = "🔥 **TIPS DO DIA**\n\n"
    for g in games[:5]:
        res += f"⚽ {g['name']}\n🎯 {g['tip']} | @{g['odd']}\n\n"
    
    # BOTÃO PARA ENVIAR AO CANAL
    kb = [[InlineKeyboardButton("📤 Postar no Canal", callback_data="post_to_channel")]]
    await u.message.reply_text(res, reply_markup=InlineKeyboardMarkup(kb))
    await msg_status.delete()

async def handle_multi_risk(u: Update, c):
    games = await api.get_odds("soccer")
    if len(games) < 5: return await u.message.reply_text("❌ Jogos insuficientes.")
    
    sel = random.sample(games, 5)
    odd_f = 1.0
    res = "💣 **MÚLTIPLA @20 (TROCO DO PÃO)**\n\n"
    for g in sel:
        odd_f *= g['odd']
        res += f"✅ {g['name']} (@{g['odd']})\n"
    res += f"\n💰 **ODD FINAL: @{odd_f:.2f}**"
    
    kb = [[InlineKeyboardButton("🚀 Enviar Bilhete", callback_data="post_to_channel")]]
    await u.message.reply_text(res, reply_markup=InlineKeyboardMarkup(kb))

# ================= CALLBACK (O BOTÃO DE ENVIAR) =================
async def button_callback(u: Update, c):
    query = u.callback_query
    await query.answer()
    
    if query.data == "post_to_channel":
        try:
            # Pega o texto da mensagem onde o botão foi clicado e envia pro canal
            await c.bot.send_message(chat_id=CHANNEL_ID, text=query.message.text, parse_mode=None)
            await query.edit_message_caption(caption=query.message.text + "\n\n✅ **ENVIADO AO CANAL!**") # Se for foto
        except:
            # Se for apenas texto
            await query.edit_message_text(text=query.message.text + "\n\n✅ **ENVIADO AO CANAL!**")

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("Top Jogos"), handle_top_games))
    app.add_handler(MessageHandler(filters.Regex("Troco do Pão"), handle_multi_risk))
    app.add_handler(MessageHandler(filters.Regex("NBA"), handle_top_games)) # Reutiliza lógica
    
    # ESSA LINHA ATIVA OS BOTÕES DE ENVIAR
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 Bot completo com botões de envio online!")
    app.run_polling()

if __name__ == "__main__":
    main()
