import os
import logging
import asyncio
import feedparser
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIGURAÇÃO DE LOGS (Essencial para ver erros no Render) ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()

# --- VARIÁVEIS DE AMBIENTE ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
NEWS_FEED = os.getenv("NEWS_FEED", "https://ge.globo.com/rss/ge/") # Link padrão do GE caso falte

# --- DADOS (SEUS JOGOS HARDCODED) ---
FUTEBOL_JOGOS = [
    {"match": "Corinthians x Palmeiras", "odd": 1.62, "tipo": "Favorito vence"},
    {"match": "Atalanta x Juventus", "odd": 1.55, "tipo": "Favorito vence"},
    {"match": "Real Madrid x Barcelona", "odd": 1.5, "tipo": "Favorito vence"},
    {"match": "Manchester City x Arsenal", "odd": 1.65, "tipo": "Favorito vence"},
]

NBA_JOGOS = [
    {"match": "Lakers x Warriors", "odd": 1.72, "tipo": "Favorito vence"},
    {"match": "Bucks x Heat", "odd": 1.68, "tipo": "Favorito vence"},
]

# --- FUNÇÕES AUXILIARES ---
def calcular_odd_total(jogos):
    total = 1.0
    for j in jogos:
        total *= j['odd']
    return total

async def enviar_para_canal(context, text):
    """Envia mensagem para o canal com tratamento de erro"""
    if not CHANNEL_ID:
        logging.warning("CHANNEL_ID não configurado!")
        return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except Exception as e:
        logging.error(f"Erro ao postar no canal: {e}")

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("🦁 All In Supremo", callback_data="all_in")],
        [InlineKeyboardButton("🚀 Múltipla 20 Odd", callback_data="multi_odd"),
         InlineKeyboardButton("📰 Notícias", callback_data="news")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🦁 **PAINEL DE CONTROLE**\nEscolha uma opção:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Para o reloginho do botão parar
    data = query.data
    
    msg = ""

    if data == "top_jogos":
        msg = "🔥 **TOP JOGOS HOJE**\n\n"
        for j in FUTEBOL_JOGOS:
            msg += f"⚽ {j['match']} - Odd: @{j['odd']}\n"

    elif data == "nba_hoje":
        msg = "🏀 **NBA HOJE**\n\n"
        for j in NBA_JOGOS:
            msg += f"⛹️ {j['match']} - Odd: @{j['odd']}\n"

    elif data == "troco_pao":
        msg = "💣 **TROCO DO PÃO — MÚLTIPLA**\n\n"
        for j in FUTEBOL_JOGOS[:3]:
            msg += f"📍 {j['match']} @ {j['odd']}\n"
        
        # Cálculo automático da odd
        odd_calc = calcular_odd_total(FUTEBOL_JOGOS[:3])
        msg += f"\n💰 **Odd Total: @{odd_calc:.2f}**"

    elif data == "all_in":
        j = FUTEBOL_JOGOS[0]
        msg = "🦁 **ALL IN SUPREMO — PICK DO DIA**\n\n"
        msg += f"⚔️ {j['match']}\n🎯 {j['tipo']} @ {j['odd']}\n🔥 Confiança: **ALTÍSSIMA**"

    elif data == "multi_odd":
        selection = FUTEBOL_JOGOS[:5] + NBA_JOGOS[:2]
        odd_calc = calcular_odd_total(selection)
        
        msg = "🎯 **MÚLTIPLA 20 ODD**\n\n"
        for j in selection:
            msg += f"✅ {j['match']} @ {j['odd']}\n"
        msg += f"\n🔥 **TOTAL ODD: @{odd_calc:.2f}**"

    elif data == "news":
        await query.edit_message_text("⏳ Baixando notícias...")
        
        # Executa o feedparser em background para não travar o bot
        def get_news():
            return feedparser.parse(NEWS_FEED)
        
        feed = await asyncio.get_running_loop().run_in_executor(None, get_news)
        
        msg = "⚽ **NOTÍCIAS DE FUTEBOL HOJE**\n\n"
        for entry in feed.entries[:5]:
            msg += f"📰 {entry.title}\n🔗 {entry.link}\n\n"

    # Envia para o admin (feedback) e para o canal
    if msg:
        await enviar_para_canal(context, msg)
        try:
            await query.edit_message_text(f"{msg}\n\n✅ **POSTADO NO CANAL!**", disable_web_page_preview=True)
        except:
            # Caso a mensagem seja igual ou dê erro de edição
            await query.message.reply_text("✅ Postado!")

# --- MAIN ---
def main():
    if not BOT_TOKEN:
        print("❌ ERRO: BOT_TOKEN não encontrado.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    print("✅ Bot rodando...")
    # run_polling já gerencia o loop, não use asyncio.run aqui
    app.run_polling()

if __name__ == "__main__":
    main()
