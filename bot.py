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
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- LOGS ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- CONFIGURAÇÕES E CHAVES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 10000))

THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY") # Usado para NBA
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY") # NOVO: Usado para Futebol
GEMINI_KEY = os.getenv("GEMINI_API_KEY") # Usado para Player Props

# CONFIGURA A IA
try:
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        print("✅ GEMINI AI V155: ATIVADO")
    else:
        model = None
        print("⚠️ GEMINI AI: Chave ausente")
except Exception as e:
    model = None
    print(f"⚠️ ERRO GEMINI: {e}")

AFFILIATE_LINKS = ["https://www.bet365.com", "https://br.betano.com", "https://stake.com"]
def get_random_link(): return random.choice(AFFILIATE_LINKS)

SENT_LINKS = set()
LATEST_HEADLINES = []

# HIERARQUIA
TIER_S_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO",
    "REAL MADRID", "BARCELONA", "LIVERPOOL", "MANCHESTER CITY", "ARSENAL", 
    "PSG", "BAYERN MUNICH", "INTER MIAMI", "AL NASSR", "CHELSEA", "MANCHESTER UNITED",
    "BENFICA", "PORTO", "SPORTING", "AJAX"
]
TIER_A_TEAMS = [
    "TOTTENHAM", "NEWCASTLE", "WEST HAM", "LEEDS", "ASTON VILLA", "EVERTON",
    "JUVENTUS", "INTER MILAN", "AC MILAN", "NAPOLI", "ATLETICO MADRID", 
    "DORTMUND", "LEVERKUSEN", "BOCA JUNIORS", "RIVER PLATE", "PSV", "FEYENOORD"
]

# NOVO MAPA DE LIGAS (IDs Oficiais da API-Football)
SOCCER_LEAGUES = {
    2: {"name": "CHAMPIONS LEAGUE", "score": 100},
    13: {"name": "LIBERTADORES", "score": 100},
    39: {"name": "PREMIER LEAGUE", "score": 100},
    71: {"name": "BRASILEIRÃO A", "score": 100},
    140: {"name": "LA LIGA", "score": 90},
    135: {"name": "SERIE A", "score": 90},
    78: {"name": "BUNDESLIGA", "score": 90},
    61: {"name": "LIGUE 1", "score": 90},
    94: {"name": "LIGA PORTUGAL", "score": 85},
    88: {"name": "EREDIVISIE", "score": 85},
    40: {"name": "CHAMPIONSHIP", "score": 80},
    45: {"name": "FA CUP", "score": 80},
    137: {"name": "COPA DA ITÁLIA", "score": 80},
    81: {"name": "COPA DA ALEMANHA", "score": 80},
    3: {"name": "EUROPA LEAGUE", "score": 80}
}

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V155 - MULTI MARKETS API")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception:", exc_info=context.error)

# --- IA PROPS ---
async def get_ai_soccer_props(match_name):
    if not model: return ""
    try:
        prompt = f"""
        Analise o jogo: {match_name} (2025/26).
        Me dê APENAS UM palpite de JOGADOR (Gol ou Assistência).
        Responda EXATAMENTE assim:
        🎯 Player: [Nome] p/ Marcar ou Assistência
        """
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except: return ""

async def get_ai_nba_props(match_name):
    if not model: return ""
    try:
        prompt = f"""
        NBA: {match_name}. Me dê 2 Props de Jogador.
        Responda assim:
        🏀 Player: [Nome] [Linha]
        🏀 Player: [Nome] [Linha]
        """
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except: return ""

# --- NOTICIAS ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    global LATEST_HEADLINES
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "escalação", "desfalque", "dúvida", "titular", "suspenso"]
        blacklist = ["bbb", "festa", "namorada", "reality"]
        if feed.entries: LATEST_HEADLINES = [entry.title for entry in feed.entries[:30]]
        c = 0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            title_lower = entry.title.lower()
            if any(w in title_lower for w in whitelist) and not any(b in title_lower for b in blacklist):
                msg = f"⚠️ <b>RADAR DE NOTÍCIAS</b>\n\n📰 {entry.title}\n🔗 {entry.link}"
                await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break
        if len(SENT_LINKS) > 1000: SENT_LINKS.clear()
    except: pass

class SportsEngine:
    def __init__(self): self.daily_accumulator = []

    async def test_all_connections(self):
        report = "📊 <b>STATUS V155</b>\n"
        if API_FOOTBALL_KEY: report += "✅ API-Football (Soccer): OK\n"
        else: report += "❌ API-Football: Faltando Chave\n"
        if THE_ODDS_API_KEY: report += "✅ The Odds API (NBA): OK\n"
        if model: report += "✅ Gemini AI: OK\n"
        return report

    # MOTOR NBA (Continua na The Odds API)
    async def fetch_nba_odds(self):
        if not THE_ODDS_API_KEY: return []
        url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds?regions=us&oddsFormat=decimal&markets=h2h&apiKey={THE_ODDS_API_KEY}"
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                r = await client.get(url)
                data = r.json()
                if not isinstance(data, list): return []
                games = []
                now_utc = datetime.now(timezone.utc)
                br_tz = timezone(timedelta(hours=-3))
                today_date = now_utc.astimezone(br_tz).date()

                for event in data:
                    try:
                        evt_time_utc = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        evt_time_br = evt_time_utc.astimezone(br_tz)
                        
                        tomorrow = today_date + timedelta(days=1)
                        is_valid = (evt_time_br.date() == today_date) or (evt_time_br.date() == tomorrow and evt_time_br.hour < 5)
                        if not is_valid: continue
                        
                        time_str = evt_time_br.strftime("%H:%M")
                        h, a = event['home_team'], event['away_team']
                        h_norm = normalize_name(h); a_norm = normalize_name(a)
                        
                        match_score = 50
                        is_vip = False
                        if any(t in h_norm or t in a_norm for t in NBA_VIP_TEAMS):
                            match_score += 1000; is_vip = True
                            
                        odds_h, odds_a = 0, 0
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = max(odds_h, o['price'])
                                        if o['name'] == a: odds_a = max(odds_a, o['price'])
                        
                        if odds_h > 1.01 and odds_a > 1.01:
                            games.append({
                                "match": f"{h} x {a}", "league": "NBA", "time": time_str, "datetime": evt_time_br, 
                                "odd_h": odds_h, "odd_a": odds_a, "home": h, "away": a, "is_vip": is_vip, "match_score": match_score
                            })
                    except: continue
                return games
            except: return []

    # NOVO MOTOR DE FUTEBOL (API-FOOTBALL)
    async def fetch_soccer_odds_api_football(self):
        if not API_FOOTBALL_KEY: return []
        headers = {"x-apisports-key": API_FOOTBALL_KEY}
        br_tz = timezone(timedelta(hours=-3))
        today_str = datetime.now(br_tz).strftime("%Y-%m-%d")
        
        games = []
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                # Busca páginas 1 e 2 para garantir que pega os jogos principais sem estourar o limite (2 reqs de 100)
                for page in range(1, 3):
                    url = f"https://v3.football.api-sports.io/odds?date={today_str}&bookmaker=8&page={page}"
                    r = await client.get(url, headers=headers)
                    data = r.json()
                    
                    if not data.get("response"): break
                    
                    for item in data["response"]:
                        league_id = item["league"]["id"]
                        if league_id in SOCCER_LEAGUES:
                            fixture = item["fixture"]
                            evt_time_utc = datetime.fromisoformat(fixture['date'])
                            evt_time_br = evt_time_utc.astimezone(br_tz)
                            
                            h = item["match"]["home"]
                            a = item["match"]["away"]
                            league_name = SOCCER_LEAGUES[league_id]["name"]
                            match_score = SOCCER_LEAGUES[league_id]["score"]
                            
                            h_norm = normalize_name(h); a_norm = normalize_name(a)
                            is_vip = False
                            if any(t in h_norm or t in a_norm for t in TIER_S_TEAMS): match_score += 1000; is_vip = True
                            elif any(t in h_norm or t in a_norm for t in TIER_A_TEAMS): match_score += 500
                            
                            # Extraindo TODOS os mercados reais
                            bets = item["bookmakers"][0]["bets"]
                            odds_1x2 = {"home": 0, "draw": 0, "away": 0}
                            odds_goals = 0 # Over 2.5
                            odds_btts = 0 # Yes
                            
                            for bet in bets:
                                if bet["id"] == 1: # Match Winner
                                    for val in bet["values"]:
                                        if val["value"] == "Home": odds_1x2["home"] = float(val["odd"])
                                        elif val["value"] == "Draw": odds_1x2["draw"] = float(val["odd"])
                                        elif val["value"] == "Away": odds_1x2["away"] = float(val["odd"])
                                elif bet["id"] == 5: # Over/Under
                                    for val in bet["values"]:
                                        if val["value"] == "Over 2.5": odds_goals = float(val["odd"])
                                elif bet["id"] == 8: # BTTS
                                    for val in bet["values"]:
                                        if val["value"] == "Yes": odds_btts = float(val["odd"])
                            
                            if odds_1x2["home"] > 1.01:
                                games.append({
                                    "match": f"{h} x {a}", "league": league_name, "time": evt_time_br.strftime("%H:%M"), "datetime": evt_time_br,
                                    "home": h, "away": a, "is_vip": is_vip, "match_score": match_score,
                                    "odds_1x2": odds_1x2, "odds_goals": odds_goals, "odds_btts": odds_btts
                                })
                return games
            except Exception as e:
                print(f"[ERRO API-FOOTBALL] {e}")
                return []

    async def analyze_soccer_game(self, game):
        lines = []
        best_pick = None
        
        # Chama a IA para Props
        ai_props = ""
        if game['is_vip'] and model:
            ai_props = await get_ai_soccer_props(game['match'])
        if ai_props: lines.append(ai_props)

        # Lógica de Mercados Variados (Adeus apenas DNB!)
        odds_1x2 = game["odds_1x2"]
        odds_goals = game["odds_goals"]
        odds_btts = game["odds_btts"]
        
        possible_picks = []

        # Analisa Vitória Seca
        if 1.20 <= odds_1x2["home"] <= 1.65:
            lines.append(f"💰 <b>Vencedor:</b> {game['home']} (@{odds_1x2['home']})")
            possible_picks.append({"pick": game['home'], "odd": odds_1x2['home'], "desc": "Vencedor"})
        elif 1.20 <= odds_1x2["away"] <= 1.65:
            lines.append(f"💰 <b>Vencedor:</b> {game['away']} (@{odds_1x2['away']})")
            possible_picks.append({"pick": game['away'], "odd": odds_1x2['away'], "desc": "Vencedor"})
            
        # Analisa Over Gols (Se tiver odd disponível)
        if 1.40 <= odds_goals <= 1.90:
            lines.append(f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{odds_goals})")
            possible_picks.append({"pick": "Over 2.5 Gols", "odd": odds_goals, "desc": "Gols"})
            
        # Analisa Ambas Marcam (BTTS)
        if 1.50 <= odds_btts <= 1.95:
            lines.append(f"⚔️ <b>Mercado:</b> Ambas Marcam Sim (@{odds_btts})")
            possible_picks.append({"pick": "Ambas Marcam", "odd": odds_btts, "desc": "BTTS"})

        # Se nenhum mercado principal for bom, protege com DNB
        if not possible_picks:
            if odds_1x2["home"] < odds_1x2["away"]:
                dnb = round(odds_1x2["home"] * 0.75, 2) # Estima DNB
                lines.append(f"🛡️ <b>Proteção:</b> {game['home']} DNB (@{dnb})")
                best_pick = {"pick": f"DNB {game['home']}", "odd": dnb, "match": game['match']}
            else:
                dnb = round(odds_1x2["away"] * 0.75, 2)
                lines.append(f"🛡️ <b>Proteção:</b> {game['away']} DNB (@{dnb})")
                best_pick = {"pick": f"DNB {game['away']}", "odd": dnb, "match": game['match']}
        else:
            # Sorteia um dos mercados bons para dar variedade na Múltipla!
            escolha = random.choice(possible_picks)
            best_pick = {"pick": escolha["pick"], "odd": escolha["odd"], "match": game['match']}

        return lines, best_pick

    async def analyze_nba_game(self, game):
        lines = []
        best_pick = None
        oh, oa = game['odd_h'], game['odd_a']
        
        ai_props = ""
        if model: ai_props = await get_ai_nba_props(game['match'])
        if ai_props: lines.append(ai_props)

        if oh < 1.50:
            lines.append(f"🔥 <b>Moneyline:</b> {game['home']} (@{oh})")
            best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}
        elif oa < 1.50:
            lines.append(f"🔥 <b>Moneyline:</b> {game['away']} (@{oa})")
            best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}
        else:
            lines.append("⚖️ <b>Jogo Parelho (Foque nos Jogadores)</b>")
        return lines, best_pick

    async def get_soccer_grade(self):
        all_games = []
        self.daily_accumulator = []
        
        games = await self.fetch_soccer_odds_api_football()
        for g in games:
            report, pick = await self.analyze_soccer_game(g)
            g['report'] = report
            if pick: self.daily_accumulator.append(pick)
            all_games.append(g)
            await asyncio.sleep(0.1)
            
        all_games.sort(key=lambda x: (-x['match_score'], x['datetime']))
        return all_games

    async def get_nba_games(self):
        games = await self.fetch_nba_odds()
        processed = []
        for g in games: 
            report, _ = await self.analyze_nba_game(g) 
            g['report'] = report
            processed.append(g)
        processed.sort(key=lambda x: (-x['match_score'], x['datetime']))
        return processed

engine = SportsEngine()

# --- MÚLTIPLA VARIADA ---
def gerar_bilhete(palpites):
    if len(palpites) < 3: return ""
    for _ in range(500):
        random.shuffle(palpites)
        # Prioriza jogos VIP na multipla
        palpites.sort(key=lambda x: 1 if any(t in x['match'].upper() for t in TIER_S_TEAMS + TIER_A_TEAMS) else 0, reverse=True)
        selected = []; total_odd = 1.0
        
        for p in palpites:
            if p['odd'] < 1.25: continue # Só odds de valor
            if total_odd * p['odd'] > 25.0: continue
            selected.append(p)
            total_odd *= p['odd']
            
            # Alvo de Múltipla entre 8.0 e 25.0
            if 8.0 <= total_odd <= 25.0:
                txt = f"\n🎟️ <b>MÚLTIPLA SNIPER V155 (ODD {total_odd:.2f})</b> 🎯\n"
                for s in selected: txt += f"🔹 {s['match']}: {s['pick']} (@{s['odd']})\n"
                txt += "⚠️ <i>Aposte com responsabilidade.</i>\n"
                return txt
    return "\n⚠️ <i>Sem múltipla de alto valor hoje.</i>"

async def enviar_audio(context, game):
    text = f"Destaque: {game['match']}."
    bet = game['report'][0].replace("<b>","").replace("</b>","").replace("🔥","").replace("🛡️","").replace("♻️","").replace("📉","").replace("🎯","").replace("⚔️","").replace("🥅","")
    text += f" Palpite: {bet[:80]}."
    try:
        tts = gTTS(text=text, lang='pt'); tts.save("audio.mp3")
        with open("audio.mp3", "rb") as f: await context.bot.send_voice(chat_id=CHANNEL_ID, voice=f)
        os.remove("audio.mp3")
    except: pass

async def enviar_post(context, text, bilhete=""):
    kb = [[InlineKeyboardButton("📲 APOSTAR AGORA", url=get_random_link())]]
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text+bilhete, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e: logger.error(f"Erro envio: {e}")

# --- JOBS & START ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_soccer_grade()
    if not games: return
    chunks = [games[i:i + 10] for i in range(0, len(games), 10)]
    for i, chunk in enumerate(chunks):
        header = "☀️ <b>BOM DIA! GRADE V155 (MÚLTIPLOS MERCADOS)</b> ☀️\n\n" if i == 0 else "👇 <b>MAIS JOGOS...</b>\n\n"
        msg = header
        for g in chunk:
            icon = "💎" if g['is_vip'] else "⚽"
            if i==0 and g == chunk[0]: await enviar_audio(context, g); icon = "⭐ <b>DESTAQUE</b>\n"
            reports = "\n".join(g['report'])
            msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
        bilhete = gerar_bilhete(engine.daily_accumulator) if i == len(chunks)-1 else ""
        await enviar_post(context, msg, bilhete)

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_nba_games()
    if not games: return
    msg = "🏀 <b>NBA - PLAYER PROPS</b> 🏀\n\n"
    for g in games[:8]:
        icon = "⭐" if g['is_vip'] else "🏀"
        reports = "\n".join(g['report'])
        msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
    await enviar_post(context, msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("⚽ Futebol (API Nova)", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
        [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Forçar Update", callback_data="force")]
    ]
    await update.message.reply_text("🦁 <b>BOT V155 ONLINE</b>\nAPI-Football Ativada. Mercados REAIS desbloqueados.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 <b>PONG!</b> Bot rodando 100%.", parse_mode=ParseMode.HTML)

async def post_init(application: Application):
    if CHANNEL_ID:
        try: await application.bot.send_message(chat_id=CHANNEL_ID, text="🚀 <b>SISTEMA V155 INICIADO!</b>\nNovo motor de busca com Ambas Marcam e Over Gols ligado.", parse_mode=ParseMode.HTML)
        except: pass

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "menu":
        kb = [[InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
              [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Forçar Update", callback_data="force")]]
        await q.edit_message_text("🦁 <b>MENU V155</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    elif q.data == "fut":
        await q.message.reply_text("⏳ <b>Buscando mercados (Gols, BTTS, Vitória)...</b>", parse_mode=ParseMode.HTML)
        await daily_soccer_job(context)
        await q.message.reply_text("✅ Feito.")
    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Analisando NBA...</b>", parse_mode=ParseMode.HTML)
        await daily_nba_job(context)
        await q.message.reply_text("✅ Feito.")
    elif q.data == "force":
        await q.message.reply_text("🔄 <b>Atualizando Tudo...</b>", parse_mode=ParseMode.HTML)
        await daily_soccer_job(context)
        await daily_nba_job(context)
        await q.message.reply_text("✅ Feito.")
    elif q.data == "status":
        rep = await engine.test_all_connections()
        await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data="menu")]]), parse_mode=ParseMode.HTML)

def main():
    if not BOT_TOKEN: print("ERRO: Configure o BOT_TOKEN no .env"); return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_error_handler(error_handler)
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=8, minute=0, tzinfo=timezone(timedelta(hours=-3))))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=18, minute=0, tzinfo=timezone(timedelta(hours=-3))))
    print("BOT V155 RODANDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
