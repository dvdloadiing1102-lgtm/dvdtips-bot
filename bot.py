import os
import logging
import asyncio
import feedparser
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configuração de Logs (Para você ver os erros no Render)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

load_dotenv()

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
NEWS_FEED = os.getenv("NEWS_FEED", "https://ge.globo.com/rss/ge/") # Link padrão do GE

# --- DADOS (MOCKUP) ---
FUTEBOL_JOGOS = [
    {"match": "Corinthians x Palmeiras", "odd": 1.62, "tipo": "Casa Vence"},
    {"match": "Atalanta x Juventus", "odd": 1.55, "tipo": "Ambas Marcam"},
    {"match": "Real Madrid x Barcelona", "odd": 1.50, "tipo": "Over 2.5"},
    {"match": "Man. City x Arsenal", "odd": 1.65, "tipo": "Casa Vence"},
]

NBA_JOGOS = [
    {"match": "Lakers x Warriors", "odd": 1.72, "tipo": "Lakers -5.5"},
    {"match": "Bucks x Heat", "odd": 1.68, "tipo": "Over 210"},
]

# --- FUNÇÕES AUXILIARES ---
def calcular_odd_total(jogos):
    total = 1.0
    for j in jogos:
        total *= j['odd']
    return total

async def enviar_para_canal(context, text):
    """Envia mensagem formatada para o canal configurado"""
    if not CHANNEL_ID:
        return
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logging.error(f"Erro ao postar no canal: {e}")

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("🦁 All In", callback_data="all_in")],
        [InlineKeyboardButton("🚀 Múltipla @20", callback_data="multi_odd"),
         InlineKeyboardButton("📰 Notícias", callback_data="news")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🦁 **PAINEL DE CONTROLE**\nSelecione uma opção para gerar a TIP:", 
        reply_markup=reply_markup, 
        parse_mode=ParseMode.MARKDOWN
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    msg = ""

    if data == "top_jogos":
        msg = "🔥 **TOP JOGOS DE HOJE**\n\n"
        for j in FUTEBOL_JOGOS:
            msg += f"⚽ {j['match']}\n📊 {j['tipo']} — @{j['odd']:.2f}\n\n"

    elif data == "nba_hoje":
        msg = "🏀 **NBA - MELHORES ENTRADAS**\n\n"
        for j in NBA_JOGOS:
            msg += f"⛹️ {j['match']}\n📊 {j['tipo']} — @{j['odd']:.2f}\n\n"

    elif data == "troco_pao":
        # Pega os 3 primeiros jogos
        selection = FUTEBOL_JOGOS[:3]
        total = calcular_odd_total(selection)
        msg = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
        for j in selection:
            msg += f"📍 {j['match']} (@{j['odd']})\n"
        msg += f"\n💰 **ODD TOTAL: @{total:.2f}**"

    elif data == "all_in":
        j = FUTEBOL_JOGOS[0]
        msg = "🦁 **ALL IN SUPREMO**\n\n"
        msg += f"⚔️ {j['match']}\n🎯 Entrada: **{j['tipo']}**\n📈 Odd: @{j['odd']:.2f}\n🔥 Confiança: **ALTÍSSIMA**"

    elif data == "multi_odd":
        # Junta Futebol e NBA
        selection = FUTEBOL_JOGOS + NBA_JOGOS
        total = calcular_odd_total(selection)
        msg = "🚀 **MÚLTIPLA LENDÁRIA (@20+)**\n\n"
        for j in selection:
            msg += f"✅ {j['match']} (@{j['odd']})\n"
        msg += f"\n🤑 **ODD FINAL: @{total:.2f}**"

    elif data == "news":
        await query.edit_message_text("⏳ Buscando notícias...")
        
        # Roda o feedparser sem travar o bot
        def get_news():
            return feedparser.parse(NEWS_FEED)
        
        feed = await asyncio.get_running_loop().run_in_executor(None, get_news)
        
        msg = "📰 **NOTÍCIAS DO MUNDO DA BOLA**\n\n"
        for entry in feed.entries[:5]:
            msg += f"🔹 [{entry.title}]({entry.link})\n"

    # Envia resposta
    if msg:
        await enviar_para_canal(context, msg)
        try:
            # Tenta editar a mensagem original com confirmação (pode falhar se for muito longa, mas ok)
            await query.edit_message_text(f"{msg}\n\n✅ **ENVIADO AO CANAL!**", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        except:
            await query.message.reply_text("✅ **Conteúdo enviado ao canal!**")

# --- MAIN ---
def main():
    if not BOT_TOKEN:
        print("❌ ERRO: BOT_TOKEN não encontrado.")
        return

    # Constrói o bot
    app = Application.builder().token(BOT_TOKEN).build()

    # Adiciona comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    print("✅ Bot rodando...")
    # Inicia o polling (Bloqueante, não use asyncio.run aqui)
    app.run_polling()

if __name__ == "__main__":
    main()
