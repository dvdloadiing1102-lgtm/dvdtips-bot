import os
import logging
import asyncio
import httpx
import threading
import unicodedata
import random
import json
from datetime import datetime, timezone, timedelta, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import feedparser
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ================= CONFIGURACAO =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ODDS_KEY = os.getenv("THE_ODDS_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

# DICIONARIO FALSO DELETADO COM SUCESSO.

# ================= IA JSON =================
async def get_players_for_matches(jogos_list):
    if not model or not jogos_list: return {}

    matches_str = "\n".join([f'- "{g["match"]}"' for g in jogos_list])
    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")

    prompt = f"""
    Sempre antes de me entregar as análises, faça uma pesquisa no Google sobre os jogadores no mês atual que estamos ({data_hoje}).
    Retorne EXATAMENTE UM ARQUIVO JSON VÁLIDO. Sem blocos de código markdown.
    As chaves do JSON devem ser EXATAMENTE os nomes dos confrontos fornecidos abaixo.
    O valor deve ser APENAS o nome e sobrenome do melhor jogador de ataque ou artilheiro em boa fase (de qualquer um dos times).
    Se não encontrar nenhum jogador 100% confirmado e em boa fase, retorne uma string vazia "". Não invente.

    Exemplo:
    {{
        "Aston Villa x Leeds United": "Ollie Watkins",
        "Nacional Potosí x Botafogo": "Igor Jesus"
    }}

    Confrontos:
    {matches_str}
    """
    try:
        logging.info("📡 Consultando IA...")
        response = await asyncio.to_thread(
            model.generate_content, 
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        dados = json.loads(raw_text)
        return dados
    except Exception as e:
        logging.error(f"❌ Erro na IA: {e}")
        return {}

# ================= ODDS FUTEBOL REAIS (SÓ HOJE + LIBERTADORES) =================
async def fetch_games():
    if not ODDS_KEY: return "SEM_CHAVE"
    
    # Adicionado Libertadores e mais ligas para cobrir os jogos que estavam sumindo
    leagues = [
        "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", 
        "soccer_uefa_champs_league", "soccer_brazil_campeonato", 
        "soccer_conmebol_libertadores", "soccer_portugal_primeira_liga",
        "soccer_germany_bundesliga", "soccer_france_ligue_one"
    ]
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
                        # TRAVA ABSOLUTA: EXATAMENTE HOJE
                        game_time = datetime.fromisoformat(g['commence_time'].replace('Z', '+00:00')).astimezone(br_tz)
                        if game_time.date() != hoje:
                            continue 
                            
                        odds_over_25 = 0
                        odds_over_15 = 0
                        
                        for book in g.get('bookmakers', []):
                            for m in book.get('markets', []):
                                if m['key'] == 'totals':
                                    for o in m['outcomes']:
                                        if o['name'] == 'Over' and o.get('point') == 2.5: odds_over_25 = max(odds_over_25, o['price'])
                                        if o['name'] == 'Over' and o.get('point') == 1.5: odds_over_15 = max(odds_over_15, o['price'])

                        jogos.append({
                            "home": g["home_team"], "away": g["away_team"], "match": f"{g['home_team']} x {g['away_team']}",
                            "odd_over_25": odds_over_25, "odd_over_15": odds_over_15, "time": game_time.strftime("%H:%M")
                        })
                        
                        if len([j for j in jogos if j['home'] == g["home_team"]]) > 3: break
                        
            except Exception as e:
                logging.error(f"Erro na API Odds: {e}")
    return jogos

def analyze_game(game, player_star):
    # Fim do nome inventado. Se a IA falhou, manda escanteios.
    if player_star and player_star.strip() != "":
        prop = f"🎯 <b>Player Prop:</b> {player_star} p/ finalizar no alvo ou marcar"
    else:
        prop = "📊 <b>Tendência:</b> Foco no mercado de Escanteios (+8.5)"

    if game["odd_over_25"] > 0 and 1.40 <= game["odd_over_25"] <= 1.95:
        gols_text = f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{game['odd_over_25']})"
    elif game["odd_over_15"] > 0 and 1.25 <= game["odd_over_15"] <= 1.55:
        gols_text = f"🥅 <b>Mercado:</b> Over 1.5 Gols (@{game['odd_over_15']})"
    else:
        gols_text = "⚔️ <b>Mercado:</b> Ambas Marcam Sim"

    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n{gols_text}\n"

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
                    if game_time.date() != hoje:
                        continue
                    jogos.append({"match": f"{g['home_team']} x {g['away_team']}"})
                    if len(jogos) >= 5: break
        except Exception as e:
            logging.error(f"Erro NBA: {e}")
    return jogos

# ================= NOTICIAS =================
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
        except Exception as e:
            pass
    if len(sent_news) > 500: sent_news.clear()
    return noticias[:5]

# ================= SERVER =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V181")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= TELEGRAM E MENU =================
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Futebol (Só Hoje)", callback_data="fut"), InlineKeyboardButton("🏀 NBA (Só Hoje)", callback_data="nba")],
        [InlineKeyboardButton("📰 Notícias", callback_data="news")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V181 ONLINE (Sem Nomes Falsos & Mais Ligas)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "fut":
        await q.message.reply_text("⏳ <b>Buscando as rodadas EXATAS de hoje...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await q.message.reply_text("❌ <b>ERRO FATAL:</b> Chave da API sem limite.")
            return
        if not jogos:
            await q.message.reply_text("❌ Nenhum jogo relevante programado para HOJE.")
            return
            
        jogadores_dict = await get_players_for_matches(jogos)

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        for g in jogos:
            craque = jogadores_dict.get(g["match"])
            msg = analyze_game(g, craque)
            texto_final += msg + "━━━━━━━━━━━━━━━━\n"
            
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Buscando NBA (Só Hoje)...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_games()
        if jogos == "COTA_EXCEDIDA":
            await q.message.reply_text("❌ <b>ERRO FATAL:</b> Limite da The Odds API acabou.")
            return
        if not jogos:
            await q.message.reply_text("❌ Nenhum jogo da NBA programado para HOJE.")
            return
            
        texto_final = "🏀 <b>NBA - JOGOS (SÓ HOJE)</b> 🏀\n\n"
        for g in jogos:
            texto_final += f"⚔️ <b>{g['match']}</b>\n🔥 ML Parelho (Foco em Props)\n━━━━━━━━━━━━━━━━\n"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "news":
        news = await fetch_news()
        if news:
            msg = "📰 <b>NOTÍCIAS</b>\n\n" + "\n\n".join(news)
            await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
