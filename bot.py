# ================= BOT V192 (DADOS REAIS, ODDS H2H & MERCADOS VARIADOS) =================
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

# ================= IA - TIPSTER PROFISSIONAL (COM REGRA DE 2026 ATIVA) =================
async def get_ai_analysis_for_match(home_team, away_team):
    if not model: 
        return {"jogador": "ERRO: Chave GEMINI_API_KEY ausente", "mercado": "ERRO"}

    br_tz = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(br_tz).strftime("%B de %Y")

    # A instrução rigorosa para dados reais e mercados variados
    prompt = f"""
    Sempre antes de me entregar as análises, faça uma pesquisa interna vigorosa sobre os jogadores no mês atual que estamos ({data_hoje}).
    Considere todas as transferências atualizadas (Ex: Giroud não está no Milan).
    
    Analise o confronto real: {home_team} x {away_team}.
    Forneça exatamente 2 informações, separadas por uma barra vertical (|).
    
    1: O nome do artilheiro ATUAL (que ainda joga no clube hoje).
    2: O mercado MAIS PROVÁVEL. VOCÊ DEVE VARIAR SUA ESCOLHA de acordo com a força dos times. Escolha UMA destas opções exatas:
    - Vitória do {home_team} (Se for favorito)
    - Vitória do {away_team} (Se for favorito)
    - Ambas Marcam Sim (Se os dois tiverem bom ataque)
    - Mais de 8.5 Escanteios (Se usarem muita jogada de linha de fundo)
    - Mais de 4.5 Cartões (Se for um jogo pegado ou clássico)
    - Over 2.5 Gols
    
    Responda APENAS: Jogador | Mercado
    """
    try:
        response = await model.generate_content_async(prompt)
        linha = response.text.strip().replace('*', '').replace('`', '').replace('"', '').split('\n')[0]
        
        logging.info(f"🧠 RESPOSTA IA: {linha}")
        
        if "|" in linha:
            parts = linha.split("|")
            return {"jogador": parts[0].strip(), "mercado": parts[1].strip()}
        else:
            return {"jogador": linha[:30], "mercado": "Mais de 8.5 Escanteios"}
            
    except Exception as e:
        return {"jogador": f"ERRO API", "mercado": "Ambas Marcam Sim"}

# ================= ODDS FUTEBOL (PUXANDO VITÓRIA E GOLS REAIS) =================
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
                        
                        # Extrai odds Reais de Match Winner (Vitória) e Totais
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
    
    prop = f"🎯 <b>Player Prop:</b> {jogador} p/ marcar"

    # Lógica de cruzamento: A IA escolheu o mercado, o Python puxa a ODD REAL daquele mercado
    mercado_final = f"📊 <b>Tendência do Jogo:</b> {mercado_ia}"
    
    if "Vitória do " + game['home'] in mercado_ia and game['odd_home'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['home']} (@{game['odd_home']})"
    elif "Vitória do " + game['away'] in mercado_ia and game['odd_away'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['away']} (@{game['odd_away']})"
    elif "Over 2.5" in mercado_ia and game['odd_over_25'] > 0:
        mercado_final = f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{game['odd_over_25']})"
    elif "Escanteios" in mercado_ia:
        mercado_final = f"🚩 <b>Estatística:</b> Média Alta de Escanteios (+8.5)"
    elif "Cartões" in mercado_ia:
        mercado_final = f"🟨 <b>Estatística:</b> Jogo pegado (+4.5 Cartões)"

    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n{mercado_final}\n"

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - DVD TIPS V192")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Analisar Grade (Dados Reais 2026)", callback_data="fut_deep")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦁 <b>BOT V192 ONLINE (Mercados Variados)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()

    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>A compilar a grade com dados de Vencedor/Gols/Cartões...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if jogos == "COTA_EXCEDIDA":
            await status_msg.edit_text("❌ <b>ERRO FATAL:</b> Chave da API de Odds esgotada.")
            return
        if not jogos:
            await status_msg.edit_text("❌ Nenhum jogo oficial programado para HOJE nas ligas ativas.")
            return

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        
        total_jogos = len(jogos)
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>IA analisando transferências 2026 e mercados...</b> ({i}/{total_jogos})\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            dados_ia = await get_ai_analysis_for_match(g['home'], g['away'])
            texto_final += format_game_analysis(g, dados_ia) + "━━━━━━━━━━━━━━━━\n"
            
            if i < total_jogos: await asyncio.sleep(4) 

        await status_msg.edit_text("✅ <b>Análise Concluída!</b> Postando no canal...", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=texto_final, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
