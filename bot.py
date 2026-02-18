# ================= BOT V200 (MOTOR 2.5 RESTAURADO + SEM DESCULPAS) =================
import os
import logging
import asyncio
import httpx
import threading
import feedparser
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # O CONSERTO DA MINHA BURRICE: Motor 2.5 de volta (O 1.5 estava dando 404)
    model = genai.GenerativeModel("gemini-2.5-flash")
else:
    model = None

# ================= NOTÍCIAS (RSS FEED) =================
NEWS_FEEDS = ["https://ge.globo.com/rss/ge/futebol/", "https://rss.uol.com.br/feed/esporte.xml"]
sent_news = set()

async def fetch_news():
    noticias = []
    for url in NEWS_FEEDS:
        try:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:3]:
                if entry.link in sent_news: continue
                texto = f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler na íntegra</a>"
                noticias.append(texto)
                sent_news.add(entry.link)
        except Exception:
            pass
    if len(sent_news) > 500: sent_news.clear()
    return noticias[:5]

# ================= IA - TIPSTER (DIRETO E MOTOR NOVO) =================
async def get_ai_analysis_for_match(home_team, away_team):
    if not model: 
        return {"jogador": "ERRO: SEM CHAVE GEMINI", "mercado": "N/A"}

    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")

    prompt = f"""
    Sempre antes de me entregar as análises, faça uma pesquisa no Google sobre os jogadores no mês atual que estamos ({data_hoje}).
    
    Analise de forma direta o confronto: {home_team} x {away_team}.
    NÃO escreva introduções, NÃO diga que não pode pesquisar e NÃO dê desculpas de que é uma IA.
    
    Responda EXATAMENTE E APENAS com duas informações separadas por uma barra vertical (|):
    1: Nome do artilheiro atual do confronto.
    2: Escolha o melhor mercado lógico (Escolha apenas UMA: Vitória do {home_team}, Vitória do {away_team}, Mais de 8.5 Escanteios, Mais de 4.5 Cartões, Over 2.5 Gols).
    
    Exemplo de resposta obrigatório: Bukayo Saka | Mais de 8.5 Escanteios
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        linha = response.text.strip().replace('*', '').replace('`', '').replace('"', '').split('\n')[0]
        
        logging.info(f"🧠 RESPOSTA IA: {linha}")
        
        if "|" in linha:
            parts = linha.split("|")
            return {"jogador": parts[0].strip(), "mercado": parts[1].strip()}
        else:
            return {"jogador": f"FALHA DE FORMATO: {linha[:20]}", "mercado": "Over 2.5 Gols"}
            
    except Exception as e:
        return {"jogador": f"ERRO GOOGLE: {str(e)[:25]}", "mercado": "Over 2.5 Gols"}

# ================= ODDS FUTEBOL =================
async def fetch_games():
    if not ODDS_KEY: return "SEM_CHAVE"
    leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league", "soccer_brazil_campeonato", "soccer_conmebol_libertadores"]
    jogos = []
    
    br_tz = timezone(timedelta(hours=-3))
    hoje = datetime.now(br_tz).date()
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?regions=uk&markets=h2h,totals&apiKey={ODDS_KEY}"
            try:
                r = await client.get(url)
                data = r.json()
                
                if isinstance(data, dict) and data.get("message"):
                    if "quota" in data["message"].lower() or "limit" in data["message"].lower(): return "COTA_EXCEDIDA"
                
                if isinstance(data, list):
                    for g in data:
                        game_time = datetime.fromisoformat(g['commence_time'].replace('Z', '+00:00')).astimezone(br_tz)
                        if game_time.date() != hoje: continue 
                            
                        odd_home = 0; odd_away = 0; odd_over_25 = 0
                        
                        for book in g.get('bookmakers', []):
                            for m in book.get('markets', []):
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == g['home_team']: odd_home = max(odd_home, o['price'])
                                        if o['name'] == g['away_team']: odd_away = max(odd_away, o['price'])
                                elif m['key'] == 'totals':
                                    for o in m['outcomes']:
                                        if o['name'] == 'Over' and o.get('point') == 2.5: odd_over_25 = max(odd_over_25, o['price'])

                        jogos.append({
                            "home": g['home_team'], "away": g['away_team'], "match": f"{g['home_team']} x {g['away_team']}",
                            "odd_home": round(odd_home, 2), "odd_away": round(odd_away, 2), "odd_over_25": round(odd_over_25, 2),
                            "time": game_time.strftime("%H:%M")
                        })
            except Exception as e:
                logging.error(f"Erro Odds: {e}")
    return jogos

def format_game_analysis(game, ai_data):
    jogador = ai_data.get("jogador", "Indisponível")
    mercado_ia = ai_data.get("mercado", "Over 2.5 Gols")
    
    if "ERRO" in jogador or "FALHA" in jogador:
        prop = f"⚠️ <b>Erro Exposto:</b> {jogador}"
    else:
        prop = f"🎯 <b>Player Prop:</b> {jogador} p/ marcar"

    mercado_final = f"📊 <b>Tendência do Jogo:</b> {mercado_ia}"
    
    if "Vitória do " + game['home'] in mercado_ia and game['odd_home'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['home']} (@{game['odd_home']})"
    elif "Vitória do " + game['away'] in mercado_ia and game['odd_away'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['away']} (@{game['odd_away']})"
    elif "Over 2.5" in mercado_ia and game['odd_over_25'] > 0:
        mercado_final = f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{game['odd_over_25']})"
    elif "Escanteios" in mercado_ia:
        mercado_final = f"🚩 <b>Estatística:</b> Média Alta de Escanteios (+8.5)"
    elif "Cartões" in mercado_ia:
        mercado_final = f"🟨 <b>Estatística:</b> Jogo pegado (+4.5 Cartões)"

    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n{mercado_final}\n"

# ================= ODDS NBA (SÓ HOJE) =================
async def fetch_nba_games():
    if not ODDS_KEY: return "SEM_CHAVE"
    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?regions=us&markets=h2h&apiKey={ODDS_KEY}"
    jogos = []
    
    br_tz = timezone(timedelta(hours=-3))
    hoje = datetime.now(br_tz).date()
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            data = r.json()
            if isinstance(data, dict) and data.get("message"):
                if "quota" in data["message"].lower(): return "COTA_EXCEDIDA"
            if isinstance(data, list):
                for g in data:
                    game_time = datetime.fromisoformat(g['commence_time'].replace('Z', '+00:00')).astimezone(br_tz)
                    if game_time.date() != hoje: continue
                    jogos.append({"match": f"{g['home_team']} x {g['away_team']}"})
                    if len(jogos) >= 7: break 
        except Exception as e:
            logging.error(f"Erro NBA: {e}")
    return jogos

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V200")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Futebol (Análise VIP)", callback_data="fut_deep")],
        [InlineKeyboardButton("🏀 NBA (Só Hoje)", callback_data="nba")],
        [InlineKeyboardButton("📰 Notícias", callback_data="news")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V200 ONLINE</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()

    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>A compilar a grade de Futebol...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await status_msg.edit_text("❌ <b>ERRO FATAL:</b> Chave da API esgotada.")
            return
        if not jogos:
            await status_msg.edit_text("❌ Nenhum jogo oficial programado para HOJE.")
            return

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        total_jogos = len(jogos)
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>Extraindo dados da IA V2.5...</b> ({i}/{total_jogos})\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            dados_ia = await get_ai_analysis_for_match(g['home'], g['away'])
            texto_final += format_game_analysis(g, dados_ia) + "━━━━━━━━━━━━━━━━\n"
            if i < total_jogos: await asyncio.sleep(4) 

        await status_msg.edit_text("✅ <b>Futebol postado no canal!</b>", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Buscando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_games()
        if jogos == "COTA_EXCEDIDA":
            await q.message.reply_text("❌ <b>ERRO FATAL:</b> Limite da API acabou.")
            return
        if not jogos:
            await q.message.reply_text("❌ Nenhum jogo da NBA programado para HOJE.")
            return
        texto_final = "🏀 <b>NBA - JOGOS (SÓ HOJE)</b> 🏀\n\n"
        for g in jogos:
            texto_final += f"⚔️ <b>{g['match']}</b>\n🔥 ML Parelho (Foco em Props)\n━━━━━━━━━━━━━━━━\n"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "news":
        await q.message.reply_text("📰 <b>Buscando notícias...</b>", parse_mode=ParseMode.HTML)
        news = await fetch_news()
        if news:
            msg = "📰 <b>NOTÍCIAS DE HOJE</b>\n\n" + "\n\n".join(news)
            await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
        else:
            await q.message.reply_text("❌ Nenhuma notícia no momento.")

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
