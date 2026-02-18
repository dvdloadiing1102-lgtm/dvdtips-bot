# ================= BOT V188 (PAINEL DE VIDRO - SEM ESCONDER ERROS) =================
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
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

# ================= IA - EXTRATOR (COM EXPOSIÇÃO DE ERROS) =================
async def get_player_for_single_match(home_team, away_team):
    if not model: 
        return "ERRO: Chave GEMINI_API_KEY faltando no Render!"

    # Comando seco e direto, sem a palavra "atualmente" para não acionar a trava da IA
    prompt = f"""
    Identifique apenas um atacante ou artilheiro famoso que joga no {home_team} ou no {away_team} na temporada atual.
    Responda EXCLUSIVAMENTE com o nome e sobrenome do jogador. 
    NÃO escreva introduções, NÃO escreva ponto final, NÃO peça desculpas. Apenas o nome.
    """
    try:
        # Usando a função assíncrona nativa para não dar timeout
        response = await model.generate_content_async(prompt)
        linha = response.text.strip().replace('*', '').replace('`', '').replace('"', '').split('\n')[0]
        
        # Se a IA soltar textão de desculpa, o bot te mostra exatamente o que ela falou
        if len(linha) > 35 or "modelo" in linha.lower() or "desculp" in linha.lower() or "não tenho" in linha.lower():
            return f"ERRO IA: {linha[:40]}..."
            
        return linha
    except Exception as e:
        return f"ERRO API DO GOOGLE: {str(e)[:30]}"

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
                            
                        odds_over_25 = 0; odds_over_15 = 0
                        
                        for book in g.get('bookmakers', []):
                            for m in book.get('markets', []):
                                if m['key'] == 'totals':
                                    for o in m['outcomes']:
                                        if o['name'] == 'Over' and o.get('point') == 2.5: odds_over_25 = max(odds_over_25, o['price'])
                                        if o['name'] == 'Over' and o.get('point') == 1.5: odds_over_15 = max(odds_over_15, o['price'])

                        jogos.append({
                            "home": g['home_team'], "away": g['away_team'], "match": f"{g['home_team']} x {g['away_team']}",
                            "odd_over_25": odds_over_25, "odd_over_15": odds_over_15, "time": game_time.strftime("%H:%M")
                        })
            except Exception as e:
                logging.error(f"Erro Odds: {e}")
    return jogos

def format_game_analysis(game, player_star):
    # O PAINEL DE VIDRO: Mostra o erro real se a IA falhar
    if player_star.startswith("ERRO"):
        prop = f"⚠️ <b>Falha na Pesquisa:</b> {player_star}"
    else:
        prop = f"🎯 <b>Player Prop:</b> {player_star} p/ finalizar ou marcar"

    if game["odd_over_25"] > 0 and 1.40 <= game["odd_over_25"] <= 1.95:
        gols_text = f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{game['odd_over_25']})"
    elif game["odd_over_15"] > 0 and 1.25 <= game["odd_over_15"] <= 1.55:
        gols_text = f"🥅 <b>Mercado:</b> Over 1.5 Gols (@{game['odd_over_15']})"
    else:
        gols_text = "⚔️ <b>Mercado:</b> Ambas Marcam Sim"

    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n{gols_text}\n"

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V188")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Analisar Grade (Deep Scan)", callback_data="fut_deep")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V188 ONLINE (Painel de Vidro Ativado)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()

    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>Coletando grade do dia...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await status_msg.edit_text("❌ <b>ERRO FATAL:</b> Chave da API de Odds sem limite.")
            return
        if not jogos:
            await status_msg.edit_text("❌ Nenhum jogo oficial programado para HOJE.")
            return

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        
        total_jogos = len(jogos)
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>Extraindo artilheiro...</b> ({i}/{total_jogos})\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            # Passa os nomes separados para a IA entender melhor
            craque = await get_player_for_single_match(g['home'], g['away'])
            texto_final += format_game_analysis(g, craque) + "━━━━━━━━━━━━━━━━\n"
            
            if i < total_jogos: await asyncio.sleep(4) # Pausa de 4s para evitar bloqueio do Google

        await status_msg.edit_text("✅ <b>Análise Concluída!</b> Postando...", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
