# ================= BOT V230 (O ESTRATEGISTA: MERCADO CEDO + JOGADOR CONFIRMADO TARDE) =================
import os
import logging
import asyncio
import threading
import random
import httpx
import feedparser
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# ================= MEMÓRIA GLOBAL =================
TODAYS_GAMES = []
ALERTED_GAMES = set()

# ================= 1. MÓDULOS AUXILIARES (NBA/NEWS) =================
async def fetch_news():
    feeds = ["https://ge.globo.com/rss/ge/futebol/", "https://rss.uol.com.br/feed/esporte.xml"]
    noticias = []
    try:
        for url in feeds:
            feed = await asyncio.to_thread(feedparser.parse, url)
            for entry in feed.entries[:2]:
                noticias.append(f"📰 <b>{entry.title}</b>\n🔗 <a href='{entry.link}'>Ler mais</a>")
    except: pass
    return noticias

async def news_loop(app: Application):
    while True:
        await asyncio.sleep(10800) # 3h
        noticias = await fetch_news()
        if noticias:
            try:
                await app.bot.send_message(chat_id=CHANNEL_ID, text="🗞️ <b>GIRO DE NOTÍCIAS</b> 🗞️\n\n" + "\n\n".join(noticias), parse_mode=ParseMode.HTML)
            except: pass

async def fetch_nba_schedule():
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    jogos = []
    br_tz = timezone(timedelta(hours=-3))
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                for event in r.json().get('events', []):
                    if event['status']['type']['state'] not in ['pre', 'in']: continue
                    comp = event['competitions'][0]['competitors']
                    home = comp[0]['team']['name'] if comp[0]['homeAway'] == 'home' else comp[1]['team']['name']
                    away = comp[1]['team']['name'] if comp[1]['homeAway'] == 'away' else comp[0]['team']['name']
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    jogos.append(f"🏀 <b>{dt_br.strftime('%H:%M')}</b> | {away} @ {home}")
        except: pass
    return jogos

# ================= 2. MÓDULO FUTEBOL (INTELIGÊNCIA HÍBRIDA) =================
async def fetch_espn_soccer():
    """Baixa a grade e salva na memória"""
    leagues = ['uefa.europa', 'uefa.champions', 'conmebol.libertadores', 'conmebol.recopa', 'bra.1', 'bra.camp.paulista', 'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1', 'arg.1', 'ksa.1']
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
                    if event['status']['type']['state'] not in ['pre', 'in']: continue
                    comp = event['competitions'][0]['competitors']
                    home = comp[0]['team']['name'] if comp[0]['homeAway'] == 'home' else comp[1]['team']['name']
                    away = comp[1]['team']['name'] if comp[1]['homeAway'] == 'away' else comp[0]['team']['name']
                    dt_br = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    jogos.append({"id": event['id'], "league_code": league, "match": f"{home} x {away}", "home": home, "away": away, "time": dt_br.strftime("%H:%M"), "league": league_name})
            except: continue
                
    unicos = {j['match']: j for j in jogos}
    lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    
    global TODAYS_GAMES
    TODAYS_GAMES = lista_final[:20]
    return TODAYS_GAMES

async def analyze_game_market(league_code, event_id):
    """
    Analisa APENAS probabilidades e estatísticas.
    Usado para a grade da manhã (sem inventar jogador).
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    prob_home = prob_away = 0.0
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if 'predictor' in data and 'homeChance' in data['predictor']:
                    prob_home = float(data['predictor']['homeChance'])
                    prob_away = float(data['predictor']['awayChance'])
    except: pass
    
    # Lógica de Mercado Segura
    if prob_home >= 60.0:
        dica_principal = f"Vitória do Mandante ({prob_home:.0f}%)"
        dica_seguranca = "Casa vence ou Empate"
    elif prob_away >= 60.0:
        dica_principal = f"Vitória do Visitante ({prob_away:.0f}%)"
        dica_seguranca = "Fora vence ou Empate"
    elif prob_home >= 40.0 and prob_away >= 30.0:
        dica_principal = "Ambas Marcam: Sim"
        dica_seguranca = "Over 1.5 Gols"
    else:
        dica_principal = "Over 1.5 Gols"
        dica_seguranca = "Mais de 8.5 Escanteios"
        
    return dica_principal, dica_seguranca

async def get_confirmed_lineup(league_code, event_id):
    """
    Tenta pegar a escalação OFICIAL.
    Retorna o primeiro atacante encontrado ou None.
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if 'rosters' in data and len(data['rosters']) > 0:
                    for player in data['rosters'][0].get('roster', []):
                        # Pega atacante titular
                        if player.get('position', {}).get('name', '').lower() in ['forward', 'atacante', 'striker']:
                            return player.get('athlete', {}).get('displayName')
    except: pass
    return None

# ================= 3. FORMATAÇÃO INTELIGENTE =================
def format_morning_game(game, d1, d2):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🔥 <b>Oportunidade:</b> {d1}\n"
        f"🛡️ <b>Segurança:</b> {d2}\n"
    )

def format_sniper_game(game, jogador, d1):
    return (
        f"⚔️ <b>{game['match']}</b> ({game['time']})\n"
        f"🚨 <b>ESCALAÇÃO CONFIRMADA!</b>\n"
        f"🎯 <b>Prop de Valor:</b> {jogador} p/ marcar\n"
        f"📊 <b>Mercado Base:</b> {d1}\n"
    )

# ================= 4. AUTOMAÇÕES =================
async def morning_routine(app: Application):
    """08:00 -> Grade de Mercado (Sem inventar jogador)"""
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if agora.hour == 8 and agora.minute == 0:
            global ALERTED_GAMES
            ALERTED_GAMES.clear()
            jogos = await fetch_espn_soccer()
            
            if jogos:
                txt = f"🌅 <b>BOM DIA! ANÁLISE DE MERCADO ({len(jogos)} Jogos)</b> 🌅\n"
                txt += "<i>Focamos nas probabilidades matemáticas. Aguarde 1h antes do jogo para Tips de Jogadores confirmados.</i>\n\n"
                
                for g in jogos:
                    d1, d2 = await analyze_game_market(g['league_code'], g['id'])
                    txt += format_morning_game(g, d1, d2) + "━━━━━━━━━━━━━━━━\n"
                    await asyncio.sleep(1)
                
                try: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                except: pass
            
            await asyncio.sleep(60)
        await asyncio.sleep(30)

async def live_sniper_routine(app: Application):
    """1 Hora antes -> Busca Escalação e manda Tip de Jogador se tiver"""
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if TODAYS_GAMES:
            jogos_do_horario = []
            for g in TODAYS_GAMES:
                if g['id'] in ALERTED_GAMES: continue
                try:
                    h, m = map(int, g['time'].split(':'))
                    hora_jogo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                    minutos = (hora_jogo - agora).total_seconds() / 60.0
                    
                    # Entre 50 e 60 min antes (horário que sai a escalação)
                    if 50 <= minutos <= 60:
                        jogos_do_horario.append(g)
                        ALERTED_GAMES.add(g['id'])
                except: pass
            
            if jogos_do_horario:
                for g in jogos_do_horario:
                    # TENTA PEGAR ESCALAÇÃO REAL
                    jogador_titular = await get_confirmed_lineup(g['league_code'], g['id'])
                    d1, _ = await analyze_game_market(g['league_code'], g['id'])
                    
                    if jogador_titular:
                        # SÓ MANDA SE TIVER JOGADOR CONFIRMADO
                        txt = format_sniper_game(g, jogador_titular, d1)
                        try: await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                        except: pass
                        await asyncio.sleep(2)
        
        await asyncio.sleep(60)

# ================= 5. MENU MANUAL =================
def get_menu(): 
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade de Mercado (Manhã)", callback_data="fut_market")],
        [InlineKeyboardButton("🕵️ Sniper de Escalação (Agora)", callback_data="fut_sniper")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba_deep")]
    ])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>BOT V230 ONLINE</b>\nEstratégia Profissional Ativada.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut_market":
        msg = await q.message.reply_text("🔎 <b>Analisando Mercados Seguros...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_espn_soccer()
        if not jogos:
            await msg.edit_text("❌ Nenhum jogo.")
            return
        txt = f"📊 <b>GRADE DE MERCADO ({len(jogos)} Jogos)</b>\n<i>Sem invenções. Apenas dados.</i>\n\n"
        for g in jogos:
            d1, d2 = await analyze_game_market(g['league_code'], g['id'])
            txt += format_morning_game(g, d1, d2) + "━━━━━━━━━━━━━━━━\n"
        await msg.edit_text("✅ <b>Postado!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        
    elif q.data == "fut_sniper":
        msg = await q.message.reply_text("🔎 <b>Procurando Escalações Confirmadas AGORA...</b>", parse_mode=ParseMode.HTML)
        # Tenta rodar o sniper manualmente para os jogos próximos
        jogos = await fetch_espn_soccer() # Atualiza lista
        encontrou = False
        for g in jogos:
             # Simula busca de escalação para todos os jogos da grade atual para teste
            jogador = await get_confirmed_lineup(g['league_code'], g['id'])
            d1, _ = await analyze_game_market(g['league_code'], g['id'])
            if jogador:
                txt = format_sniper_game(g, jogador, d1)
                await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
                encontrou = True
        
        if encontrou: await msg.edit_text("✅ <b>Escalações encontradas postadas!</b>")
        else: await msg.edit_text("❌ <b>Nenhuma escalação oficial liberada no momento.</b>\n(Elas saem 1h antes do jogo).")

    elif q.data == "nba_deep":
        j = await fetch_nba_schedule()
        if j: await c.bot.send_message(CHANNEL_ID, "🏀 <b>NBA</b>\n"+"\n".join(j), parse_mode=ParseMode.HTML)
        else: await q.message.edit_text("Sem NBA.")

# ================= 6. START =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V230 PRO")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    await fetch_espn_soccer()
    asyncio.create_task(morning_routine(app))
    asyncio.create_task(live_sniper_routine(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
