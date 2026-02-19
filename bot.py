# ================= BOT V220 (API OCULTA ESPN - SEM CHAVE, SEM LIMITE) =================
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

# ================= API PUBLICA ESPN (A MINA DE OURO) =================
async def fetch_espn_hidden_api():
    """
    Consome o endpoint público do app da ESPN.
    Zero chaves. Zero limites. Retorna JSON puro.
    """
    # Códigos internos da ESPN para as ligas que importam
    leagues = [
        'uefa.europa', 'uefa.conference', 'uefa.champions', 
        'conmebol.libertadores', 'conmebol.sudamericana', 
        'bra.1', 'bra.2', # Brasileirão A e B
        'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1'
    ]
    
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=15) as client:
        for league in leagues:
            # Rota direta de dados da ESPN
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
            
            try:
                r = await client.get(url)
                if r.status_code != 200: continue
                
                data = r.json()
                
                # Nome da liga
                league_name = "Futebol"
                if data.get('leagues') and len(data['leagues']) > 0:
                    league_name = data['leagues'][0].get('name', 'Futebol')
                
                events = data.get('events', [])
                for event in events:
                    # 'pre' = Agendado, 'in' = Rolando
                    status = event['status']['type']['state']
                    if status not in ['pre', 'in']: continue
                    
                    # Extrai os times com segurança
                    competitors = event['competitions'][0]['competitors']
                    
                    # ESPN as vezes inverte a ordem no JSON, checamos quem é 'home'
                    team1 = competitors[0]['team']['name']
                    team2 = competitors[1]['team']['name']
                    
                    home = team1 if competitors[0]['homeAway'] == 'home' else team2
                    away = team2 if competitors[1]['homeAway'] == 'away' else team1
                    
                    # Horário
                    dt_utc = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                    dt_br = dt_utc.astimezone(br_tz)
                    
                    # Só adiciona se for jogo de HOJE (horário de Brasília)
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    
                    jogos.append({
                        "id": f"{home}x{away}".replace(" ", "").lower(),
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "time": dt_br.strftime("%H:%M"),
                        "league": league_name
                    })
            except Exception as e:
                logging.error(f"Erro ao ler liga {league}: {e}")
                continue
                
    # Ordena os jogos por horário
    jogos.sort(key=lambda x: x['time'])
    
    return jogos[:20] # Limita a 20 para o Telegram não cortar a mensagem

# ================= IA - TIPSTER ESTATÍSTICO =================
async def analyze_match(home, away):
    if not model: return {"player": "Destaque", "market": "Over 2.5 Gols"}
    
    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")
    
    prompt = f"""
    Como um tipster profissional (Data: {data_hoje}):
    Analise o jogo: {home} x {away}.
    
    1. Informe APENAS o nome do principal ATACANTE titular do {home}. 
       (Cuidado extremo com jogadores transferidos recentemente. Seja preciso).
    2. Escolha APENAS UMA opção lógica de mercado:
       [Vitória do Mandante, Vitória do Visitante, +8.5 Escanteios, +4.5 Cartões, Over 2.5 Gols, Ambas Marcam Sim].
       
    Responda EXATAMENTE neste formato: JOGADOR | MERCADO
    Sem enrolação, sem notas.
    """
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip().replace('*', '')
        if "|" in text:
            parts = text.split("|")
            return {"player": parts[0].strip(), "market": parts[1].strip()}
        return {"player": "Camisa 9", "market": "Over 2.5 Gols"}
    except:
        return {"player": "Destaque", "market": "Over 2.5 Gols"}

def format_game(game, analysis):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🎯 <b>Prop:</b> {analysis['player']} p/ marcar\n"
        f"📊 <b>Tendência:</b> {analysis['market']}\n"
    )

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V220 ESPN API")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Gerar Grade Completa", callback_data="fut_deep")]])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>BOT V220 ONLINE</b>\nConectado na rota direta da ESPN. Sem limites.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "fut_deep":
        msg = await q.message.reply_text("🔎 <b>Varrendo ligas na ESPN...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await fetch_espn_hidden_api()
        
        if not jogos:
            await msg.edit_text("❌ <b>Nenhum jogo retornado.</b> (Provavelmente não há jogos das grandes ligas programados para o resto do dia).")
            return

        txt = f"🔥 <b>SUPER GRADE DE FUTEBOL ({len(jogos)})</b> 🔥\n\n"
        for i, g in enumerate(jogos, 1):
            await msg.edit_text(f"⏳ <b>Analisando {i}/{len(jogos)}...</b>\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            analysis = await analyze_match(g['home'], g['away'])
            txt += format_game(g, analysis) + "━━━━━━━━━━━━━━━━\n"
            
            # Pausa curta apenas para não estourar a cota da IA do Google
            await asyncio.sleep(2)

        await msg.edit_text("✅ <b>Grade Finalizada!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
