import os
import logging
import asyncio
import httpx
import threading
import json
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
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

# ================= IA PARA JOGADORES E ESTATÍSTICAS =================
async def get_ai_analysis(jogos_list):
    """
    Usa a IA para buscar jogadores em destaque e sugerir estatísticas (escanteios/cartões)
    baseado no confronto atual e notícias recentes.
    """
    if not model or not jogos_list: return {}

    matches_str = "\n".join([f'- "{g["match"]}"' for g in jogos_list])
    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%d de %B de %Y")

    prompt = f"""
    Você é um analista de apostas esportivas especialista em futebol mundial.
    Data de hoje: {data_hoje}.
    Para cada confronto abaixo, pesquise e identifique:
    1. O jogador (artilheiro ou batedor de faltas/pênaltis) mais provável de marcar ou dar assistência hoje.
    2. Uma linha de Escanteios provável (ex: "Mais de 9.5 escanteios").
    3. Uma linha de Cartões provável (ex: "Mais de 4.5 cartões").

    Retorne EXATAMENTE um JSON no seguinte formato, sem markdown:
    {{
        "Time A x Time B": {{
            "player": "Nome do Jogador",
            "corners": "Tendência de Escanteios",
            "cards": "Tendência de Cartões"
        }}
    }}

    Confrontos:
    {matches_str}
    """
    try:
        logging.info("📡 Consultando IA para análise detalhada...")
        response = await asyncio.to_thread(
            model.generate_content, 
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        raw_text = response.text.strip()
        dados = json.loads(raw_text)
        return dados
    except Exception as e:
        logging.error(f"❌ Erro na IA: {e}")
        return {}

# ================= ODDS FUTEBOL REAIS =================
async def fetch_games():
    if not ODDS_KEY: return "SEM_CHAVE"
    # Ligas principais para análise
    leagues = [
        "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", 
        "soccer_uefa_champs_league", "soccer_brazil_campeonato", 
        "soccer_portugal_primeira_liga", "soccer_germany_bundesliga",
        "soccer_france_ligue_one"
    ]
    jogos = []
    
    br_tz = timezone(timedelta(hours=-3))
    hoje = datetime.now(br_tz).date()
    
    async with httpx.AsyncClient(timeout=30) as client:
        for league in leagues:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?regions=eu&markets=h2h,totals&apiKey={ODDS_KEY}"
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
                            
                        odds_over_25 = 0
                        odds_over_15 = 0
                        
                        for book in g.get('bookmakers', []):
                            for m in book.get('markets', []):
                                if m['key'] == 'totals':
                                    for o in m['outcomes']:
                                        if o['name'] == 'Over' and o.get('point') == 2.5: 
                                            odds_over_25 = max(odds_over_25, o['price'])
                                        if o['name'] == 'Over' and o.get('point') == 1.5: 
                                            odds_over_15 = max(odds_over_15, o['price'])

                        jogos.append({
                            "id": g["id"],
                            "sport": league,
                            "home": g["home_team"], 
                            "away": g["away_team"], 
                            "match": f"{g['home_team']} x {g['away_team']}",
                            "odd_over_25": odds_over_25, 
                            "odd_over_15": odds_over_15, 
                            "time": game_time.strftime("%H:%M")
                        })
            except Exception as e:
                logging.error(f"Erro na API Odds ({league}): {e}")
    return jogos

def format_game_msg(game, ai_data):
    match_name = game["match"]
    analysis = ai_data.get(match_name, {})
    
    player = analysis.get("player", "Destaque do jogo")
    corners = analysis.get("corners", "Tendência de +8.5 escanteios")
    cards = analysis.get("cards", "Tendência de +3.5 cartões")

    # Lógica de Gols baseada em Odds Reais da API
    if game["odd_over_25"] > 0 and 1.40 <= game["odd_over_25"] <= 2.15:
        gols_text = f"Over 2.5 Gols (@{game['odd_over_25']})"
    elif game["odd_over_15"] > 0 and 1.20 <= game["odd_over_15"] <= 1.65:
        gols_text = f"Over 1.5 Gols (@{game['odd_over_15']})"
    else:
        gols_text = "Ambas Marcam Sim"

    msg = (
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{match_name}</b>\n"
        f"🎯 <b>Jogador:</b> {player}\n"
        f"🥅 <b>Gols:</b> {gols_text}\n"
        f"🚩 <b>Escanteios:</b> {corners}\n"
        f"🟨 <b>Cartões:</b> {cards}\n"
    )
    return msg

# ================= SERVER PARA MANTER ONLINE =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT ONLINE - DVD TIPS V2.0")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= TELEGRAM E COMANDOS =================
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Gerar Análises de Hoje", callback_data="fut")],
        [InlineKeyboardButton("🏀 NBA (Em breve)", callback_data="nba")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦁 <b>DVD TIPS V2.0 - SISTEMA ATUALIZADO</b>\n\n"
        "Clique no botão abaixo para buscar os jogos de hoje e gerar as análises completas com IA.", 
        reply_markup=get_main_menu(), 
        parse_mode=ParseMode.HTML
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "fut":
        await q.message.reply_text("📡 <b>Buscando jogos e consultando estatísticas atualizadas...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await q.message.reply_text("❌ Erro: Limite da API de Odds atingido.")
            return
        if not jogos:
            await q.message.reply_text("❌ Nenhum jogo relevante encontrado para hoje.")
            return
            
        # Pega análise da IA para os jogos encontrados
        ai_analysis = await get_ai_analysis(jogos)

        texto_final = "🔥 <b>GRADE DE HOJE - ANÁLISE COMPLETA</b> 🔥\n\n"
        for g in jogos:
            msg = format_game_msg(g, ai_analysis)
            texto_final += msg + "━━━━━━━━━━━━━━━━\n"
            
        # Envia para o canal configurado
        try:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)
            await q.message.reply_text("✅ Análises enviadas com sucesso para o canal!")
        except Exception as e:
            await q.message.reply_text(f"❌ Erro ao enviar para o canal: {e}")

def main():
    # Inicia servidor web em thread separada (para plataformas como Render/Heroku)
    threading.Thread(target=run_server, daemon=True).start()
    
    # Inicia o Bot do Telegram
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    
    print("Bot iniciado e aguardando comandos...")
    app.run_polling()

if __name__ == "__main__":
    main()
