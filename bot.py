# ================= BOT V191 (IA MULTI-MERCADOS) =================
import os
import logging
import asyncio
import httpx
import threading
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

# ================= IA - EXTRATOR MULTI-MERCADOS =================
async def get_ai_analysis_for_match(home_team, away_team):
    if not model: 
        return {"jogador": "ERRO: Chave GEMINI_API_KEY em falta", "mercado": "ERRO"}

    # Pedimos à IA para fazer a dupla análise de forma direta
    prompt = f"""
    Atua como um analista de apostas desportivas. Analisa o confronto: {home_team} vs {away_team}.
    Fornece exatamente DUAS informações, separadas por uma barra vertical (|).
    1: O nome e apelido do principal avançado ou melhor marcador atual de uma das equipas.
    2: O mercado estatístico com maior probabilidade de bater para este estilo de jogo. (Escolhe APENAS UMA opção: "Mais de 8.5 Cantos", "Mais de 4.5 Cartões", "Menos de 2.5 Golos", "Mais de 2.5 Golos" ou "Ambas Marcam Sim").
    
    NÃO escrevas introduções. Responde APENAS no formato: Jogador | Mercado
    Exemplo: Bukayo Saka | Mais de 8.5 Cantos
    """
    try:
        response = await model.generate_content_async(prompt)
        linha = response.text.strip().replace('*', '').replace('`', '').replace('"', '').split('\n')[0]
        
        logging.info(f"🧠 RESPOSTA IA ({home_team}): {linha}")
        
        if "|" in linha:
            parts = linha.split("|")
            return {"jogador": parts[0].strip(), "mercado": parts[1].strip()}
        else:
            return {"jogador": linha[:30], "mercado": "Mais de 8.5 Cantos"}
            
    except Exception as e:
        return {"jogador": f"ERRO API: {str(e)[:20]}", "mercado": "Indisponível"}

# ================= ODDS FUTEBOL (SÓ HOJE) =================
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
                            
                        odds_over_25 = 0
                        
                        for book in g.get('bookmakers', []):
                            for m in book.get('markets', []):
                                if m['key'] == 'totals':
                                    for o in m['outcomes']:
                                        if o['name'] == 'Over' and o.get('point') == 2.5: odds_over_25 = max(odds_over_25, o['price'])

                        jogos.append({
                            "home": g['home_team'], "away": g['away_team'], "match": f"{g['home_team']} x {g['away_team']}",
                            "odd_over_25": odds_over_25, "time": game_time.strftime("%H:%M")
                        })
            except Exception as e:
                logging.error(f"Erro Odds: {e}")
    return jogos

def format_game_analysis(game, ai_data):
    jogador = ai_data.get("jogador", "Desconhecido")
    mercado_ia = ai_data.get("mercado", "Mercado Indisponível")
    
    if jogador.startswith("ERRO"):
        prop = f"⚠️ <b>Falha na Pesquisa:</b> {jogador}"
        mercado_texto = f"📊 <b>Análise:</b> Erro de IA"
    else:
        prop = f"🎯 <b>Player Prop:</b> {jogador} p/ finalizar ou marcar"
        mercado_texto = f"📊 <b>Tendência do Jogo:</b> {mercado_ia}"

    # Adiciona a Odd real se a IA tiver escolhido golos e a casa de apostas tiver valor
    odd_info = ""
    if "Mais de 2.5 Golos" in mercado_ia and game["odd_over_25"] > 0:
        odd_info = f" (@{game['odd_over_25']})"

    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n{mercado_texto}{odd_info}\n"

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V191")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Analisar Grade (IA Multi-Mercados)", callback_data="fut_deep")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V191 ONLINE (Análise Dinâmica de Mercados)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()

    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>A compilar a grelha de hoje...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await status_msg.edit_text("❌ <b>ERRO FATAL:</b> Chave da API de Odds esgotada.")
            return
        if not jogos:
            await status_msg.edit_text("❌ Nenhum jogo oficial programado para HOJE nas ligas ativas.")
            return

        texto_final = "🔥 <b>GRELHA DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        
        total_jogos = len(jogos)
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>IA a analisar características e mercados...</b> ({i}/{total_jogos})\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            # Passa a responsabilidade do mercado para a IA
            dados_ia = await get_ai_analysis_for_match(g['home'], g['away'])
            texto_final += format_game_analysis(g, dados_ia) + "━━━━━━━━━━━━━━━━\n"
            
            if i < total_jogos: await asyncio.sleep(4) 

        await status_msg.edit_text("✅ <b>Análise Concluída!</b> A postar no canal...", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
