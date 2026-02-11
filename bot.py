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

# --- CONFIGURAÇÕES ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 10000))
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_KEY")

# CONFIGURA A IA (COM PROTEÇÃO)
try:
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        print("✅ GEMINI AI: ATIVADA")
    else:
        model = None
        print("⚠️ GEMINI AI: DESATIVADA (Chave ausente)")
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
NBA_VIP_TEAMS = ["LAKERS", "CELTICS", "WARRIORS", "BUCKS", "SUNS", "NUGGETS", "HEAT", "MAVERICKS", "KNICKS", "76ERS"]

# LIGAS
SOCCER_LEAGUES = [
    {"key": "soccer_uefa_champs_league", "name": "CHAMPIONS LEAGUE", "score": 100},
    {"key": "soccer_conmebol_libertadores", "name": "LIBERTADORES", "score": 100},
    {"key": "soccer_epl", "name": "PREMIER LEAGUE", "score": 100},
    {"key": "soccer_brazil_campeonato", "name": "BRASILEIRÃO A", "score": 100},
    {"key": "soccer_spain_la_liga", "name": "LA LIGA", "score": 90},
    {"key": "soccer_italy_serie_a", "name": "SERIE A", "score": 90},
    {"key": "soccer_germany_bundesliga", "name": "BUNDESLIGA", "score": 90},
    {"key": "soccer_france_ligue_one", "name": "LIGUE 1", "score": 90},
    {"key": "soccer_portugal_primeira_liga", "name": "LIGA PORTUGAL", "score": 85},
    {"key": "soccer_netherlands_eredivisie", "name": "EREDIVISIE", "score": 85},
    {"key": "soccer_england_championship", "name": "CHAMPIONSHIP", "score": 85},
    {"key": "soccer_england_fa_cup", "name": "FA CUP", "score": 80},
    {"key": "soccer_england_efl_cup", "name": "EFL CUP", "score": 80},
    {"key": "soccer_italy_coppa_italia", "name": "COPA DA ITÁLIA", "score": 80},
    {"key": "soccer_germany_dfb_pokal", "name": "COPA DA ALEMANHA", "score": 80},
    {"key": "soccer_uefa_europa_league", "name": "EUROPA LEAGUE", "score": 80}
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V150 - MENU FIXED")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception:", exc_info=context.error)

# --- FUNÇÃO DE CONSULTA À IA (GEMINI) ---
async def get_ai_stats(match_name):
    if not model: return ""
    try:
        prompt = f"""
        Aja como um analista de apostas profissional.
        Para o jogo: {match_name}.
        Baseado no estilo tático RECENTE dos times, me dê APENAS UMA recomendação estatística de valor alto (Cantos, Cartões ou Gols).
        Responda EXATAMENTE neste formato curto:
        💡 IA Check: [Mercado] [Linha] (Motivo curto)
        """
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro AI: {e}")
        return ""

# --- JOB DE NOTÍCIAS ---
async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    global LATEST_HEADLINES
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "escalação", "desfalque", "dúvida", "titular", "banco", "suspenso"]
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
        report = "📊 <b>STATUS V150</b>\n"
        if THE_ODDS_API_KEY: report += "✅ API Odds: Conectada\n"
        if model: report += "✅ Google Gemini AI: Conectada\n"
        else: report += "❌ Google Gemini AI: Sem chave ou Erro\n"
        return report

    async def fetch_odds(self, sport_key, display_name, league_score=0, is_nba=False):
        if not THE_ODDS_API_KEY: return []
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=us&oddsFormat=decimal&markets=h2h&apiKey={THE_ODDS_API_KEY}"
        
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
                        
                        if is_nba:
                            tomorrow = today_date + timedelta(days=1)
                            is_valid = (evt_time_br.date() == today_date) or (evt_time_br.date() == tomorrow and evt_time_br.hour < 5)
                            if not is_valid: continue
                        else:
                            if evt_time_br.date() != today_date: continue
                        
                        time_str = evt_time_br.strftime("%H:%M")
                        h, a = event['home_team'], event['away_team']
                        h_norm = normalize_name(h); a_norm = normalize_name(a)
                        
                        match_score = league_score
                        is_vip = False
                        if is_nba:
                            match_score += 1000
                        else:
                            if any(t in h_norm or t in a_norm for t in TIER_S_TEAMS): match_score += 1000; is_vip = True
                            elif any(t in h_norm or t in a_norm for t in TIER_A_TEAMS): match_score += 500
                        
                        odds_h, odds_a, odds_d = 0, 0, 0
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = max(odds_h, o['price'])
                                        if o['name'] == a: odds_a = max(odds_a, o['price'])
                                        if o['name'] == 'Draw': odds_d = max(odds_d, o['price'])
                        
                        if odds_h > 1.01 and odds_a > 1.01:
                            games.append({
                                "match": f"{h} x {a}", "league": display_name, 
                                "time": time_str, "datetime": evt_time_br, 
                                "odd_h": odds_h, "odd_a": odds_a, "odd_d": odds_d, 
                                "home": h, "away": a, "is_vip": is_vip,
                                "match_score": match_score, "is_nba": is_nba
                            })
                    except: continue
                return games
            except: return []

    async def analyze_game_async(self, game):
        lines = []
        best_pick = None
        oh, oa, od = game['odd_h'], game['odd_a'], game['odd_d']
        
        # --- NBA ---
        if game.get('is_nba'):
            if oh < 1.50:
                handicap = "-4.5" if oh > 1.35 else ("-7.5" if oh > 1.25 else "-9.5")
                lines.append(f"🏀 <b>Vencedor:</b> {game['home']} (@{oh})")
                lines.append(f"📉 <b>Handicap:</b> {game['home']} {handicap}")
                best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}
            elif oa < 1.50:
                handicap = "-4.5" if oa > 1.35 else ("-7.5" if oa > 1.25 else "-9.5")
                lines.append(f"🏀 <b>Vencedor:</b> {game['away']} (@{oa})")
                lines.append(f"📉 <b>Handicap:</b> {game['away']} {handicap}")
                best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}
            else:
                lines.append("🔥 <b>Clutch Time (Jogo Parelho)</b>")
                if 1.80 < oh < 2.20: lines.append(f"💎 <b>Valor:</b> {game['home']} (@{oh})")
                elif 1.80 < oa < 2.20: lines.append(f"💎 <b>Valor:</b> {game['away']} (@{oa})")
            return lines, best_pick

        # --- FUTEBOL ---
        market_stats = ""
        # Análise matemática simples
        if oh < 1.30 or oa < 1.30: market_stats = "📊 <i>Mercado: Over 2.5 Gols provável</i>"
        elif od < 3.05: market_stats = "📊 <i>Mercado: Under 2.5 Gols (Jogo Truncado)</i>"
        
        # Análise IA (Só VIP e se o modelo existir)
        ai_msg = ""
        if game.get('is_vip') and model:
            ai_msg = await get_ai_stats(game['match'])
        
        # Pick 1x2
        if oh < 1.55:
            lines.append(f"🔥 <b>Favorito:</b> {game['home']} (@{oh})")
            best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}
        elif oa < 1.55:
            lines.append(f"🔥 <b>Favorito:</b> {game['away']} (@{oa})")
            best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}
        else:
            if oh < oa: 
                dnb = round(oh * 0.75, 2)
                lines.append(f"♻️ <b>Empate Anula:</b> {game['home']} (@{dnb})")
                lines.append(f"🛡️ <b>Dupla Chance:</b> 1X (@{round(1/(1/oh+1/od),2)})")
                best_pick = {"pick": f"DNB {game['home']}", "odd": dnb, "match": game['match']}
            else:
                dnb = round(oa * 0.75, 2)
                lines.append(f"♻️ <b>Empate Anula:</b> {game['away']} (@{dnb})")
                lines.append(f"🛡️ <b>Dupla Chance:</b> X2 (@{round(1/(1/oa+1/od),2)})")
                best_pick = {"pick": f"DNB {game['away']}", "odd": dnb, "match": game['match']}

        if ai_msg: lines.append(ai_msg)
        elif market_stats: lines.append(market_stats)
        
        return lines, best_pick

    async def get_soccer_grade(self):
        all_games = []
        self.daily_accumulator = []
        for league in SOCCER_LEAGUES:
            games = await self.fetch_odds(league['key'], league['name'], league['score'], is_nba=False)
            for g in games:
                report, pick = await self.analyze_game_async(g)
                g['report'] = report
                if pick: self.daily_accumulator.append(pick)
                all_games.append(g)
            await asyncio.sleep(0.1)
        if not all_games: return []
        all_games.sort(key=lambda x: (-x['match_score'], x['datetime']))
        return all_games

    async def get_nba_games(self):
        games = await self.fetch_odds("basketball_nba", "NBA", 50, is_nba=True)
        processed = []
        for g in games: 
            report, _ = await self.analyze_game_async(g) 
            g['report'] = report
            processed.append(g)
        processed.sort(key=lambda x: (-x['match_score'], x['datetime']))
        return processed

engine = SportsEngine()

# --- MÚLTIPLA 10x-20x ---
def gerar_bilhete(palpites):
    if len(palpites) < 3: return ""
    for _ in range(500):
        random.shuffle(palpites)
        palpites.sort(key=lambda x: 1 if any(t in x['match'].upper() for t in TIER_S_TEAMS + TIER_A_TEAMS) else 0, reverse=True)
        selected = []; total_odd = 1.0
        for p in palpites:
            if p['odd'] < 1.28: continue
            if total_odd * p['odd'] > 21.0: continue
            selected.append(p)
            total_odd *= p['odd']
            if 10.0 <= total_odd <= 20.0:
                txt = f"\n🎟️ <b>MÚLTIPLA SNIPER (ODD {total_odd:.2f})</b> 🎯\n"
                for s in selected: txt += f"🔹 {s['match']}: {s['pick']} (@{s['odd']})\n"
                txt += "⚠️ <i>Aposte com responsabilidade.</i>\n"
                return txt
    return "\n⚠️ <i>Sem múltipla segura (10x-20x) hoje.</i>"

async def enviar_audio(context, game):
    text = f"Destaque: {game['match']}."
    bet = game['report'][0].replace("<b>","").replace("</b>","").replace("🔥","").replace("🛡️","").replace("♻️","").replace("📉","")
    text += f" Palpite: {bet}. "
    if "Over" in str(game['report']): text += "Promessa de gols."
    try:
        tts = gTTS(text=text, lang='pt'); tts.save("audio.mp3")
        with open("audio.mp3", "rb") as f: await context.bot.send_voice(chat_id=CHANNEL_ID, voice=f)
        os.remove("audio.mp3")
    except: pass

async def enviar_post(context, text, bilhete=""):
    kb = [[InlineKeyboardButton("📲 APOSTAR AGORA", url=get_random_link())]]
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text+bilhete, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e: logger.error(f"Erro envio: {e}")

# --- JOBS ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_soccer_grade()
    if not games: return
    chunks = [games[i:i + 10] for i in range(0, len(games), 10)]
    for i, chunk in enumerate(chunks):
        header = "☀️ <b>BOM DIA! FUTEBOL HOJE</b> ☀️\n\n" if i == 0 else "👇 <b>MAIS JOGOS...</b>\n\n"
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
    msg = "🏀 <b>NBA - RODADA DA NOITE</b> 🏀\n\n"
    for g in games[:8]:
        icon = "⭐" if g['is_vip'] else "🏀"
        reports = "\n".join(g['report'])
        msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
    await enviar_post(context, msg)

# --- START COM BOTÕES ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
        [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Forçar Update", callback_data="force")]
    ]
    await update.message.reply_text(
        "🦁 <b>BOT V150 ONLINE</b>\nIA Ativada. Botões Restaurados.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "menu":
        kb = [[InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
              [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Forçar Update", callback_data="force")]]
        await q.edit_message_text("🦁 <b>MENU V150</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    elif q.data == "fut":
        await q.message.reply_text("⏳ <b>IA Analisando Jogos...</b>", parse_mode=ParseMode.HTML)
        await daily_soccer_job(context)
        await q.message.reply_text("✅ Enviado!")
    elif q.data == "nba":
        await q.message.reply_text("🏀 <b>Buscando NBA...</b>", parse_mode=ParseMode.HTML)
        await daily_nba_job(context)
        await q.message.reply_text("✅ Enviado!")
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
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_error_handler(error_handler)
    
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=8, minute=0, tzinfo=timezone(timedelta(hours=-3))))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=18, minute=0, tzinfo=timezone(timedelta(hours=-3))))
    
    print("BOT V150 RODANDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
