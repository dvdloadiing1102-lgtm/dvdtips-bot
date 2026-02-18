# ================= BOT V183 (ANÁLISE PROFUNDA / DEEP SCAN) =================
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

# ================= IA - DEEP SCAN JOGO A JOGO =================
async def get_player_for_single_match(match_name):
    """Analisa UM ÚNICO jogo por vez com tempo de sobra para a IA raciocinar."""
    if not model: return ""

    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")

    # A sua instrução oficial embutida no prompt
    prompt = f"""
    Sempre antes de me entregar as análises, faça uma pesquisa no Google sobre os jogadores no mês atual que estamos ({data_hoje}).
    O jogo de hoje é: {match_name}.
    Me diga APENAS o nome do melhor jogador de ataque ou artilheiro em boa fase (de qualquer um dos times).
    Certifique-se de que ele NÃO está lesionado e é titular.
    Responda APENAS o nome e sobrenome do jogador, sem pontos, sem explicações.
    """
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        jogador = response.text.strip().replace('*', '').replace('`', '')
        # Validação simples: se a IA viajou e mandou um texto longo, a gente descarta
        if len(jogador) > 30 or "\n" in jogador:
            return ""
        return jogador
    except Exception as e:
        logging.error(f"❌ Erro na IA para {match_name}: {e}")
        return ""

# ================= ODDS FUTEBOL REAIS (SÓ HOJE) =================
async def fetch_games():
    if not ODDS_KEY: return "SEM_CHAVE"
    leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_uefa_champs_league", "soccer_brazil_campeonato"]
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
                            "match": f"{g['home_team']} x {g['away_team']}",
                            "odd_over_25": odds_over_25, "odd_over_15": odds_over_15, "time": game_time.strftime("%H:%M")
                        })
                        
            except Exception as e:
                logging.error(f"Erro na API Odds: {e}")
    return jogos

def format_game_analysis(game, player_star):
    if player_star:
        prop = f"🎯 <b>Player Prop:</b> {player_star} p/ finalizar no alvo ou marcar"
    else:
        prop = "⚠️ <b>Aviso:</b> Sem props de jogadores claros para este jogo."

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
                    if game_time.date() != hoje: continue
                    jogos.append({"match": f"{g['home_team']} x {g['away_team']}"})
        except Exception as e:
            logging.error(f"Erro NBA: {e}")
    return jogos

# ================= SERVER =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V183")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= TELEGRAM E MENU =================
def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Futebol (Análise Profunda)", callback_data="fut_deep")],
        [InlineKeyboardButton("🏀 NBA (Só Hoje)", callback_data="nba")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V183 ONLINE (Motor de Análise Profunda)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "fut_deep":
        # Avisa que começou
        status_msg = await q.message.reply_text("🔎 <b>Coletando grade do dia...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await status_msg.edit_text("❌ <b>ERRO FATAL:</b> Chave da API sem limite.")
            return
        if not jogos:
            await status_msg.edit_text("❌ Nenhum jogo oficial programado para HOJE nas ligas configuradas.")
            return

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        
        # O LOOP DA ANÁLISE PROFUNDA (Com progresso na tela)
        total_jogos = len(jogos)
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>Análise Profunda em andamento...</b>\n\nPesquisando jogador atualizado no Google para o jogo {i} de {total_jogos}:\n👉 <i>{g['match']}</i>\n\n(Pausa de segurança ativada para não bloquear a API)", parse_mode=ParseMode.HTML)
            
            # Pesquisa 1 jogo por vez
            craque = await get_player_for_single_match(g['match'])
            
            # Formata e guarda
            msg = format_game_analysis(g, craque)
            texto_final += msg + "━━━━━━━━━━━━━━━━\n"
            
            # Respira por 10 segundos antes do próximo jogo (O Segredo do sucesso)
            if i < total_jogos:
                await asyncio.sleep(10)

        # Finaliza e manda pro canal
        await status_msg.edit_text("✅ <b>Análise Profunda Concluída com sucesso!</b> Postando no canal...", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Buscando NBA (Só Hoje)...</b>", parse_mode=ParseMode.HTML)
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

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
