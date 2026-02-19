# ================= BOT V209 (FOOTBALL-DATA.ORG PURO + IA HÍBRIDA) =================
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
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY") # SUA CHAVE DA FOOTBALL-DATA.ORG
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # Usando o Flash 2.5 que é mais inteligente para escalar times brasileiros
    model = genai.GenerativeModel("gemini-2.5-flash")
else:
    model = None

# ================= BUSCADOR DE JOGOS (FOOTBALL-DATA.ORG) =================
# IDs das Ligas suportadas no plano Free da sua API
# 2013: Brasileirão | 2021: Premier League | 2001: Champions | 2146: Europa League | 2154: Conference
COMPETITION_IDS = [2013, 2021, 2001, 2146, 2154, 2014, 2015, 2002, 2019]

async def fetch_schedule_football_data():
    if not FOOTBALL_DATA_KEY: return "SEM_CHAVE"
    
    headers = {'X-Auth-Token': FOOTBALL_DATA_KEY}
    
    # Pega jogos de hoje e amanhã (para cobrir fuso horário e jogos da noite)
    br_tz = timezone(timedelta(hours=-3))
    hoje = datetime.now(br_tz).strftime("%Y-%m-%d")
    amanha = (datetime.now(br_tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    
    url = f"http://api.football-data.org/v4/matches?dateFrom={hoje}&dateTo={amanha}"
    
    jogos_formatados = []
    
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(url, headers=headers)
            
            if r.status_code != 200:
                logging.error(f"Erro API Schedule: {r.status_code}")
                return []
                
            data = r.json()
            matches = data.get('matches', [])
            
            for m in matches:
                comp_id = m['competition']['id']
                
                # Filtra só as ligas importantes
                if comp_id not in COMPETITION_IDS: continue
                
                # Status do jogo (SCHEDULED ou TIMED)
                if m['status'] not in ['SCHEDULED', 'TIMED']: continue
                
                home_name = m['homeTeam']['name']
                away_name = m['awayTeam']['name']
                home_id = m['homeTeam']['id']
                match_time_utc = datetime.strptime(m['utcDate'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                match_time_br = match_time_utc.astimezone(br_tz)
                
                # Filtra apenas jogos que são HOJE no horário de Brasília
                if match_time_br.date() != datetime.now(br_tz).date(): continue
                
                jogos_formatados.append({
                    "match": f"{home_name} x {away_name}",
                    "home": home_name,
                    "away": away_name,
                    "home_id": home_id,
                    "comp_id": comp_id, # Importante para saber se é Brasil ou Europa
                    "time": match_time_br.strftime("%H:%M")
                })
                
        except Exception as e:
            logging.error(f"Erro ao buscar jogos: {e}")
            return []
            
    return jogos_formatados[:10] # Limite de segurança

# ================= BUSCA HÍBRIDA DE JOGADOR =================
async def get_squad_player(team_id, team_name, comp_id):
    """
    AQUI TÁ A MÁGICA:
    - Se for Brasil (ID 2013) ou América: USA IA (pra não vir Mastriani no CAP).
    - Se for Europa: Usa a API (que é atualizada).
    """
    
    # Lista de IDs da Europa onde a API é confiável
    EUROPE_IDS = [2021, 2001, 2146, 2154, 2014, 2015, 2002, 2019]
    
    # === ROTA 1: EUROPA (Usa API) ===
    if comp_id in EUROPE_IDS and FOOTBALL_DATA_KEY:
        headers = {'X-Auth-Token': FOOTBALL_DATA_KEY}
        url = f"http://api.football-data.org/v4/teams/{team_id}"
        
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    squad = data.get('squad', [])
                    # Pega atacantes
                    atacantes = [p['name'] for p in squad if p.get('position') in ['Offence', 'Forward', 'Attacker']]
                    if atacantes: return atacantes[0]
            except:
                pass # Se falhar, cai pra IA
    
    # === ROTA 2: BRASIL/OUTROS (Usa IA Blindada) ===
    # Aqui a gente proíbe a IA de alucinar transferências antigas
    if model:
        br_tz = timezone(timedelta(hours=-3))
        data_hoje = datetime.now(br_tz).strftime("%B de %Y")
        
        prompt = f"""
        Você é um especialista em futebol brasileiro e sul-americano. Data: {data_hoje} (2026).
        Diga o nome do principal ATACANTE TITULAR do time: {team_name}.
        
        REGRA DE OURO:
        - Verifique transferências recentes. Se o jogador saiu do clube em 2024 ou 2025, NÃO o cite.
        - Exemplo: Mastriani saiu do Athletico-PR. Não cite ele.
        - Exemplo: Gabigol saiu do Flamengo (se for o caso).
        
        Responda APENAS o nome do jogador. Sem frases.
        """
        try:
            response = await asyncio.to_thread(model.generate_content, prompt)
            return response.text.strip().replace('*', '')
        except:
            return "Craque da Equipe"
            
    return "Destaque do Time"

# ================= IA - MERCADO ESTATÍSTICO =================
async def get_market_analysis(home_team, away_team):
    if not model: return "Over 2.5 Gols"
    
    # Embaralha pra não viciar
    opcoes = [f"Vitória do {home_team}", f"Vitória do {away_team}", "Mais de 8.5 Escanteios", "Mais de 4.5 Cartões", "Over 2.5 Gols", "Ambas Marcam Sim"]
    random.shuffle(opcoes)
    lista_opcoes = ", ".join(opcoes)
    
    prompt = f"""
    Analise o jogo {home_team} x {away_team}.
    Escolha o melhor mercado estatístico.
    Responda APENAS com UMA dessas opções: {lista_opcoes}.
    """
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        linha = response.text.strip().replace('*', '')
        return linha if linha in opcoes else "Over 2.5 Gols"
    except:
        return "Over 2.5 Gols"

def format_game_analysis(game, jogador_real, mercado_ia):
    prop = f"🎯 <b>Player Prop:</b> {jogador_real} p/ marcar"
    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n📊 <b>Tendência:</b> {mercado_ia}\n"

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V209")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Futebol (Sua API + Correção BR)", callback_data="fut_deep")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V209 ONLINE</b>\nUsando sua API nova e corrigindo o Brasileirão.", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>Buscando jogos na sua API...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await fetch_schedule_football_data()
        
        if jogos == "SEM_CHAVE":
            await status_msg.edit_text("❌ <b>Erro:</b> Variável FOOTBALL_DATA_KEY não encontrada.")
            return
            
        if not jogos:
            await status_msg.edit_text("❌ <b>Grade Vazia.</b> Nenhum jogo das ligas principais encontrado para hoje nessa API.")
            return

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>Analisando jogo {i}/{len(jogos)}...</b>\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            # Aqui a mágica: manda o ID da competição pra saber se usa API ou IA
            jogador = await get_squad_player(g['home_id'], g['home'], g['comp_id'])
            
            mercado = await get_market_analysis(g['home'], g['away'])
            
            texto_final += format_game_analysis(g, jogador, mercado) + "━━━━━━━━━━━━━━━━\n"
            
            # Pausa de 6s pra não estourar os 10 req/min da sua API
            await asyncio.sleep(6)

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
