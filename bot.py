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

# --- LOGS ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# --- CONFIGURAÇÕES E CHAVES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 10000))

THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY") 
GEMINI_KEY = os.getenv("GEMINI_API_KEY") 

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# CONFIGURA A IA
try:
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        print("✅ GEMINI AI: ATIVADO")
    else:
        model = None
        print("⚠️ GEMINI AI: Chave ausente")
except Exception as e:
    model = None
    print(f"⚠️ ERRO GEMINI: {e}")

AFFILIATE_LINKS = ["https://www.bet365.com", "https://br.betano.com", "https://stake.com"]
def get_random_link(): return random.choice(AFFILIATE_LINKS)

SENT_LINKS = set()

# HIERARQUIA
TIER_S_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO",
    "REAL MADRID", "BARCELONA", "LIVERPOOL", "MANCHESTER CITY", "ARSENAL", 
    "PSG", "BAYERN MUNICH", "INTER MIAMI", "AL NASSR", "CHELSEA", "MANCHESTER UNITED",
    "BENFICA", "PORTO", "SPORTING", "AJAX", "JUVENTUS", "INTER MILAN", "AC MILAN"
]
TIER_A_TEAMS = [
    "TOTTENHAM", "NEWCASTLE", "WEST HAM", "LEEDS", "ASTON VILLA", "EVERTON",
    "NAPOLI", "ATLETICO MADRID", "DORTMUND", "LEVERKUSEN", "BOCA JUNIORS", "RIVER PLATE"
]

SOCCER_LEAGUES = [
    {"key": "soccer_uefa_champs_league", "name": "CHAMPIONS LEAGUE", "score": 100},
    {"key": "soccer_conmebol_libertadores", "name": "LIBERTADORES", "score": 100},
    {"key": "soccer_epl", "name": "PREMIER LEAGUE", "score": 100},
    {"key": "soccer_spain_la_liga", "name": "LA LIGA", "score": 90},
    {"key": "soccer_italy_serie_a", "name": "SERIE A", "score": 90},
    {"key": "soccer_germany_bundesliga", "name": "BUNDESLIGA", "score": 90},
    {"key": "soccer_france_ligue_one", "name": "LIGUE 1", "score": 90},
    {"key": "soccer_portugal_primeira_liga", "name": "LIGA PORTUGAL", "score": 85},
    {"key": "soccer_netherlands_eredivisie", "name": "EREDIVISIE", "score": 85},
    {"key": "soccer_england_fa_cup", "name": "FA CUP", "score": 80},
    {"key": "soccer_brazil_campeonato", "name": "BRASILEIRÃO A", "score": 100}
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V161 - BUG FIXED")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception:", exc_info=context.error)

# --- IA PROPS ---
async def get_ai_soccer_props(match_name):
    if not model: return ""
    try:
        prompt = f"Analise o jogo {match_name}. Dê UMA dica focada em CARTÕES, ESCANTEIOS ou JOGADORES. Formato: 💡 Dica: [Sua dica curta]"
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except: return ""

async def get_ai_nba_props(match_name):
    if not model: return ""
    try:
        prompt = f"NBA: {match_name}. Dê 2 Props de Jogador. Formato: 🏀 Player: [Nome] [Linha]"
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except: return ""

# --- NOTICIAS ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "escalação", "desfalque", "suspenso"]
        if feed.entries:
            for entry in feed.entries[:5]:
                if entry.link in SENT_LINKS: continue
                title_lower = entry.title.lower()
                if any(w in title_lower for w in whitelist):
                    msg = f"⚠️ <b>RADAR DE NOTÍCIAS</b>\n\n📰 {entry.title}\n🔗 {entry.link}"
                    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                    SENT_LINKS.add(entry.link)
        if len(SENT_LINKS) > 1000: SENT_LINKS.clear()
    except: pass

class SportsEngine:
    def __init__(self): 
        self.daily_accumulator = []
        self.soccer_cache = []
        self.soccer_last_update = None
        self.nba_cache = []
        self.nba_last_update = None

    async def test_all_connections(self):
        report = "📊 <b>STATUS V161</b>\n"
        if THE_ODDS_API_KEY: report += "✅ The Odds API: OK\n"
        if model: report += "✅ Gemini AI: OK\n"
        if self.soccer_last_update:
            report += f"🕒 Cache Futebol: {self.soccer_last_update.strftime('%H:%M')}\n"
        return report

    async def fetch_odds(self, sport_key, display_name, league_score=0, is_nba=False):
        if not THE_ODDS_API_KEY: return []
        # CORREÇÃO CRÍTICA: regions=uk (Pega odds europeias de todas as ligas mundiais)
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=uk&oddsFormat=decimal&markets=h2h,totals&apiKey={THE_ODDS_API_KEY}"
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                r = await client.get(url)
                data = r.json()
                
                # Proteção caso a cota da API acabe
                if isinstance(data, dict) and data.get("message"):
                    print(f"ERRO THE ODDS API: {data['message']}")
                    return []
                    
                if not isinstance(data, list): return []
                
                games = []
                br_tz = timezone(timedelta(hours=-3))
                today_date = datetime.now(timezone.utc).astimezone(br_tz).date()

                for event in data:
                    try:
                        evt_time_br = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')).astimezone(br_tz)
                        tomorrow = today_date + timedelta(days=1)
                        if is_nba:
                            if not ((evt_time_br.date() == today_date) or (evt_time_br.date() == tomorrow and evt_time_br.hour < 5)): continue
                        else:
                            if evt_time_br.date() != today_date: continue
                        
                        h, a = event['home_team'], event['away_team']
                        h_norm, a_norm = normalize_name(h), normalize_name(a)
                        
                        match_score = league_score
                        is_vip = False
                        if any(t in h_norm or t in a_norm for t in TIER_S_TEAMS): match_score += 1000; is_vip = True
                        elif any(t in h_norm or t in a_norm for t in TIER_A_TEAMS): match_score += 500
                            
                        odds_1x2 = {"home": 0, "draw": 0, "away": 0}
                        odds_over_25 = 0
                        
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_1x2["home"] = max(odds_1x2["home"], o['price'])
                                        if o['name'] == a: odds_1x2["away"] = max(odds_1x2["away"], o['price'])
                                        if o['name'] == 'Draw': odds_1x2["draw"] = max(odds_1x2["draw"], o['price'])
                                elif m['key'] == 'totals':
                                    for o in m['outcomes']:
                                        if o['name'] == 'Over' and o.get('point') == 2.5:
                                            odds_over_25 = max(odds_over_25, o['price'])
                        
                        if odds_1x2["home"] > 1.01:
                            games.append({
                                "match": f"{h} x {a}", "league": display_name, "time": evt_time_br.strftime("%H:%M"), "datetime": evt_time_br,
                                "home": h, "away": a, "is_vip": is_vip, "match_score": match_score, 
                                "odds_1x2": odds_1x2, "odds_over_25": odds_over_25, "is_nba": is_nba
                            })
                    except: continue
                return games
            except: return []

    async def analyze_game(self, game):
        lines = []
        best_pick = None
        
        # --- NBA ---
        if game.get('is_nba'):
            ai_props = ""
            if model: ai_props = await get_ai_nba_props(game['match'])
            if ai_props: lines.append(ai_props)

            oh, oa = game['odds_1x2']['home'], game['odds_1x2']['away']
            if oh < 1.50:
                lines.append(f"🔥 <b>ML:</b> {game['home']} (@{oh})")
                best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}
            elif oa < 1.50:
                lines.append(f"🔥 <b>ML:</b> {game['away']} (@{oa})")
                best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}
            else:
                lines.append("⚖️ <b>ML Parelho</b>")
            return lines, best_pick

        # --- FUTEBOL ---
        ai_props = ""
        if game['is_vip'] and model: 
            ai_props = await get_ai_soccer_props(game['match'])
            if ai_props: lines.append(ai_props)

        oh, oa, od = game["odds_1x2"]["home"], game["odds_1x2"]["away"], game["odds_1x2"]["draw"]
        odds_over = game["odds_over_25"]
        possible_picks = []

        # 1. Gols (Prioridade)
        if odds_over > 0:
            if 1.35 <= odds_over <= 1.95:
                lines.append(f"🥅 <b>Mercado:</b> Over 2.5 Gols (@{odds_over})")
                possible_picks.append({"pick": "Over 2.5 Gols", "odd": odds_over})

        # 2. Vencedores (Range expandido para não apagar o jogo)
        if 1.15 <= oh <= 1.85:
            lines.append(f"💰 <b>Vencedor:</b> {game['home']} (@{oh})")
            possible_picks.append({"pick": game['home'], "odd": oh})
        elif 1.15 <= oa <= 1.85:
            lines.append(f"💰 <b>Vencedor:</b> {game['away']} (@{oa})")
            possible_picks.append({"pick": game['away'], "odd": oa})

        # 3. Proteção / DNB (A rede de segurança para nenhum jogo sumir)
        if not possible_picks:
            if oh < oa:
                dnb = round(oh * 0.75, 2)
                lines.append(f"🛡️ <b>Proteção:</b> {game['home']} DNB (@{dnb})")
                possible_picks.append({"pick": f"DNB {game['home']}", "odd": dnb})
            else:
                dnb = round(oa * 0.75, 2)
                lines.append(f"🛡️ <b>Proteção:</b> {game['away']} DNB (@{dnb})")
                possible_picks.append({"pick": f"DNB {game['away']}", "odd": dnb})

        if possible_picks:
            escolha = random.choice(possible_picks)
            best_pick = {"pick": escolha["pick"], "odd": escolha["odd"], "match": game['match']}

        return lines, best_pick

    async def get_soccer_grade(self):
        now = datetime.now()
        if self.soccer_cache and self.soccer_last_update:
            if (now - self.soccer_last_update) < timedelta(hours=6):
                return self.soccer_cache

        print("📡 Buscando novos dados na API (Futebol)...")
        all_games = []
        self.daily_accumulator = []
        
        for league in SOCCER_LEAGUES:
            games = await self.fetch_odds(league['key'], league['name'], league['score'], is_nba=False)
            for g in games:
                report, pick = await self.analyze_game(g)
                g['report'] = report
                if pick: self.daily_accumulator.append(pick)
                all_games.append(g)
            await asyncio.sleep(0.2)
            
        all_games.sort(key=lambda x: (-x['match_score'], x['datetime']))
        if all_games:
            self.soccer_cache = all_games
            self.soccer_last_update = now
        return all_games

    async def get_nba_games(self):
        now = datetime.now()
        if self.nba_cache and self.nba_last_update:
            if (now - self.nba_last_update) < timedelta(hours=6):
                return self.nba_cache

        games = await self.fetch_odds("basketball_nba", "NBA", 50, is_nba=True)
        processed = []
        for g in games: 
            report, _ = await self.analyze_game(g) 
            g['report'] = report
            processed.append(g)
            
        processed.sort(key=lambda x: (-x['match_score'], x['datetime']))
        if processed:
            self.nba_cache = processed
            self.nba_last_update = now
        return processed

engine = SportsEngine()

def gerar_bilhete(palpites):
    if len(palpites) < 3: return ""
    for _ in range(500):
        random.shuffle(palpites)
        palpites.sort(key=lambda x: 1 if any(t in x['match'].upper() for t in TIER_S_TEAMS + TIER_A_TEAMS) else 0, reverse=True)
        selected = []; total_odd = 1.0
        for p in palpites:
            if p['odd'] < 1.25: continue
            if total_odd * p['odd'] > 25.0: continue
            selected.append(p)
            total_odd *= p['odd']
            if 8.0 <= total_odd <= 30.0:
                txt = f"\n🎟️ <b>MÚLTIPLA SNIPER (ODD {total_odd:.2f})</b> 🎯\n"
                for s in selected: txt += f"🔹 {s['match']}: {s['pick']} (@{s['odd']})\n"
                txt += "⚠️ <i>Aposte com responsabilidade.</i>\n"
                return txt
    return "\n⚠️ <i>Sem múltipla de alto valor hoje.</i>"

async def enviar_audio(context, game):
    text = f"Análise do jogo: {game['match']}."
    try:
        tts = gTTS(text=text, lang='pt'); tts.save("audio.mp3")
        with open("audio.mp3", "rb") as f: await context.bot.send_voice(chat_id=CHANNEL_ID, voice=f)
        os.remove("audio.mp3")
    except: pass

async def enviar_post(context, text, bilhete=""):
    kb = [[InlineKeyboardButton("📲 APOSTAR AGORA", url=get_random_link())]]
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text+bilhete, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e: logger.error(f"Erro envio: {e}")

async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_soccer_grade()
    if not games: return False 
    chunks = [games[i:i + 10] for i in range(0, len(games), 10)]
    for i, chunk in enumerate(chunks):
        header = "☀️ <b>BOM DIA! GRADE V161</b> ☀️\n\n" if i == 0 else "👇 <b>MAIS JOGOS...</b>\n\n"
        msg = header
        for g in chunk:
            icon = "💎" if g['is_vip'] else "⚽"
            if i==0 and g == chunk[0]: await enviar_audio(context, g); icon = "⭐ <b>DESTAQUE</b>\n"
            reports = "\n".join(g['report'])
            msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
        bilhete = gerar_bilhete(engine.daily_accumulator) if i == len(chunks)-1 else ""
        await enviar_post(context, msg, bilhete)
    return True

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_nba_games()
    if not games: return False
    msg = "🏀 <b>NBA - PLAYER PROPS</b> 🏀\n\n"
    for g in games[:8]:
        icon = "⭐" if g['is_vip'] else "🏀"
        reports = "\n".join(g['report'])
        msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
    await enviar_post(context, msg)
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
        [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Limpar Cache", callback_data="force")]
    ]
    await update.message.reply_text("🦁 <b>BOT V161 ONLINE</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "menu":
        kb = [[InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
              [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Limpar Cache", callback_data="force")]]
        await q.edit_message_text("🦁 <b>MENU V161</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    
    elif q.data == "fut":
        await q.message.reply_text("⏳ <b>Buscando a grade completa...</b>", parse_mode=ParseMode.HTML)
        sucesso = await daily_soccer_job(context)
        if sucesso: await q.message.reply_text("✅ Feito.")
        else: await q.message.reply_text("❌ <b>Nenhum jogo encontrado agora.</b> (A API pode ter fechado as odds dos jogos da tarde ou sua cota excedeu).", parse_mode=ParseMode.HTML)
    
    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Analisando NBA...</b>", parse_mode=ParseMode.HTML)
        await daily_nba_job(context)
        await q.message.reply_text("✅ Feito.")
    
    elif q.data == "force":
        engine.soccer_cache = []
        engine.nba_cache = []
        engine.soccer_last_update = None
        await q.message.reply_text("🔄 <b>Cache Limpo!</b> O próximo clique puxará odds frescas da API UK.", parse_mode=ParseMode.HTML)
        
    elif q.data == "status":
        rep = await engine.test_all_connections()
        await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data="menu")]]), parse_mode=ParseMode.HTML)

def main():
    if not BOT_TOKEN: print("ERRO: Configure o BOT_TOKEN no .env"); return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_error_handler(error_handler)
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=8, minute=0, tzinfo=timezone(timedelta(hours=-3))))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=18, minute=0, tzinfo=timezone(timedelta(hours=-3))))
    print("BOT V161 RODANDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
