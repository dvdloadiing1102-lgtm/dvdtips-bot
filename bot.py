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
PORT = int(os.getenv("PORT", 10000))
AFFILIATE_LINK = os.getenv("AFFILIATE_LINK", "https://www.bet365.com") 
ADMIN_ID = os.getenv("ADMIN_ID")
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

SENT_LINKS = set()
LATEST_HEADLINES = []

# LISTA VIP (PONTUAÇÃO MÁXIMA - TOPO DA LISTA)
VIP_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO", "GREMIO", "INTERNACIONAL",
    "REAL MADRID", "BARCELONA", "ATLETICO MADRID",
    "MANCHESTER CITY", "LIVERPOOL", "ARSENAL", "CHELSEA", "MANCHESTER UNITED", "TOTTENHAM", "NEWCASTLE",
    "PSG", "BAYERN MUNICH", "DORTMUND", "LEVERKUSEN",
    "INTER MILAN", "AC MILAN", "JUVENTUS", "NAPOLI",
    "INTER MIAMI", "AL NASSR", "AL HILAL"
]

# LIGAS ELITE (PESOS DE PRIORIDADE)
SOCCER_LEAGUES = [
    {"key": "soccer_england_premier_league", "name": "PREMIER LEAGUE", "weight": 2000},
    {"key": "soccer_uefa_champs_league", "name": "CHAMPIONS LEAGUE", "weight": 2000},
    {"key": "soccer_brazil_campeonato", "name": "BRASILEIRÃO A", "weight": 1500},
    {"key": "soccer_spain_la_liga", "name": "LA LIGA", "weight": 1000},
    {"key": "soccer_italy_serie_a", "name": "SERIE A", "weight": 1000},
    {"key": "soccer_germany_bundesliga", "name": "BUNDESLIGA", "weight": 1000},
    {"key": "soccer_france_ligue_one", "name": "LIGUE 1", "weight": 500},
    {"key": "soccer_brazil_campeonato_paulista", "name": "PAULISTA A1", "weight": 200},
    {"key": "soccer_brazil_campeonato_carioca", "name": "CARIOCA A1", "weight": 200}
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V126 - FULL TRANSPARENCY")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    global LATEST_HEADLINES
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação"]
        blacklist = ["bbb", "festa", "namorada"]
        LATEST_HEADLINES = [entry.title for entry in feed.entries[:20]]
        c=0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            if any(w in entry.title.lower() for w in whitelist) and not any(b in entry.title.lower() for b in blacklist):
                await context.bot.send_message(chat_id=CHANNEL_ID, text=f"⚠️ **BOLETIM**\n\n📰 {entry.title}\n🔗 {entry.link}")
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break
        if len(SENT_LINKS)>500: SENT_LINKS.clear()
    except: pass

# ================= MOTOR V126 (TRANSPARÊNCIA TOTAL) =================
class SportsEngine:
    def __init__(self):
        self.daily_accumulator = []

    async def test_all_connections(self):
        report = "📊 **STATUS V126**\n\n"
        mem = psutil.virtual_memory()
        report += f"💻 RAM: {mem.percent}%\n"
        
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: 
                        rem = r.headers.get("x-requests-remaining", "?")
                        report += f"✅ The Odds API: {rem} restantes\n"
                    else: report += "❌ The Odds API: Erro Key\n"
                except: report += "❌ The Odds API: Erro Conexão\n"
        else: report += "⚠️ CHAVE API FALTANDO\n"
        return report

    async def fetch_odds(self, sport_key, display_name, weight):
        if not THE_ODDS_API_KEY: return []
        
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=us&oddsFormat=decimal&markets=h2h&apiKey={THE_ODDS_API_KEY}"
        
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                r = await client.get(url)
                data = r.json()
                if not isinstance(data, list): return []
                
                games = []
                now = datetime.now(timezone.utc)
                limit_time = now + timedelta(hours=30) 
                
                for event in data:
                    try:
                        evt_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        if evt_time > limit_time or evt_time < now: continue 

                        time_str = evt_time.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
                        h = event['home_team']
                        a = event['away_team']
                        
                        match_score = weight 
                        h_norm = normalize_name(h)
                        a_norm = normalize_name(a)
                        
                        is_vip = False
                        for vip in VIP_TEAMS:
                            if vip in h_norm or vip in a_norm:
                                match_score += 100000 
                                is_vip = True
                                break
                        
                        odds_h, odds_a, odds_d = 0, 0, 0
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = max(odds_h, o['price'])
                                        if o['name'] == a: odds_a = max(odds_a, o['price'])
                                        if o['name'] == 'Draw': odds_d = max(odds_d, o['price'])
                        
                        if odds_h > 1.0 and odds_a > 1.0:
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

    def analyze_game(self, game):
        lines = []
        best_pick = None
        
        has_news = False
        for news in LATEST_HEADLINES:
            if normalize_name(game['home']) in normalize_name(news) or normalize_name(game['away']) in normalize_name(news):
                has_news = True
        
        if has_news: lines.append("📰 **Radar:** Notícias recentes detectadas no GE.")

        oh, oa, od = game['odd_h'], game['odd_a'], game['odd_d']

        # ESTRATÉGIA: SEGURANÇA (Verde)
        if 1.15 < oh < 1.75:
            lines.append(f"🟢 **Segura:** {game['home']} Vence (@{oh})")
            best_pick = {"pick": f"{game['home']}", "odd": oh, "match": game['match']}
        elif 1.15 < oa < 1.75:
            lines.append(f"🟢 **Segura:** {game['away']} Vence (@{oa})")
            best_pick = {"pick": f"{game['away']}", "odd": oa, "match": game['match']}
        elif 1.25 < oh < 2.30 and od > 0:
            dc_odd = round(1 / (1/oh + 1/od), 2)
            if 1.15 < dc_odd < 1.60:
                 lines.append(f"🟢 **Segura:** {game['home']} ou Empate (@{dc_odd})")
                 if not best_pick: best_pick = {"pick": f"{game['home']} ou Empate", "odd": dc_odd, "match": game['match']}

        # SE NÃO ACHOU SEGURA, MOSTRA O QUE TEM (V126)
        if not lines:
            if oh < 2.05: 
                lines.append(f"🟡 **Valor:** {game['home']} (@{oh})")
                best_pick = {"pick": f"{game['home']}", "odd": oh, "match": game['match']}
            elif oa < 2.05: 
                lines.append(f"🟡 **Valor:** {game['away']} (@{oa})")
                best_pick = {"pick": f"{game['away']}", "odd": oa, "match": game['match']}
            else: 
                # AQUI ESTÁ A MUDANÇA: MOSTRA A ODD CRUA
                lines.append(f"⚖️ **Equilibrado:** Casa @{oh} | Fora @{oa}")

        return lines, best_pick

    async def get_soccer_grade(self):
        all_games = []
        self.daily_accumulator = []
        
        for league in SOCCER_LEAGUES:
            games = await self.fetch_odds(league['key'], league['name'], league['weight'])
            for g in games:
                report, pick = self.analyze_game(g)
                g['report'] = report
                if pick: self.daily_accumulator.append(pick)
                all_games.append(g)
            await asyncio.sleep(0.5)

        # Ordena pelo Score (VIP) e Hora
        all_games.sort(key=lambda x: (-x['match_score'], x['datetime']))
        
        return all_games

    async def get_nba_games(self):
        games = await self.fetch_odds("basketball_nba", "NBA", 500)
        processed = []
        for g in games:
            report, _ = self.analyze_game(g)
            g['report'] = report
            processed.append(g)
        return processed

    async def get_ufc_games(self):
        games = await self.fetch_odds("mma_mixed_martial_arts", "UFC/MMA", 500)
        return games

engine = SportsEngine()

def gerar_texto_bilhete(palpites):
    if not palpites: return ""
    selected = []
    total_odd = 1.0
    import random
    random.shuffle(palpites)
    
    # Prioriza VIPs
    palpites.sort(key=lambda x: 1 if "Real" in x['match'] or "City" in x['match'] or "Flamengo" in x['match'] or "Arsenal" in x['match'] else 0, reverse=True)

    for p in palpites:
        if total_odd > 20: break 
        selected.append(p)
        total_odd *= p['odd']
        
    if total_odd < 3.0: return ""
    
    txt = f"\n🎟️ **BILHETE LUNÁTICO (ODD {total_odd:.2f})** 🚀\n"
    for s in selected: txt += f"🎯 {s['match']}: {s['pick']} (@{s['odd']})\n"
    txt += "⚠️ *Alto Risco. Aposte com moderação.*\n"
    return txt

async def enviar_com_botao(context, text, poll_data=None, bilhete_txt=""):
    full_text = text + bilhete_txt
    kb = [[InlineKeyboardButton("💸 Apostar Agora", url=AFFILIATE_LINK)]]
    try: 
        await context.bot.send_message(chat_id=CHANNEL_ID, text=full_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        if poll_data:
            await asyncio.sleep(2)
            await context.bot.send_poll(chat_id=CHANNEL_ID, question=f"Quem ganha: {poll_data['h']} x {poll_data['a']}?", options=[poll_data['h'], "Empate", poll_data['a']], is_anonymous=True)
    except: pass

async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Reiniciando...")
    os.execl(sys.executable, sys.executable, *sys.argv)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rep = await engine.test_all_connections()
    await update.message.reply_text(rep, parse_mode=ParseMode.MARKDOWN)

async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_soccer_grade()
    if not games: return
    
    top_games = games[:7]
    
    msg = f"🔥 **DOSSIÊ V126 (TRANSPARENTE)** 🔥\n\n"
    poll_data = None
    
    for i, g in enumerate(top_games):
        is_main = (i == 0)
        icon = "⭐ **JOGO DO DIA** ⭐\n" if is_main else ""
        if g['is_vip']: icon = "💎 **SUPER VIP** 💎\n"
        
        if is_main: poll_data = {"h": g['home'], "a": g['away']}
        
        block = "\n".join(g['report'])
        msg += f"{icon}🏆 **{g['league']}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
    
    bilhete = gerar_texto_bilhete(engine.daily_accumulator)
    await enviar_com_botao(context, msg, poll_data, bilhete)

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_nba_games()
    if not games: return
    msg = f"🏀 **NBA PRIME V126** 🏀\n\n"
    for g in games[:3]:
        block = "\n".join(g['report'])
        msg += f"🏟 **{g['league']}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
    await enviar_com_botao(context, msg)

async def daily_ufc_job(context: ContextTypes.DEFAULT_TYPE):
    fights = await engine.get_ufc_games()
    if not fights: return
    msg = "🥊 **UFC FIGHT DAY (V126)** 🥊\n\n"
    for f in fights[:6]:
        msg += f"⏰ {f['time']} | ⚔️ **{f['match']}**\n👊 {f['home']}: @{f['odd_h']}\n👊 {f['away']}: @{f['odd_a']}\n━━━━━━━━━━━━━━━━━━━━\n"
    await enviar_com_botao(context, msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔥 Futebol", callback_data="top_jogos"), InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
          [InlineKeyboardButton("🥊 UFC Manual", callback_data="ufc_fights"), InlineKeyboardButton("🔧 Status", callback_data="test_api")]]
    await update.message.reply_text("🦁 **PAINEL V126 - TRANSPARENTE**\nMostrando Odds de tudo!", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = q.data
    
    if data == "test_api":
        await q.edit_message_text("⏳ Check-up..."); rep = await engine.test_all_connections()
        kb = [[InlineKeyboardButton("Voltar", callback_data="top_jogos")]]
        await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN); return
    
    if data == "ufc_fights":
        await q.edit_message_text("🥊 Buscando Lutas..."); fights = await engine.get_ufc_games()
        if not fights: await q.message.reply_text("⚠️ Sem lutas."); return
        msg = "🥊 **UFC MANUAL** 🥊\n\n"
        for f in fights[:6]: msg += f"⏰ {f['time']} | ⚔️ **{f['match']}**\n👊 {f['home']}: @{f['odd_h']}\n👊 {f['away']}: @{f['odd_a']}\n━━━━━━━━━━━━━━━━━━━━\n"
        await enviar_com_botao(context, msg); await q.message.reply_text("✅ Postado!"); return

    if data == "nba_hoje":
        await q.edit_message_text("🏀 Buscando NBA..."); games = await engine.get_nba_games()
        if not games: await q.message.reply_text("⚠️ Sem jogos."); return
        msg = f"🏀 **NBA MANUAL** 🏀\n\n"
        for g in games[:3]:
            blk = "\n".join(g['report']); msg += f"🏟 **{g['league']}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"
        await enviar_com_botao(context, msg); await q.message.reply_text("✅ Postado!"); return

    if data == "top_jogos":
        await q.edit_message_text("⚽ Buscando Elite (V126)..."); games = await engine.get_soccer_grade()
        if not games: await q.message.reply_text("⚠️ Sem jogos Tier 1 com Odds."); return
        msg = f"🔥 **GRADE MANUAL V126**\n\n"
        poll_data = None
        for i, g in enumerate(games[:7]):
            is_main = (i == 0)
            icon = "⭐ **JOGO DO DIA** ⭐\n" if is_main else ""
            if g['is_vip']: icon = "💎 **SUPER VIP** 💎\n"
            if is_main: poll_data = {"h": g['home'], "a": g['away']}
            blk = "\n".join(g['report']); msg += f"{icon}🏆 **{g['league']}** • ⏰ {g['time']}\n⚔️ **{g['match']}**\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"
        
        bilhete = gerar_texto_bilhete(engine.daily_accumulator)
        await enviar_com_botao(context, msg, poll_data, bilhete)
        await q.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reboot", reboot_command))
    app.add_handler(CallbackQueryHandler(button))
    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=12, minute=0, tzinfo=timezone.utc))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=21, minute=0, tzinfo=timezone.utc))
        app.job_queue.run_daily(daily_ufc_job, time=time(hour=15, minute=0, tzinfo=timezone.utc), days=(4, 5))
    app.run_polling()

if __name__ == "__main__":
    main()
