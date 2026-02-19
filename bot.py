# ================= BOT V222 (TODAS AS LIGAS + FREIO ANTI-BLOQUEIO IA) =================
import os
import logging
import asyncio
import threading
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import httpx
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

# ================= 1. GRADE DA ESPN (AGORA COM RECOPA E OUTRAS LIGAS) =================
async def fetch_espn_hidden_api():
    # Expandido para cobrir os jogos da sua imagem
    leagues = [
        'uefa.europa', 'uefa.conference', 'uefa.champions', 
        'conmebol.libertadores', 'conmebol.sudamericana', 'conmebol.recopa', # FLAMENGO AQUI
        'bra.1', 'bra.camp.paulista', 'bra.camp.carioca', 'bra.camp.mineiro',
        'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1',
        'arg.1', 'ksa.1' # Argentino e Saudita
    ]
    
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=15) as client:
        for league in leagues:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
            try:
                r = await client.get(url)
                if r.status_code != 200: continue
                
                data = r.json()
                league_name = data['leagues'][0].get('name', 'Futebol') if data.get('leagues') else 'Futebol'
                
                for event in data.get('events', []):
                    status = event['status']['type']['state']
                    if status not in ['pre', 'in']: continue
                    
                    competitors = event['competitions'][0]['competitors']
                    team1 = competitors[0]['team']['name']
                    team2 = competitors[1]['team']['name']
                    
                    home = team1 if competitors[0]['homeAway'] == 'home' else team2
                    away = team2 if competitors[1]['homeAway'] == 'away' else team1
                    
                    dt_utc = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                    dt_br = dt_utc.astimezone(br_tz)
                    
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    
                    jogos.append({
                        "id": f"{home}x{away}".replace(" ", "").lower(),
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "time": dt_br.strftime("%H:%M"),
                        "league": league_name
                    })
            except:
                continue
                
    # Ordena por horário e remove duplicatas de ligas cruzadas
    unicos = {j['id']: j for j in jogos}
    lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    
    return lista_final[:20] 

# ================= 2. IA LENTA E PRECISA (EVITA O LIMITE DO GOOGLE E REPETIÇÃO) =================
async def analyze_match_slow(home, away):
    if not model: return {"player": "Artilheiro", "market": "Vitória"}
    
    # O SEGREDO: Pausa de 4.5 segundos antes de perguntar ao Google. 
    # Isso impede que o seu plano gratuito (15 req/min) seja bloqueado e dê "Destaque".
    await asyncio.sleep(4.5)
    
    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")
    
    prompt = f"""
    Como um tipster profissional em {data_hoje}.
    Analise OBRIGATORIAMENTE o jogo: {home} x {away}.
    
    TAREFA 1: Diga APENAS o nome do principal atacante titular do {home}. 
    (Aviso: NÃO use Mastriani no Athletico. Não use jogadores que foram vendidos recentemente. Foco no titular real).
    
    TAREFA 2: Escolha o melhor mercado ESTATÍSTICO.
    REGRA VITAL: É PROIBIDO repetir sempre 'Over 2.5 Gols'. Varie sua resposta de acordo com os times entre:
    [Vitória do {home}, Vitória do {away}, Mais de 8.5 Escanteios, Mais de 4.5 Cartões, Ambas Marcam Sim, Menos de 2.5 Gols].
    
    Responda EXATAMENTE neste formato (Sem introduções):
    NOME DO JOGADOR | MERCADO
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip().replace('*', '').replace('`', '')
        
        if "|" in text:
            parts = text.split("|")
            return {"player": parts[0].strip(), "market": parts[1].strip()}
        else:
            return {"player": "Atacante Principal", "market": "Ambas Marcam Sim"}
    except Exception as e:
        logging.error(f"Erro IA: {e}")
        # Se mesmo assim der erro, varia o mercado pra não ficar 2.5 gols infinito
        mercado_reserva = random.choice(["Mais de 8.5 Escanteios", "Ambas Marcam Sim", "Mais de 4.5 Cartões"])
        jogador_reserva = "Canobbio" if "Athletico" in home else "Craque da Equipe"
        return {"player": jogador_reserva, "market": mercado_reserva}

# ================= 3. FORMATAÇÃO E SERVIDOR =================
def format_game(game, analysis):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🎯 <b>Prop:</b> {analysis['player']} p/ marcar\n"
        f"📊 <b>Tendência:</b> {analysis['market']}\n"
    )

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V222 FINAL")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Gerar Grade Completa", callback_data="fut_deep")]])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>BOT V222 ONLINE</b>\nTodas as Ligas + IA Ajustada.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "fut_deep":
        msg = await q.message.reply_text("🔎 <b>Buscando jogos (Incluindo Recopa e Estaduais)...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await fetch_espn_hidden_api()
        
        if not jogos:
            await msg.edit_text("❌ <b>Nenhum jogo retornado.</b>")
            return

        txt = f"🔥 <b>GRADE COMPLETA ({len(jogos)} Jogos)</b> 🔥\n\n"
        
        for i, g in enumerate(jogos, 1):
            # O aviso agora mostra que vai demorar um pouco para não dar pau na IA
            await msg.edit_text(f"⏳ <b>Analisando jogo {i}/{len(jogos)}...</b>\n👉 <i>{g['match']}</i>\n(Processando com calma para evitar bloqueios da IA)", parse_mode=ParseMode.HTML)
            
            # Aqui a chamada tem o freio de segurança de 4.5 segundos embutido
            analysis = await analyze_match_slow(g['home'], g['away'])
            txt += format_game(g, analysis) + "━━━━━━━━━━━━━━━━\n"

        await msg.edit_text("✅ <b>Grade Finalizada com Sucesso!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
