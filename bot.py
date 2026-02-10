import os
import sys
import logging
import asyncio
import feedparser
import httpx
import threading
import unicodedata
import psutil
import random
from datetime import datetime, timezone, timedelta, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from gtts import gTTS 

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- LOGS ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- CONFIGURAÇÕES ---
# Certifique-se de que estas variaveis estao no seu arquivo .env ou nas variaveis do Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID") # Adicione seu ID aqui para receber erros
PORT = int(os.getenv("PORT", 10000))
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

AFFILIATE_LINKS = ["https://www.bet365.com", "https://br.betano.com", "https://stake.com"]
def get_random_link(): return random.choice(AFFILIATE_LINKS)

SENT_LINKS = set()
LATEST_HEADLINES = []

# LISTA VIP REFINADA (Times que o publico brasileiro gosta)
VIP_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO", "GREMIO", "INTERNACIONAL", "ATLETICO-MG", "CRUZEIRO",
    "REAL MADRID", "BARCELONA", "LIVERPOOL", "MANCHESTER CITY", "ARSENAL", "MANCHESTER UNITED", "CHELSEA", 
    "PSG", "BAYERN MUNICH", "JUVENTUS", "INTER MILAN", "AC MILAN", "NAPOLI",
    "BOCA JUNIORS", "RIVER PLATE", "AL NASSR", "INTER MIAMI"
]

# LIGAS RELEVANTES (Peso ajustado para dar prioridade a jogos bons)
SOCCER_LEAGUES = [
    {"key": "soccer_uefa_champs_league", "name": "CHAMPIONS LEAGUE", "weight": 3000},
    {"key": "soccer_conmebol_libertadores", "name": "LIBERTADORES", "weight": 3000},
    {"key": "soccer_brazil_campeonato", "name": "BRASILEIRÃO A", "weight": 2500},
    {"key": "soccer_england_premier_league", "name": "PREMIER LEAGUE", "weight": 2000},
    {"key": "soccer_spain_la_liga", "name": "LA LIGA", "weight": 1500},
    {"key": "soccer_italy_serie_a", "name": "SERIE A", "weight": 1500},
    {"key": "soccer_germany_bundesliga", "name": "BUNDESLIGA", "weight": 1500},
    {"key": "soccer_france_ligue_one", "name": "LIGUE 1", "weight": 1000},
    {"key": "soccer_brazil_campeonato_paulista", "name": "PAULISTA A1", "weight": 800},
    {"key": "soccer_brazil_campeonato_carioca", "name": "CARIOCA A1", "weight": 800},
    {"key": "soccer_uefa_europa_league", "name": "EUROPA LEAGUE", "weight": 1000}
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVIDOR FAKE PARA O RENDER NÃO DORMIR ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V135 - ON FIRE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        if ADMIN_ID:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ <b>ERRO NO BOT:</b>\n<code>{context.error}</code>", parse_mode=ParseMode.HTML)
    except: pass

# --- NEWS JOB (NOTÍCIAS) ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    global LATEST_HEADLINES
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        
        # Palavras-chave que indicam notícia importante de aposta/jogo
        whitelist = ["lesão", "vetado", "fora", "contratado", "escalação", "desfalque", "dúvida", "titular", "banco"]
        blacklist = ["bbb", "festa", "namorada", "reality", "camarote"]
        
        if feed.entries: LATEST_HEADLINES = [entry.title for entry in feed.entries[:30]]
        
        c=0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            title_lower = entry.title.lower()
            
            # Filtro: Tem palavra chave E não tem palavra proibida
            if any(w in title_lower for w in whitelist) and not any(b in title_lower for b in blacklist):
                msg = f"⚠️ <b>RADAR DE NOTÍCIAS</b>\n\n📰 {entry.title}\n🔗 {entry.link}"
                await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break # Manda max 2 noticias por vez para não floodar
        if len(SENT_LINKS)>1000: SENT_LINKS.clear()
    except Exception as e: logger.error(f"Erro News: {e}")

# ================= MOTOR DE APOSTAS =================
class SportsEngine:
    def __init__(self): self.daily_accumulator = []

    async def test_all_connections(self):
        report = "📊 <b>STATUS DO SISTEMA</b>\n\n"
        mem = psutil.virtual_memory()
        report += f"💻 RAM: {mem.percent}%\n"
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: 
                        rem = r.headers.get("x-requests-remaining", "?")
                        report += f"✅ API Odds: Conectada ({rem} requisições restantes)\n"
                    else: report += f"❌ API Odds: Erro {r.status_code}\n"
                except: report += "❌ API Odds: Falha na Conexão\n"
        else: report += "❌ API Key não configurada.\n"
        return report

    async def fetch_odds(self, sport_key, display_name, weight):
        if not THE_ODDS_API_KEY: return []
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=us&oddsFormat=decimal&markets=h2h&apiKey={THE_ODDS_API_KEY}"
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                r = await client.get(url)
                data = r.json()
                if not isinstance(data, list): return []
                games = []
                now = datetime.now(timezone.utc)
                limit_time = now + timedelta(hours=26) # Pega jogos das próximas 26h
                
                for event in data:
                    try:
                        evt_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        if evt_time > limit_time or evt_time < now: continue 
                        
                        time_str = evt_time.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
                        h, a = event['home_team'], event['away_team']
                        
                        # CALCULO DE RELEVÂNCIA (SCORE)
                        match_score = weight 
                        h_norm = normalize_name(h); a_norm = normalize_name(a)
                        is_vip = False
                        
                        # Se for time VIP, ganha bonus, mas não infinito
                        for vip in VIP_TEAMS:
                            if vip in h_norm or vip in a_norm: 
                                match_score += 5000 
                                is_vip = True
                                break
                        
                        odds_h, odds_a, odds_d = 0, 0, 0
                        # Pega a melhor odd disponível
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = max(odds_h, o['price'])
                                        if o['name'] == a: odds_a = max(odds_a, o['price'])
                                        if o['name'] == 'Draw': odds_d = max(odds_d, o['price'])
                        
                        # FILTRO ANTI-LIXO: Ignora jogos onde a odd é 1.01 (jogo ganho) ou inválida
                        if odds_h > 1.01 and odds_a > 1.01:
                             # Se a odd for muito baixa (ex: Man City vs Time da 4ª divisão), diminui o score para não poluir
                            if odds_h < 1.10 or odds_a < 1.10:
                                match_score -= 2000

                            games.append({
                                "match": f"{h} x {a}", 
                                "league": display_name, 
                                "time": time_str, 
                                "datetime": evt_time, 
                                "odd_h": odds_h, 
                                "odd_a": odds_a, 
                                "odd_d": odds_d, 
                                "home": h, 
                                "away": a, 
                                "match_score": match_score, 
                                "is_vip": is_vip
                            })
                    except: continue
                return games
            except: return []

    def calculate_stats(self, odd):
        try:
            prob = round((1 / odd) * 100, 1)
            stake = "1 Unidade" # Padrão conservador
            if odd >= 1.60 and odd < 2.00: stake = "0.75 Unidade"
            elif odd >= 2.00: stake = "0.5 Unidade"
            return prob, stake
        except: return 0, "?"

    def analyze_game(self, game):
        lines = []
        best_pick = None
        
        # Analise de Notícias (Radar)
        has_news = False
        for news in LATEST_HEADLINES:
            if normalize_name(game['home']) in normalize_name(news) or normalize_name(game['away']) in normalize_name(news): has_news = True
        if has_news: lines.append("📰 <b>Radar:</b> Atenção às notícias recentes!")

        oh, oa, od = game['odd_h'], game['odd_a'], game['odd_d']
        pick_odd = 0
        
        # ESTRATÉGIA DE APOSTA
        # Favorito Jogando em Casa
        if 1.20 < oh < 1.75:
            lines.append(f"🔥 <b>Favorito:</b> {game['home']} Vence (@{oh})")
            best_pick = {"pick": f"{game['home']}", "odd": oh, "match": game['match']}; pick_odd = oh
        
        # Favorito Jogando Fora
        elif 1.20 < oa < 1.80:
            lines.append(f"🔥 <b>Favorito:</b> {game['away']} Vence (@{oa})")
            best_pick = {"pick": f"{game['away']}", "odd": oa, "match": game['match']}; pick_odd = oa
        
        # Dupla Chance (Casa ou Empate) em jogo parelho
        elif 1.30 < oh < 2.40 and od > 0:
            dc_odd = round(1 / (1/oh + 1/od), 2)
            if 1.25 < dc_odd < 1.65:
                 lines.append(f"🛡️ <b>Segurança:</b> {game['home']} ou Empate (@{dc_odd})")
                 if not best_pick: best_pick = {"pick": f"1X ({game['home']})", "odd": dc_odd, "match": game['match']}; pick_odd = dc_odd
        
        # Zebras (Oportunidade de Ouro)
        if not lines:
            if oh > 3.20 and oa < 1.45: # Visitante muito favorito, mas casa paga bem
                lines.append(f"🦓 <b>Olho na Zebra:</b> {game['home']} pode surpreender?")
            if oa > 3.20 and oh < 1.45:
                lines.append(f"🦓 <b>Olho na Zebra:</b> {game['away']} pode surpreender?")

        # Se não achou nada óbvio, mostra valor
        if not lines:
            if oh < 2.10: 
                lines.append(f"💎 <b>Valor:</b> {game['home']} (@{oh})")
                best_pick = {"pick": f"{game['home']}", "odd": oh, "match": game['match']}; pick_odd = oh
            elif oa < 2.10: 
                lines.append(f"💎 <b>Valor:</b> {game['away']} (@{oa})")
                best_pick = {"pick": f"{game['away']}", "odd": oa, "match": game['match']}; pick_odd = oa
            else: 
                lines.append(f"⚖️ <b>Jogo Duro:</b> Odds Equilibradas")

        if pick_odd > 0:
            prob, stake = self.calculate_stats(pick_odd)
            lines.append(f"📊 <b>Prob:</b> {prob}% | 💰 <b>Stake:</b> {stake}")
        
        return lines, best_pick

    async def get_soccer_grade(self):
        all_games = []
        self.daily_accumulator = []
        
        # Busca todas as ligas configuradas
        for league in SOCCER_LEAGUES:
            games = await self.fetch_odds(league['key'], league['name'], league['weight'])
            for g in games:
                report, pick = self.analyze_game(g)
                g['report'] = report
                if pick: self.daily_accumulator.append(pick)
                all_games.append(g)
            await asyncio.sleep(0.2) # Pequeno delay para não sobrecarregar
        
        # ORDENAÇÃO MÁGICA: Score (Relevância) -> Depois Horário
        all_games.sort(key=lambda x: (-x['match_score'], x['datetime']))
        return all_games
    
    async def get_nba_games(self):
        games = await self.fetch_odds("basketball_nba", "NBA", 500)
        processed = []
        for g in games: report, _ = self.analyze_game(g); g['report'] = report; processed.append(g)
        return processed
    
    async def get_ufc_games(self): return await self.fetch_odds("mma_mixed_martial_arts", "UFC/MMA", 500)

engine = SportsEngine()

# --- FUNÇÕES DE ENVIO ---
def gerar_texto_bilhete(palpites):
    if not palpites: return ""
    selected = []
    total_odd = 1.0
    import random
    random.shuffle(palpites)
    
    # Tenta priorizar times conhecidos no bilhete
    palpites.sort(key=lambda x: 1 if "Real" in x['match'] or "City" in x['match'] or "Flamengo" in x['match'] or "Arsenal" in x['match'] else 0, reverse=True)
    
    for p in palpites:
        # Bilhete com odd maxima de 15 para ter chance real de green
        if total_odd * p['odd'] > 15: continue 
        selected.append(p)
        total_odd *= p['odd']
        if len(selected) >= 4: break # Maximo 4 jogos no bilhete
        
    if total_odd < 2.5: return "" # Se a odd for muito baixa, nem manda
    
    txt = f"\n🎟️ <b>BILHETE DO DIA (ODD {total_odd:.2f})</b> 🚀\n"
    for s in selected: txt += f"🎯 {s['match']}: {s['pick']} (@{s['odd']})\n"
    txt += "⚠️ <i>Gestão de banca sempre!</i>\n"
    return txt

async def enviar_audio_narracao(context, game):
    # Narracao humanizada
    text = f"Atenção apostadores! Destaque para {game['match']} pela {game['league']}. "
    found_bet = False
    for line in game['report']:
        if "Favorito" in line or "Valor" in line or "Segurança" in line:
            clean = line.replace("<b>", "").replace("</b>", "").replace("🔥", "").replace("💎", "").replace("🛡️", "")
            text += f"A análise indica: {clean}. "
            found_bet = True
            break
    if not found_bet: text += "O jogo promete ser equilibrado. Analise as odds com carinho."
    text += "Vamos buscar esse green!"
    
    try:
        tts = gTTS(text=text, lang='pt')
        tts.save("narracao.mp3")
        with open("narracao.mp3", "rb") as audio: await context.bot.send_voice(chat_id=CHANNEL_ID, voice=audio)
        os.remove("narracao.mp3")
    except: pass

async def enviar_com_botao(context, text, poll_data=None, bilhete_txt=""):
    full_text = text + bilhete_txt
    link = get_random_link()
    kb = [[InlineKeyboardButton("📲 APOSTAR AGORA", url=link)]]
    try: 
        await context.bot.send_message(chat_id=CHANNEL_ID, text=full_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        if poll_data:
            await asyncio.sleep(2)
            await context.bot.send_poll(chat_id=CHANNEL_ID, question=f"Quem leva essa? {poll_data['h']} x {poll_data['a']}", options=["Casa", "Empate", "Visitante"], is_anonymous=True)
    except Exception as e:
        logger.error(f"Erro ao postar no canal: {e}")

# --- COMANDOS DO BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🦁 ABRIR MENU", callback_data="open_menu")]]
    await update.message.reply_text("🦁 <b>SISTEMA DE APOSTAS V135</b>\n\nEstou online e monitorando o mercado.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = q.data
    
    if data == "open_menu":
        kb = [
            [InlineKeyboardButton("⚽ Futebol Hoje", callback_data="top_jogos"), InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
            [InlineKeyboardButton("🥊 UFC", callback_data="ufc_fights"), InlineKeyboardButton("📊 Status", callback_data="test_api")],
            [InlineKeyboardButton("🆘 SOS", callback_data="sos_help"), InlineKeyboardButton("📖 Ajuda", callback_data="help_msg")]
        ]
        await q.edit_message_text("🦁 <b>MENU PRINCIPAL</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML); return

    if data == "test_api": rep = await engine.test_all_connections(); kb = [[InlineKeyboardButton("Voltar", callback_data="open_menu")]]; await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML); return
    
    # Processamento dos jogos manuais
    if data == "top_jogos":
        await q.message.reply_text("⏳ <b>Analisando mercado... aguarde...</b>", parse_mode=ParseMode.HTML)
        games = await engine.get_soccer_grade()
        if not games: await q.message.reply_text("⚠️ Nenhum jogo relevante encontrado agora."); return
        
        # PEGA OS TOP 10 JOGOS (Aumentei de 7 para 10)
        msg = f"🔥 <b>MELHORES OPORTUNIDADES DO DIA</b> 🔥\n\n"; poll_data = None
        for i, g in enumerate(games[:10]):
            is_main = (i == 0)
            icon = "🏆"
            if g['is_vip']: icon = "💎"
            if is_main: 
                icon = "⭐ <b>JOGO DO DIA</b> ⭐\n"
                poll_data = {"h": g['home'], "a": g['away']}
                await enviar_audio_narracao(context, g) # Manda audio so do principal
            
            blk = "\n".join(g['report'])
            msg += f"{icon} <b>{g['league']}</b> | {g['time']}\n⚔️ <b>{g['match']}</b>\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"
        
        bilhete = gerar_texto_bilhete(engine.daily_accumulator)
        await enviar_com_botao(context, msg, poll_data, bilhete)
        await q.message.reply_text("✅ Análise enviada para o canal!")

    if data == "nba_hoje":
        await q.message.reply_text("🏀 <b>Buscando jogos da NBA...</b>", parse_mode=ParseMode.HTML)
        games = await engine.get_nba_games()
        if not games: await q.message.reply_text("⚠️ Nenhum jogo da NBA encontrado."); return
        msg = f"🏀 <b>NBA TONIGHT</b> 🏀\n\n"
        for g in games[:5]:
            blk = "\n".join(g['report'])
            msg += f"🏟 <b>{g['league']}</b> • {g['time']}\n⚔️ <b>{g['match']}</b>\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"
        await enviar_com_botao(context, msg)
        await q.message.reply_text("✅ NBA enviada!")

# --- JOBS AUTOMÁTICOS ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    # Versão automatica que roda todo dia meio dia
    games = await engine.get_soccer_grade()
    if not games: return
    
    # Pega top 12 para garantir variedade
    top_games = games[:12]
    msg = f"☀️ <b>BOM DIA! APOSTAS DE HOJE</b> ☀️\n\n"
    poll_data = None
    
    for i, g in enumerate(top_games):
        is_main = (i == 0)
        icon = "🏆"
        if g['is_vip']: icon = "💎"
        if is_main: 
            icon = "⭐ <b>DESTAQUE MÁXIMO</b> ⭐\n"
            poll_data = {"h": g['home'], "a": g['away']}
            await enviar_audio_narracao(context, g)
            
        block = "\n".join(g['report'])
        msg += f"{icon} <b>{g['league']}</b> | ⏰ {g['time']}\n⚔️ <b>{g['match']}</b>\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
        
    bilhete = gerar_texto_bilhete(engine.daily_accumulator)
    await enviar_com_botao(context, msg, poll_data, bilhete)

# --- MAIN ---
def main():
    if not BOT_TOKEN: 
        print("ERRO: BOT_TOKEN nao encontrado no .env")
        return
        
    threading.Thread(target=run_web_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_error_handler(error_handler)

    # Agendamentos (Jobs)
    if app.job_queue:
        # Noticias a cada 30 min
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        # Futebol todo dia as 11:00 (ajustado para dar tempo de pegar jogos cedo)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=11, minute=0, tzinfo=timezone(timedelta(hours=-3)))) 

    print("BOT V135 RODANDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
