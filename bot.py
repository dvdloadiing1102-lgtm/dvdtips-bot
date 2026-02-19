# ================= BOT V207 (FILTRO GEOGRÁFICO INTELIGENTE) =================
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
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # Voltando ao 1.5 Flash que é mais estável para evitar alucinações de formato
    model = genai.GenerativeModel("gemini-1.5-flash")
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

# ================= FOOTBALL-DATA.ORG (SÓ PARA EUROPA) =================
TEAM_CACHE = {} 
# Ligas da Europa (ONDE A API É BOA)
LIGAS_EUROPA = [2021, 2014, 2019, 2002, 2015, 2001] 
# Ligas do Brasil (ONDE A API É RUIM - REMOVIDAS DA LISTA DE CARGA)

async def mapear_times_startup():
    if not FOOTBALL_DATA_KEY: return
    headers = {'X-Auth-Token': FOOTBALL_DATA_KEY}
    
    async with httpx.AsyncClient(timeout=20) as client:
        # Só mapeia Europa. Brasil deixa pra IA.
        for liga_id in LIGAS_EUROPA:
            try:
                url = f"http://api.football-data.org/v4/competitions/{liga_id}/teams"
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    for time in data.get('teams', []):
                        TEAM_CACHE[time['name']] = time['id']
                await asyncio.sleep(6)
            except Exception:
                pass

async def get_player_strategy(team_name, league_code):
    """
    CÉREBRO DO BOT:
    - Se for Europa: Usa API (Precisa e Rápida).
    - Se for Brasil/Outros: Usa IA (Atualizada, evita API velha).
    """
    
    # 1. ESTRATÉGIA EUROPA (API)
    # Verifica se a liga é europeia (soccer_epl, soccer_spain, etc)
    is_europe = any(x in league_code for x in ['epl', 'la_liga', 'serie_a', 'bundesliga', 'champs_league'])
    
    if is_europe and FOOTBALL_DATA_KEY and team_name in TEAM_CACHE:
        team_id = TEAM_CACHE[team_name]
        headers = {'X-Auth-Token': FOOTBALL_DATA_KEY}
        url = f"http://api.football-data.org/v4/teams/{team_id}"
        
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    squad = data.get('squad', [])
                    atacantes = [p['name'] for p in squad if p.get('position') in ['Offence', 'Forward', 'Attacker']]
                    if not atacantes: atacantes = [p['name'] for p in squad if p.get('position') == 'Midfield']
                    
                    if atacantes: return atacantes[0] # Retorna API
            except:
                pass

    # 2. ESTRATÉGIA BRASIL/RESTO (IA PURA BLINDADA)
    if model:
        try:
            br_tz = timezone(timedelta(hours=-3))
            data_hoje = datetime.now(br_tz).strftime("%B de %Y")
            
            prompt = f"""
            Você é um especialista em transferências atualizado em {data_hoje}.
            Diga o nome do principal ARTILHEIRO TITULAR do time: {team_name}.
            
            ATENÇÃO CRÍTICA:
            - Muitos jogadores mudaram de time recentemente no Brasil.
            - Verifique se o jogador NÃO foi transferido (Ex: Mastriani saiu do Athletico, não cite ele).
            - Responda APENAS o nome do jogador atual.
            """
            response = await asyncio.to_thread(model.generate_content, prompt)
            return response.text.strip().replace('*', '')
        except:
            return "Destaque do Time"
            
    return "Craque da Equipe"

# ================= IA - MERCADO ESTATÍSTICO =================
async def get_market_analysis(home_team, away_team):
    if not model: return "Over 2.5 Gols"
    opcoes = [f"Vitória do {home_team}", f"Vitória do {away_team}", "Mais de 8.5 Escanteios", "Mais de 4.5 Cartões", "Over 2.5 Gols", "Ambas Marcam Sim"]
    random.shuffle(opcoes)
    lista_opcoes = ", ".join(opcoes)
    prompt = f"Analise {home_team} x {away_team}. Escolha o melhor mercado estatístico. Responda APENAS UMA opção: {lista_opcoes}."
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        linha = response.text.strip()
        return linha if linha in opcoes else "Over 2.5 Gols"
    except:
        return "Over 2.5 Gols"

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
                if isinstance(data, dict) and "quota" in str(data): return "COTA_EXCEDIDA"
                if isinstance(data, list):
                    for g in data:
                        game_time = datetime.fromisoformat(g['commence_time'].replace('Z', '+00:00')).astimezone(br_tz)
                        
                        # Filtra apenas jogos de HOJE (ou madrugada de hoje para amanhã cedo)
                        if game_time.date() != hoje:
                            continue
                            
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
                            "league_code": league, # Passamos o código da liga para saber se é BR ou Europa
                            "odd_home": round(odd_home, 2), "odd_away": round(odd_away, 2), "odd_over_25": round(odd_over_25, 2),
                            "time": game_time.strftime("%H:%M")
                        })
            except Exception:
                pass
    return jogos

def format_game_analysis(game, jogador_real, mercado_ia):
    prop = f"🎯 <b>Player Prop:</b> {jogador_real} p/ marcar"
    mercado_final = f"📊 <b>Tendência do Jogo:</b> {mercado_ia}"
    if "Vitória do " + game['home'] in mercado_ia and game['odd_home'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['home']} (@{game['odd_home']})"
    elif "Vitória do " + game['away'] in mercado_ia and game['odd_away'] > 0:
        mercado_final = f"💰 <b>Vencedor:</b> {game['away']} (@{game['odd_away']})"
    elif "Over 2.5" in mercado_ia and game['odd_over_25'] > 0:
        mercado_final = f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{game['odd_over_25']})"
    
    return f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n{prop}\n{mercado_final}\n"

# ================= SERVER E MAIN =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V207")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Futebol (Híbrido)", callback_data="fut_deep")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asyncio.create_task(mapear_times_startup())
    await update.message.reply_text("🦁 <b>BOT V207 ONLINE (Brasil via IA / Europa via API)</b>", reply_markup=get_main_menu(), parse_mode=ParseMode.HTML)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "fut_deep":
        status_msg = await q.message.reply_text("🔎 <b>Analisando grade...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_games()
        
        if not jogos:
            await status_msg.edit_text("❌ Grade vazia.")
            return

        texto_final = "🔥 <b>GRADE DE FUTEBOL (SÓ HOJE)</b> 🔥\n\n"
        for i, g in enumerate(jogos, 1):
            await status_msg.edit_text(f"⏳ <b>Analisando ({i}/{len(jogos)})...</b>\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            # Aqui está o segredo: passamos o código da liga
            jogador = await get_player_strategy(g['home'], g['league_code'])
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
