# ================= BOT V203 (ARQUITETURA EM LOTE - FIM DO ERRO 429) =================
import os
import logging
import asyncio
import httpx
import threading
import random
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

# ================= IA - TIPSTER EM LOTE (1 REQUISIÇÃO = SEM BLOQUEIOS) =================
async def get_bulk_ai_analysis(jogos):
    if not model or not jogos: 
        return [{"jogador": "ERRO_CHAVE", "mercado": "Mais de 8.5 Escanteios"} for _ in jogos]

    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")

    # Prepara a lista de jogos num texto só
    jogos_texto = "\n".join([f"{i+1}. {g['home']} x {g['away']}" for i, g in enumerate(jogos)])

    prompt = f"""
    Você é um tipster VIP. Estamos em {data_hoje}.
    Analise a seguinte lista de {len(jogos)} jogos de futebol de hoje:
    
    {jogos_texto}
    
    Responda COM EXATAMENTE {len(jogos)} LINHAS. Uma para cada jogo, mantendo a mesma ordem da lista.
    NÃO escreva introduções. NÃO pule linhas.
    
    Formato OBRIGATÓRIO para cada linha: Nome do Jogador | Mercado Lógico
    
    Regras:
    1. Jogador: Artilheiro atual (NÃO liste aposentados).
    2. Mercado Lógico: Escolha APENAS UMA entre (Vitória do Mandante, Vitória do Visitante, Mais de 8.5 Escanteios, Mais de 4.5 Cartões, Over 2.5 Gols, Ambas Marcam Sim). Varie as opções entre os jogos.
    
    Exemplo:
    Bukayo Saka | Mais de 8.5 Escanteios
    Rafael Leão | Over 2.5 Gols
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        linhas = response.text.strip().replace('*', '').replace('`', '').split('\n')
        
        resultados = []
        for linha in linhas:
            if "|" in linha:
                parts = linha.split("|")
                resultados.append({"jogador": parts[0].strip(), "mercado": parts[1].strip()})
        
        # Se a IA engolir alguma linha, preenchemos o que faltou para não quebrar o código
        while len(resultados) < len(jogos):
            resultados.append({"jogador": "FALHA_FORMATO", "mercado": random.choice(["Mais de 8.5 Escanteios", "Mais de 4.5 Cartões"])})
            
        return resultados
    except Exception as e:
        logging.error(f"Erro no Bulk IA: {e}")
        # Retorno de segurança se a requisição falhar de vez
        return [{"jogador": "FALHA_CONEXÃO", "mercado": random.choice(["Mais de 8.5 Escanteios", "Mais de 4.5 Cartões"])} for _ in jogos]

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
    mercado_ia = ai_data.get("mercado", "Mais de 8.5 Escanteios")
    
    if "COTA" in jogador or "FALHA" in jogador or "ERRO" in jogador:
        prop = f"⚠️ <b>Aviso:</b> Falha na conexão da IA."
    else:
        prop = f"🎯 <b>Player Prop:</b> {jogador} p/ marcar"

    mercado_final = f"📊 <b>Tendência do Jogo:</b> {mercado_ia}"
    
    if "Vitória do " + game['home'] in mercado_ia and game['odd_home'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['home']} (@{game['odd_home']})"
    elif "Vitória do " + game['away'] in mercado_ia and game['odd_away'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['away']} (@{game['odd_away']})"
    elif "Over 2.5" in mercado_ia and game['odd_over_25'] > 0:
        mercado_final = f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{game['odd_over_25']})"
    elif "Ambas" in mercado_ia:
        mercado_final = f"⚔️ <b>Mercado:</b> Ambas Marcam Sim"
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
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V203")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Futebol (Análise em Lote)", callback_data="fut_deep")],
        [InlineKeyboardButton("🏀 NBA (Só Hoje)", callback_data="nba")],
        [InlineKeyboardButton("📰 Notícias", callback_data="news")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V203 ONLINE (Sistema Anti-Bloqueio)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()

    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>A compilar a grade de Futebol...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await status_msg.edit_text("❌ <b>ERRO FATAL:</b> Chave da API das Odds esgotada.")
            return
        if not jogos:
            await status_msg.edit_text("❌ Nenhum jogo oficial programado para HOJE.")
            return

        await status_msg.edit_text("⏳ <b>A processar todos os jogos numa única requisição...</b>", parse_mode=ParseMode.HTML)
        
        # A MÁGICA ACONTECE AQUI: Uma única chamada para a IA com a lista inteira
        dados_ia_lista = await get_bulk_ai_analysis(jogos)
        
        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        for i, g in enumerate(jogos):
            dados = dados_ia_lista[i] if i < len(dados_ia_lista) else {"jogador": "FALHA", "mercado": "Mais de 8.5 Escanteios"}
            texto_final += format_game_analysis(g, dados) + "━━━━━━━━━━━━━━━━\n"

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
