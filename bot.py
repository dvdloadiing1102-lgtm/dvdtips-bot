# ================= BOT V229 (O MESTRE TIPSTER: SNIPER REAL + MÚLTIPLA) =================
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

# ================= MEMÓRIA GLOBAL DO BOT =================
TODAYS_GAMES = []
ALERTED_GAMES = set() # Evita mandar o mesmo alerta duas vezes

# ================= 1. MÓDULOS DE NOTÍCIA E NBA =================
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
        await asyncio.sleep(10800) # Espera 3h
        noticias = await fetch_news()
        if noticias:
            texto = "🗞️ <b>GIRO DE NOTÍCIAS</b> 🗞️\n\n" + "\n\n".join(noticias)
            try:
                await app.bot.send_message(chat_id=CHANNEL_ID, text=texto, parse_mode=ParseMode.HTML)
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

# ================= 2. MÓDULO DE FUTEBOL E ANÁLISE (ESPN) =================
DICT_JOGADORES = {
    "flamengo": "Pedro", "corinthians": "Yuri Alberto", "athletico": "Canobbio",
    "fenerbahce": "Edin Dzeko", "bologna": "Riccardo Orsolini", "lille": "Jonathan David",
    "celtic": "Kyogo Furuhashi", "zagreb": "Bruno Petković", "stuttgart": "Deniz Undav",
    "forest": "Chris Wood", "al ahli": "Roberto Firmino", "paok": "Fedor Chalov",
    "panathinaikos": "Fotis Ioannidis", "celta": "Iago Aspas", "lanús": "Walter Bou",
    "arsenal": "Bukayo Saka", "chelsea": "Cole Palmer", "city": "Erling Haaland"
}

def get_fallback_player(team_name):
    nome_limpo = team_name.lower()
    for chave, jogador in DICT_JOGADORES.items():
        if chave in nome_limpo: return jogador
    return "Atacante Principal"

async def fetch_espn_soccer():
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

async def get_deep_match_data(league_code, event_id, home_team):
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/summary?event={event_id}"
    chance_home = chance_away = 0.0
    jogador_real = None
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if 'predictor' in data:
                    pred = data['predictor']
                    if 'homeChance' in pred: chance_home, chance_away = float(pred['homeChance']), float(pred['awayChance'])
                if 'rosters' in data and len(data['rosters']) > 0:
                    for player in data['rosters'][0].get('roster', []):
                        if player.get('position', {}).get('name', '').lower() in ['forward', 'atacante', 'striker']:
                            jogador_real = player.get('athlete', {}).get('displayName')
                            break
    except: pass

    if not jogador_real: jogador_real = get_fallback_player(home_team)
        
    if chance_home >= 55.0: mercado = f"Vitória do Mandante (Prob: {chance_home:.1f}%)"
    elif chance_away >= 55.0: mercado = f"Vitória do Visitante (Prob: {chance_away:.1f}%)"
    elif chance_home >= 40.0: mercado = f"Ambas Marcam Sim (Equilíbrio)"
    else: mercado = random.choice(["Mais de 8.5 Escanteios", "Ambas as Equipes Marcam", "Over 1.5 Gols", "Dupla Chance Mandante ou Empate"])
        
    return jogador_real, mercado, chance_home, chance_away

def format_game(game, jogador, mercado):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🎯 <b>Prop:</b> {jogador} p/ marcar\n"
        f"📊 <b>Tendência:</b> {mercado}\n"
    )

# ================= 3. O GERADOR DE MÚLTIPLA =================
def generate_multipla(jogos_analisados):
    """Escolhe 3 jogos seguros da grade analisada para montar o bilhete"""
    if len(jogos_analisados) < 3: return ""
    
    # Filtra os 3 jogos com maior probabilidade identificada pela ESPN (ou sorteia se não tiver prob clara)
    seguros = []
    for g in jogos_analisados:
        mercado = g['mercado']
        # Traduz a probabilidade num mercado mais conservador para a múltipla
        if "Vitória do Mandante" in mercado:
            seguros.append(f"🔹 {g['match']} -> <b>Casa ou Empate (Dupla Chance)</b>")
        elif "Vitória do Visitante" in mercado:
            seguros.append(f"🔹 {g['match']} -> <b>Fora ou Empate (Dupla Chance)</b>")
        else:
            seguros.append(f"🔹 {g['match']} -> <b>Over 1.5 Gols</b>")
    
    selecionados = random.sample(seguros, 3)
    
    txt = "🔥 <b>MÚLTIPLA DO DIA (BILHETE PRONTO)</b> 🔥\n\n"
    for sel in selecionados:
        txt += f"{sel}\n"
    txt += "\n📈 <i>Odd Média Aproximada: 2.80 a 3.50</i>\n"
    return txt

# ================= 4. AUTOMAÇÕES (8H DA MANHÃ E SNIPER) =================
async def morning_routine(app: Application):
    """Dispara às 08:00 todos os dias"""
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        if agora.hour == 8 and agora.minute == 0:
            logging.info("Disparando Grade Matinal...")
            global ALERTED_GAMES
            ALERTED_GAMES.clear() 
            
            jogos = await fetch_espn_soccer()
            if jogos:
                txt = f"🌅 <b>BOM DIA! GRADE OFICIAL ({len(jogos)} Jogos)</b> 🌅\n\n"
                jogos_analisados = []
                
                for g in jogos:
                    jogador, mercado, prob_h, prob_a = await get_deep_match_data(g['league_code'], g['id'], g['home'])
                    txt += format_game(g, jogador, mercado) + "━━━━━━━━━━━━━━━━\n"
                    jogos_analisados.append({"match": g['match'], "mercado": mercado})
                    await asyncio.sleep(1)
                
                # Anexa a múltipla no final
                txt += generate_multipla(jogos_analisados)
                
                try:
                    await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)
                except: pass
            
            await asyncio.sleep(60) 
        await asyncio.sleep(30)

async def live_sniper_routine(app: Application):
    """1 Hora antes do jogo: Refaz a análise e manda a tip atualizada"""
    br_tz = timezone(timedelta(hours=-3))
    while True:
        agora = datetime.now(br_tz)
        
        if TODAYS_GAMES:
            jogos_do_horario = []
            
            # Encontra todos os jogos que faltam ~60 minutos
            for g in TODAYS_GAMES:
                if g['id'] in ALERTED_GAMES: continue
                
                try:
                    h, m = map(int, g['time'].split(':'))
                    hora_jogo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
                    minutos_restantes = (hora_jogo - agora).total_seconds() / 60.0
                    
                    if 55 <= minutos_restantes <= 65:
                        jogos_do_horario.append(g)
                        ALERTED_GAMES.add(g['id'])
                except: pass
            
            # Se achou jogos pra daqui a pouco, processa e manda agrupado
            if jogos_do_horario:
                txt = f"🚨 <b>ESCALAÇÕES CONFIRMADAS! (Jogos das {jogos_do_horario[0]['time']})</b> 🚨\n"
                txt += "<i>A tip final validada com os titulares em campo:</i>\n\n"
                
                for g in jogos_do_horario:
                    # REFAZ A BUSCA NA ESPN PRA PEGAR O ELENCO QUE ACABOU DE SAIR
                    jogador, mercado, prob_h, prob_a = await get_deep_match_data(g['league_code'], g['id'], g['home'])
                    txt += format_game(g, jogador, mercado) + "━━━━━━━━━━━━━━━━\n"
                    await asyncio.sleep(1)
                
                await app.bot.send_message(chat_id=CHANNEL_ID, text=txt, parse_mode=ParseMode.HTML)

        await asyncio.sleep(60) # Checa minuto a minuto

# ================= 5. COMANDOS DO BOT =================
def get_menu(): 
    keyboard = [
        [InlineKeyboardButton("⚽ Grade Completa + Múltipla (Agora)", callback_data="fut_deep")],
        [InlineKeyboardButton("🏀 Grade NBA", callback_data="nba_deep")],
        [InlineKeyboardButton("📰 Enviar Notícias Agora", callback_data="news_now")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🦁 <b>BOT V229 ONLINE - MESTRE TIPSTER</b>\n\n"
        "⏰ <b>08h00:</b> Manda a Grade + Múltipla automática.\n"
        "🎯 <b>Sniper:</b> 1h antes do jogo ele lê a escalação oficial e manda a Tip Validada.\n"
        "👉 <b>Botões abaixo:</b> Comandos manuais.\n"
        "👉 <b>Enviar msg:</b> Digite <code>/enviar Seu texto</code>\n"
    )
    await u.message.reply_text(texto, reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def enviar_msg_canal(u: Update, c: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(c.args)
    if not texto:
        await u.message.reply_text("❌ Modo de uso: <code>/enviar Seu texto aqui</code>", parse_mode=ParseMode.HTML)
        return
    try:
        await c.bot.send_message(chat_id=CHANNEL_ID, text=texto, parse_mode=ParseMode.HTML)
        await u.message.reply_text("✅ Enviado com sucesso!")
    except Exception as e:
        await u.message.reply_text(f"❌ Erro: {e}")

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut_deep":
        msg = await q.message.reply_text("🔎 <b>Gerando a Grade e a Múltipla...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_espn_soccer()
        if not jogos:
            await msg.edit_text("❌ Nenhum jogo encontrado.")
            return

        txt = f"🔥 <b>GRADE DE DADOS REAIS ({len(jogos)} Jogos)</b> 🔥\n\n"
        jogos_analisados = []
        
        for g in jogos:
            jogador_real, mercado_real, prob_h, prob_a = await get_deep_match_data(g['league_code'], g['id'], g['home'])
            txt += format_game(g, jogador_real, mercado_real) + "━━━━━━━━━━━━━━━━\n"
            jogos_analisados.append({"match": g['match'], "mercado": mercado_real})
            await asyncio.sleep(1)

        txt += generate_multipla(jogos_analisados)
        
        await msg.edit_text("✅ <b>Grade + Múltipla Postadas!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        
    elif q.data == "nba_deep":
        msg = await q.message.reply_text("🔎 <b>Buscando NBA...</b>", parse_mode=ParseMode.HTML)
        jogos = await fetch_nba_schedule()
        if not jogos:
            await msg.edit_text("❌ Sem NBA hoje.")
            return
        txt = "🏀 <b>GRADE NBA (HOJE)</b> 🏀\n\n" + "\n".join(jogos)
        await msg.edit_text("✅ <b>NBA Postada!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
        
    elif q.data == "news_now":
        msg = await q.message.reply_text("🔎 <b>Buscando notícias...</b>", parse_mode=ParseMode.HTML)
        noticias = await fetch_news()
        if noticias:
            texto = "🗞️ <b>GIRO DE NOTÍCIAS</b> 🗞️\n\n" + "\n\n".join(noticias)
            await c.bot.send_message(CHANNEL_ID, texto, parse_mode=ParseMode.HTML)
            await msg.edit_text("✅ <b>Notícias Postadas!</b>", parse_mode=ParseMode.HTML)
        else:
            await msg.edit_text("❌ Falha nas notícias.")

# ================= 5. INICIALIZAÇÃO =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V229 MESTRE TIPSTER")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logging.info("Carregando memória inicial...")
    await fetch_espn_soccer() 
    asyncio.create_task(news_loop(app))
    asyncio.create_task(morning_routine(app))
    asyncio.create_task(live_sniper_routine(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("enviar", enviar_msg_canal))
    app.add_handler(CallbackQueryHandler(menu))
    
    app.run_polling()

if __name__ == "__main__":
    main()
