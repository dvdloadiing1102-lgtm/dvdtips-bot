# ================= BOT V174 (NEWS BR + IA DINÂMICA + PAINEL COMPLETO) =================
import os
import logging
import asyncio
import httpx
import threading
import unicodedata
import random
from datetime import datetime, timezone, timedelta, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import feedparser
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIG =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# ================= IA =================
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

# ================= MEMÓRIA DINÂMICA DE JOGADORES =================
dynamic_players_cache = {}

async def get_best_player_of_the_moment(team_name):
    if team_name in dynamic_players_cache:
        return dynamic_players_cache[team_name]
    
    if not model: return None
        
    try:
        await asyncio.sleep(2) 
        prompt = f"""
        Você é um analista esportivo. Pense no time {team_name} no futebol atual.
        Quem é o melhor jogador de ataque ou artilheiro deles que está jogando MUITO BEM no momento?
        Leve em conta lesões recentes (não me dê jogador machucado).
        Responda APENAS o nome e sobrenome do jogador, sem pontos, sem frases. Só o nome.
        """
        response = await asyncio.to_thread(model.generate_content, prompt)
        jogador_do_momento = response.text.strip()
        
        dynamic_players_cache[team_name] = jogador_do_momento
        return jogador_do_momento
    except Exception as e:
        logging.error(f"Erro ao buscar jogador do {team_name}: {e}")
        return None

# ================= RSS NEWS (100% BRASILEIRO) =================
NEWS_FEEDS = [
    "https://ge.globo.com/rss/ge/futebol/",
    "https://rss.uol.com.br/feed/esporte.xml",
    "https://www.gazetaesportiva.com/feed/"
]

sent_news = set()

async def summarize_news(title):
    if not model: return None
    try:
        prompt = f"Resuma a notícia a seguir em 1 frase curta em português do Brasil, e diga o impacto nas apostas esportivas:\n{title}"
        r = await asyncio.to_thread(model.generate_content, prompt)
        return r.text.strip()
    except:
        return None

async def fetch_news():
    noticias = []
    for url in NEWS_FEEDS:
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:3]:
                if entry.link in sent_news: continue

                resumo = await summarize_news(entry.title)
                if resumo:
                    texto = f"📰 <b>{entry.title}</b>\n🧠 {resumo}\n🔗 <a href='{entry.link}'>Ler na íntegra</a>"
                else:
                    texto = f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler na íntegra</a>"

                noticias.append(texto)
                sent_news.add(entry.link)
        except Exception as e:
            logging.error(f"Erro feed {url}: {e}")
            
    if len(sent_news) > 500: sent_news.clear()
    return noticias[:5]

KEYWORDS_IMPORTANTES = ["lesão", "lesionado", "desfalque", "fora", "suspenso", "transferência", "contratado", "demitido", "banido", "crise", "demissão", "rompimento", "vetado"]

def is_breaking(text):
    return any(k in text.lower() for k in KEYWORDS_IMPORTANTES)

# ================= ODDS FUTEBOL =================
async def fetch_games():
    if not ODDS_KEY: return []
    leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league", "soccer_brazil_campeonato"]
    jogos = []
    
    async with httpx.AsyncClient(timeout=15) as client:
        for league in leagues:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?regions=uk&markets=h2h&apiKey={ODDS_KEY}"
            try:
                r = await client.get(url)
                data = r.json()
                if isinstance(data, list):
                    for g in data[:3]: 
                        jogos.append({"home": g["home_team"], "away": g["away_team"], "match": f"{g['home_team']} x {g['away_team']}"})
            except Exception as e:
                logging.error(f"Erro na API Odds Fut: {e}")
    return jogos

async def analyze_game(game):
    star = await get_best_player_of_the_moment(game["home"])
    if star:
        prop = f"🎯 <b>Player Prop:</b> {star} p/ finalizar no alvo"
    else:
        prop = "📊 <b>Tendência:</b> Foco no mercado de Escanteios (+8.5)"
    return f"⚔️ <b>{game['match']}</b>\n{prop}\n🥅 Over 1.5 Gols (Tendência)\n"

# ================= ODDS NBA =================
async def fetch_nba_games():
    if not ODDS_KEY: return []
    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?regions=us&markets=h2h&apiKey={ODDS_KEY}"
    jogos = []
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            data = r.json()
            if isinstance(data, list):
                for g in data[:5]:
                    jogos.append({"home": g["home_team"], "away": g["away_team"], "match": f"{g['home_team']} x {g['away_team']}"})
        except Exception as e:
            logging.error(f"Erro na API Odds NBA: {e}")
    return jogos

# ================= SERVER =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ONLINE - DVD TIPS V174")

def run_server():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= MENU E BOTÕES =================
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
        [InlineKeyboardButton("📰 Forçar Notícias", callback_data="news")],
        [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Limpar Cache", callback_data="force")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V174 ONLINE</b>\nPainel de Controle de Apostas e Notícias.", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "menu":
        await q.edit_message_text("🦁 <b>MENU PRINCIPAL V174</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

    elif q.data == "fut":
        await q.message.reply_text("⏳ <b>Buscando Futebol e analisando artilheiros via IA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        if not jogos:
            await q.message.reply_text("❌ Nenhum jogo de futebol encontrado agora.")
            return
        texto_final = "🔥 <b>GRADE DE FUTEBOL DO DIA</b> 🔥\n\n"
        for g in jogos:
            msg = await analyze_game(g)
            texto_final += msg + "━━━━━━━━━━━━━━━━\n"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Buscando jogos da NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_games()
        if not jogos:
            await q.message.reply_text("❌ Nenhum jogo da NBA encontrado agora.")
            return
        texto_final = "🏀 <b>NBA - JOGOS DO DIA</b> 🏀\n\n"
        for g in jogos:
            texto_final += f"⚔️ <b>{g['match']}</b>\n🔥 ML Parelho (Foco em Props dos astros)\n━━━━━━━━━━━━━━━━\n"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "news":
        await q.message.reply_text("📰 <b>Caçando últimas notícias na mídia...</b>", parse_mode=ParseMode.HTML)
        news = await fetch_news()
        if not news:
            await q.message.reply_text("Nenhuma notícia relevante no momento.")
            return
        breaking = [n for n in news if is_breaking(n)]
        if breaking:
            msg = "🚨 <b>BREAKING NEWS</b>\n\n" + "\n\n".join(breaking)
        else:
            msg = "📰 <b>GIRO DE NOTÍCIAS (BRASIL E MUNDO)</b>\n\n" + "\n\n".join(news)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)

    elif q.data == "force":
        dynamic_players_cache.clear()
        sent_news.clear()
        await q.message.reply_text("🔄 <b>Cache Limpo!</b> Memória de jogadores e histórico de notícias apagados.", parse_mode=ParseMode.HTML)

    elif q.data == "status":
        report = "📊 <b>STATUS DO SISTEMA V174</b>\n"
        report += f"✅ The Odds API: {'Configurada' if ODDS_KEY else 'Faltando'}\n"
        report += f"✅ Gemini AI: {'Online' if model else 'Off'}\n"
        report += f"🧠 Jogadores no Cache: {len(dynamic_players_cache)}\n"
        report += f"📰 Notícias no Cache: {len(sent_news)}\n"
        await q.edit_message_text(report, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data="menu")]]), parse_mode=ParseMode.HTML)

# ================= JOBS =================
async def send_news_job(context):
    news = await fetch_news()
    if not news: return
    breaking = [n for n in news if is_breaking(n)]
    if breaking:
        msg = "🚨 <b>BREAKING NEWS</b>\n\n" + "\n\n".join(breaking)
    else:
        msg = "📰 <b>GIRO DE NOTÍCIAS (BRASIL E MUNDO)</b>\n\n" + "\n\n".join(news)
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)

async def daily_games_job(context):
    jogos = await fetch_games()
    if not jogos: return
    texto_final = "🔥 <b>GRADE DE JOGOS (AUTOMÁTICA)</b> 🔥\n\n"
    for g in jogos:
        msg = await analyze_game(g)
        texto_final += msg + "━━━━━━━━━━━━━━━━\n"
    await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

# ================= MAIN =================
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))

    if app.job_queue:
        tz = timezone(timedelta(hours=-3))
        app.job_queue.run_daily(send_news_job, time=time(9,0,tzinfo=tz))
        app.job_queue.run_daily(send_news_job, time=time(15,0,tzinfo=tz))
        app.job_queue.run_daily(send_news_job, time=time(21,0,tzinfo=tz))
        app.job_queue.run_daily(daily_games_job, time=time(10,0,tzinfo=tz))

    print("BOT V174 rodando com Painel de Controle Completo...")
    app.run_polling()

if __name__ == "__main__":
    main()
