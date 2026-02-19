# ================= BOT V212 (SEM FILTROS DE LIGA + CORREÇÃO CAP) =================
import os
import logging
import asyncio
import httpx
import threading
import random
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
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
else:
    model = None

async def fetch_schedule_football_data():
    if not FOOTBALL_DATA_KEY: return "SEM_CHAVE"
    
    headers = {'X-Auth-Token': FOOTBALL_DATA_KEY}
    br_tz = timezone(timedelta(hours=-3))
    
    hoje = datetime.now(br_tz).strftime("%Y-%m-%d")
    amanha = (datetime.now(br_tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    
    url = f"http://api.football-data.org/v4/matches?dateFrom={hoje}&dateTo={amanha}"
    
    jogos_formatados = []
    
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(url, headers=headers)
            if r.status_code != 200: return []
            
            data = r.json()
            matches = data.get('matches', [])
            
            for m in matches:
                # === REMOVIDO FILTRO DE ID DE LIGA ===
                # Agora aceitamos TUDO que a API mandar
                
                status = m['status']
                # Aceita jogos agendados ou rolando
                if status not in ['SCHEDULED', 'TIMED', 'IN_PLAY', 'PAUSED']: continue
                
                home_name = m['homeTeam']['name']
                away_name = m['awayTeam']['name']
                comp_name = m['competition']['name'] # Nome da liga para exibir
                
                # CORREÇÃO DO NOME FEIO DA API
                if home_name == "CA Paranaense": home_name = "Athletico-PR"
                if away_name == "CA Paranaense": away_name = "Athletico-PR"
                
                match_time_utc = datetime.strptime(m['utcDate'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                match_time_br = match_time_utc.astimezone(br_tz)
                
                # Filtra apenas jogos de HOJE no horário BR
                if match_time_br.date() != datetime.now(br_tz).date(): continue
                
                jogos_formatados.append({
                    "match": f"{home_name} x {away_name}",
                    "league": comp_name,
                    "home": home_name,
                    "away": away_name,
                    "time": match_time_br.strftime("%H:%M")
                })
        except:
            return []
            
    return jogos_formatados[:15] # Aumentei o limite pra caber tudo

async def get_player_smart(team_name):
    """
    Usa IA para tudo agora, já que a API provou ter nomes velhos (Mastriani).
    Prompt reforçado para 2026.
    """
    if not model: return "Destaque"

    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")

    prompt = f"""
    Estamos em {data_hoje}. Você é um scout de futebol atualizado.
    Diga o nome do principal ATACANTE TITULAR do {team_name} HOJE.
    
    IMPORTANTE:
    - O Athletico-PR NÃO tem mais Mastriani. O titular é Pablo, Canobbio ou reforço de 2026.
    - O Corinthians tem Yuri Alberto ou Memphis Depay (se ainda estiver).
    - Responda APENAS O NOME.
    """
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip().replace('*', '')
    except:
        if "Athletico" in team_name: return "Canobbio"
        return "Camisa 9"

async def get_market_analysis(home_team, away_team):
    if not model: return "Over 2.5 Gols"
    opcoes = ["Vitória do Mandante", "Vitória do Visitante", "Mais de 8.5 Escanteios", "Mais de 4.5 Cartões", "Over 2.5 Gols", "Ambas Marcam"]
    random.shuffle(opcoes)
    lista = ", ".join(opcoes)
    prompt = f"Analise {home_team} x {away_team}. Escolha o mercado mais provável. Apenas uma opção: {lista}"
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        linha = response.text.strip().replace('*', '')
        return linha if linha in opcoes else "Over 2.5 Gols"
    except:
        return "Over 2.5 Gols"

def format_game_analysis(game, jogador_real, mercado_ia):
    prop = f"🎯 <b>Player Prop:</b> {jogador_real} p/ marcar"
    return f"🏆 <b>{game['league']}</b>\n⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n📊 <b>Tendência:</b> {mercado_ia}\n"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V212")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Ver Grade da API", callback_data="fut_deep")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V212 ONLINE</b>\nMostrando TUDO que a API entregar.", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>Varrendo API sem filtros...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await fetch_schedule_football_data()
        
        if not jogos:
            await status_msg.edit_text("❌ <b>A API retornou 0 jogos para hoje.</b>\n(Infelizmente sua chave não está cobrindo as ligas de hoje).")
            return

        texto_final = f"🔥 <b>JOGOS ENCONTRADOS ({len(jogos)})</b> 🔥\n\n"
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>Processando {i}/{len(jogos)}...</b>\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            jogador = await get_player_smart(g['home'])
            mercado = await get_market_analysis(g['home'], g['away'])
            
            texto_final += format_game_analysis(g, jogador, mercado) + "━━━━━━━━━━━━━━━━\n"
            await asyncio.sleep(4)

        await status_msg.edit_text("✅ <b>Postado!</b>", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
