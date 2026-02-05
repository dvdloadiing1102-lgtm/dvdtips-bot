import os
import asyncio
import logging
import feedparser
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configuração de Logs (Essencial para debugar erros)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Carrega variáveis
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
NEWS_FEED = os.getenv("NEWS_FEED", "https://ge.globo.com/rss/ge/") # Valor padrão caso não tenha no .env

# --- DADOS (Mockup) ---
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
    """Função segura para enviar ao canal"""
    if not CHANNEL_ID:
        return "❌ ERRO: CHANNEL_ID não configurado."
    
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode=ParseMode.MARKDOWN)
        return "✅ Enviado para o canal!"
    except Exception as e:
        logging.error(f"Erro ao postar no canal: {e}")
        return f"❌ Erro ao postar: {e}"

# --- HANDLERS DO BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔥 Top Jogos", callback_data="top_jogos"),
         InlineKeyboardButton("🏀 NBA Hoje", callback_data="nba_hoje")],
        [InlineKeyboardButton("💣 Troco do Pão", callback_data="troco_pao"),
         InlineKeyboardButton("🦁 All In Supremo", callback_data="all_in")],
        [InlineKeyboardButton("🚀 Múltipla @20", callback_data="multi_odd"),
         InlineKeyboardButton("📰 Notícias", callback_data="news")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🦁 **PAINEL DE CONTROLE**\nEscolha o que deseja gerar:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Para o reloginho do botão parar de girar
    data = query.data
    
    msg_to_send = ""

    if data == "top_jogos":
        msg_to_send = "🔥 **TOP JOGOS DE HOJE**\n\n"
        for j in FUTEBOL_JOGOS:
            msg_to_send += f"⚽ {j['match']}\n📊 {j['tipo']} — @{j['odd']:.2f}\n\n"

    elif data == "nba_hoje":
        msg_to_send = "🏀 **NBA - MELHORES ENTRADAS**\n\n"
        for j in NBA_JOGOS:
            msg_to_send += f"⛹️ {j['match']}\n📊 {j['tipo']} — @{j['odd']:.2f}\n\n"

    elif data == "troco_pao":
        jogos = FUTEBOL_JOGOS[:3]
        odd_total = calcular_odd_total(jogos)
        msg_to_send = "💣 **TROCO DO PÃO (MÚLTIPLA)**\n\n"
        for j in jogos:
            msg_to_send += f"📍 {j['match']} (@{j['odd']})\n"
        msg_to_send += f"\n💰 **ODD TOTAL: @{odd_total:.2f}**"

    elif data == "all_in":
        j = FUTEBOL_JOGOS[0]
        msg_to_send = "🦁 **ALL IN SUPREMO**\n\n"
        msg_to_send += f"⚔️ {j['match']}\n🎯 Entrada: **{j['tipo']}**\n📈 Odd: @{j['odd']:.2f}\n\n🔥 Confiança: **ALTÍSSIMA**"

    elif data == "multi_odd":
        # Pega 5 de futebol + 2 de NBA
        jogos = FUTEBOL_JOGOS + NBA_JOGOS 
        odd_total = calcular_odd_total(jogos)
        
        msg_to_send = "🚀 **MÚLTIPLA LENDÁRIA (@20+)**\n\n"
        for j in jogos:
            msg_to_send += f"✅ {j['match']} (@{j['odd']})\n"
        msg_to_send += f"\n🤑 **ODD FINAL: @{odd_total:.2f}**"

    elif data == "news":
        await query.edit_message_text("⏳ Buscando notícias...")
        
        # Executa o feedparser em uma thread separada para não travar o bot
        def get_feed():
            return feedparser.parse(NEWS_FEED)
        
        loop = asyncio.get_running_loop()
        feed = await loop.run_in_executor(None, get_feed)
        
        msg_to_send = "📰 **NOTÍCIAS DO MUNDO DA BOLA**\n\n"
        for entry in feed.entries[:5]:
            msg_to_send += f"🔹 [{entry.title}]({entry.link})\n"

    # Envia para o canal e avisa o admin
    if msg_to_send:
        status = await enviar_para_canal(context, msg_to_send)
        # Edita a mensagem do bot para confirmar o envio
        await query.edit_message_text(text=f"{msg_to_send}\n\n📢 Status: {status}", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# --- MAIN ---
def main():
    if not BOT_TOKEN:
        print("Erro: BOT_TOKEN não encontrado no arquivo .env")
        return

    # Cria a aplicação
    app = Application.builder().token(BOT_TOKEN).build()

    # Adiciona os comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    # Inicia o bot (Bloqueante)
    print("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()