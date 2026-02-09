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
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

AFFILIATE_LINKS = ["https://www.bet365.com", "https://br.betano.com", "https://stake.com"]
def get_random_link(): return random.choice(AFFILIATE_LINKS)

SENT_LINKS = set()
LATEST_HEADLINES = []

VIP_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO", "GREMIO", "INTERNACIONAL",
    "REAL MADRID", "BARCELONA", "ATLETICO MADRID",
    "MANCHESTER CITY", "LIVERPOOL", "ARSENAL", "CHELSEA", "MANCHESTER UNITED", "TOTTENHAM", "NEWCASTLE",
    "PSG", "BAYERN MUNICH", "DORTMUND", "LEVERKUSEN",
    "INTER MILAN", "AC MILAN", "JUVENTUS", "NAPOLI",
    "INTER MIAMI", "AL NASSR", "AL HILAL"
]

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
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V134 - DEBUG MODE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # Tenta avisar o admin se der erro grave
    try:
        if update.callback_query:
            await update.callback_query.message.reply_text(f"❌ <b>ERRO INTERNO:</b> {context.error}", parse_mode=ParseMode.HTML)
    except: pass

async def auto_news_job(context: ContextTypes.DEFAULT_TYPE):
    global LATEST_HEADLINES
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        whitelist = ["lesão", "vetado", "fora", "contratado", "vendido", "reforço", "escalação", "desfalque", "dúvida"]
        blacklist = ["bbb", "festa", "namorada", "reality"]
        if feed.entries: LATEST_HEADLINES = [entry.title for entry in feed.entries[:20]]
        c=0
        for entry in feed.entries:
            if entry.link in SENT_LINKS: continue
            title_lower = entry.title.lower()
            if any(w in title_lower for w in whitelist) and not any(b in title_lower for b in blacklist):
                msg = f"⚠️ <b>BOLETIM</b>\n\n📰 {entry.title}\n🔗 {entry.link}"
                # REMOVIDO try/except para mostrar erro no log se falhar
                await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode=ParseMode.HTML)
                SENT_LINKS.add(entry.link)
                c+=1
                if c>=2: break
        if len(SENT_LINKS)>500: SENT_LINKS.clear()
    except Exception as e: logger.error(f"Erro News: {e}")

# ================= MOTOR V134 =================
class SportsEngine:
    def __init__(self): self.daily_accumulator = []

    async def test_all_connections(self):
        report = "📊 <b>STATUS V134</b>\n\n"
        mem = psutil.virtual_memory()
        report += f"💻 RAM: {mem.percent}%\n"
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: 
                        rem = r.headers.get("x-requests-remaining", "?")
                        report += f"✅ API Odds: {rem} rest.\n"
                    else: report += f"❌ API Odds: Erro {r.status_code}\n"
                except: report += "❌ API Odds: Erro Conexão\n"
        else: report += "❌ API Key não configurada.\n"
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
                        h, a = event['home_team'], event['away_team']
                        match_score = weight 
                        h_norm = normalize_name(h); a_norm = normalize_name(a)
                        is_vip = False
                        for vip in VIP_TEAMS:
                            if vip in h_norm or vip in a_norm: match_score += 100000; is_vip = True; break
                        odds_h, odds_a, odds_d = 0, 0, 0
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = max(odds_h, o['price'])
                                        if o['name'] == a: odds_a = max(odds_a, o['price'])
                                        if o['name'] == 'Draw': odds_d = max(odds_d, o['price'])
                        if odds_h > 1.0 and odds_a > 1.0:
                            games.append({"match": f"{h} x {a}", "league": display_name, "time": time_str, "datetime": evt_time, "odd_h": odds_h, "odd_a": odds_a, "odd_d": odds_d, "home": h, "away": a, "match_score": match_score, "is_vip": is_vip})
                    except: continue
                return games
            except: return []

    def calculate_stats(self, odd):
        try:
            prob = round((1 / odd) * 100, 1)
            stake = "1 Unidade"
            risk = "🟢 Baixo"
            if odd >= 1.70 and odd < 2.20: stake = "0.75 Unidade"; risk = "🟡 Médio"
            elif odd >= 2.20: stake = "0.5 Unidade"; risk = "🔴 Alto"
            return prob, stake, risk
        except: return 0, "?", "?"

    def analyze_game(self, game):
        lines = []
        best_pick = None
        has_news = False
        for news in LATEST_HEADLINES:
            if normalize_name(game['home']) in normalize_name(news) or normalize_name(game['away']) in normalize_name(news): has_news = True
        if has_news: lines.append("📰 <b>Radar:</b> Notícias recentes no GE.")
        oh, oa, od = game['odd_h'], game['odd_a'], game['odd_d']
        pick_odd = 0
        if 1.15 < oh < 1.75:
            lines.append(f"🟢 <b>Segura:</b> {game['home']} Vence (@{oh})")
            best_pick = {"pick": f"{game['home']}", "odd": oh, "match": game['match']}; pick_odd = oh
        elif 1.15 < oa < 1.75:
            lines.append(f"🟢 <b>Segura:</b> {game['away']} Vence (@{oa})")
            best_pick = {"pick": f"{game['away']}", "odd": oa, "match": game['match']}; pick_odd = oa
        elif 1.25 < oh < 2.30 and od > 0:
            dc_odd = round(1 / (1/oh + 1/od), 2)
            if 1.15 < dc_odd < 1.60:
                 lines.append(f"🟢 <b>Segura:</b> {game['home']} ou Empate (@{dc_odd})")
                 if not best_pick: best_pick = {"pick": f"{game['home']} ou Empate", "odd": dc_odd, "match": game['match']}; pick_odd = dc_odd
        if not lines:
            if oh > 3.00 and oa < 1.50 and od > 0:
                dc_zebra = round(1 / (1/oh + 1/od), 2)
                if dc_zebra > 1.90: lines.append(f"🦓 <b>ZEBRA ALERTA:</b> {game['home']} ou Empate (@{dc_zebra})")
            if oa > 3.00 and oh < 1.50 and od > 0:
                dc_zebra = round(1 / (1/oa + 1/od), 2)
                if dc_zebra > 1.90: lines.append(f"🦓 <b>ZEBRA ALERTA:</b> {game['away']} ou Empate (@{dc_zebra})")
        if not lines:
            if oh < 2.05: 
                lines.append(f"🟡 <b>Valor:</b> {game['home']} (@{oh})")
                best_pick = {"pick": f"{game['home']}", "odd": oh, "match": game['match']}; pick_odd = oh
            elif oa < 2.05: 
                lines.append(f"🟡 <b>Valor:</b> {game['away']} (@{oa})")
                best_pick = {"pick": f"{game['away']}", "odd": oa, "match": game['match']}; pick_odd = oa
            else: 
                lines.append(f"⚖️ <b>Equilibrado:</b> Casa @{oh} | Fora @{oa}")
        if pick_odd > 0:
            prob, stake, risk = self.calculate_stats(pick_odd)
            lines.append(f"📊 <b>Prob:</b> {prob}% | ⚖️ <b>Stake:</b> {stake}")
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
        all_games.sort(key=lambda x: (-x['match_score'], x['datetime']))
        return all_games
    
    async def get_nba_games(self):
        games = await self.fetch_odds("basketball_nba", "NBA", 500)
        processed = []
        for g in games: report, _ = self.analyze_game(g); g['report'] = report; processed.append(g)
        return processed
    
    async def get_ufc_games(self): return await self.fetch_odds("mma_mixed_martial_arts", "UFC/MMA", 500)

engine = SportsEngine()

def gerar_texto_bilhete(palpites):
    if not palpites: return ""
    selected = []
    total_odd = 1.0
    import random
    random.shuffle(palpites)
    palpites.sort(key=lambda x: 1 if "Real" in x['match'] or "City" in x['match'] or "Flamengo" in x['match'] or "Arsenal" in x['match'] else 0, reverse=True)
    for p in palpites:
        if total_odd > 20: break 
        selected.append(p)
        total_odd *= p['odd']
    if total_odd < 3.0: return ""
    txt = f"\n🎟️ <b>BILHETE LUNÁTICO (ODD {total_odd:.2f})</b> 🚀\n"
    for s in selected: txt += f"🎯 {s['match']}: {s['pick']} (@{s['odd']})\n"
    txt += "⚠️ <i>Alto Risco. Aposte com moderação.</i>\n"
    return txt

async def enviar_audio_narracao(context, game):
    text = f"Destaque do dia: {game['match']} pela {game['league']}. "
    found_bet = False
    for line in game['report']:
        if "Segura" in line or "Valor" in line:
            clean = line.replace("<b>", "").replace("</b>", "").replace("🟢", "").replace("🟡", "")
            text += f"Nossa análise indica: {clean}. "
            found_bet = True
            break
    if not found_bet: text += "Jogo equilibrado, confira as odds."
    text += "Boa sorte!"
    try:
        tts = gTTS(text=text, lang='pt')
        tts.save("narracao.mp3")
        with open("narracao.mp3", "rb") as audio: await context.bot.send_voice(chat_id=CHANNEL_ID, voice=audio)
        os.remove("narracao.mp3")
    except: pass

async def enviar_com_botao(context, text, poll_data=None, bilhete_txt=""):
    full_text = text + bilhete_txt
    link = get_random_link()
    kb = [[InlineKeyboardButton("💸 Apostar Agora", url=link)]]
    try: 
        await context.bot.send_message(chat_id=CHANNEL_ID, text=full_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        if poll_data:
            await asyncio.sleep(2)
            await context.bot.send_poll(chat_id=CHANNEL_ID, question=f"Quem ganha: {poll_data['h']} x {poll_data['a']}?", options=["🔥 Casa", "🤝 Empate", "🤑 Visitante"], is_anonymous=True)
    except Exception as e:
        # AQUI ESTÁ A CORREÇÃO V134: AVISA O ADMIN/USUÁRIO DO ERRO
        logger.error(f"Erro ao postar no canal: {e}")
        # Tenta mandar para o Admin se falhar no canal
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ <b>ERRO NO CANAL:</b>\nNão consegui postar a mensagem.\n\n<b>Motivo:</b> {str(e)}\n\n<i>Dica: Verifique se o Bot é Administrador do Canal.</i>", parse_mode=ParseMode.HTML)
        except: pass

async def sos_red_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🆘 <b>SOS RED</b> 🆘\n\nCalma, guerreiro. Dia ruim acontece.\n\n1. Pare por hoje.\n2. Não tente recuperar tudo de uma vez.\n3. Respeite sua gestão (1% a 2% da banca)."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def testar_news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🕵️‍♂️ <b>Testando Radar de Notícias...</b>", parse_mode=ParseMode.HTML)
    try:
        def get_feed(): return feedparser.parse("https://ge.globo.com/rss/ge/")
        feed = await asyncio.get_running_loop().run_in_executor(None, get_feed)
        if not feed.entries: await update.message.reply_text("❌ Erro: Feed vazio."); return
        msg = f"✅ <b>Feed OK! {len(feed.entries)} notícias.</b>\n\n<b>Últimas 3:</b>\n"
        for entry in feed.entries[:3]: msg += f"- {entry.title}\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except Exception as e: await update.message.reply_text(f"❌ Erro Crítico: {str(e)}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📚 <b>Dicionário</b>\n\n🟢 <b>Segura:</b> Odd baixa.\n🟡 <b>Valor:</b> Odd média.\n📊 <b>Prob:</b> Chance.\n⚖️ <b>Stake:</b> Aposta."
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔥 Futebol", callback_data="top_jogos"), InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
        [InlineKeyboardButton("🥊 UFC Manual", callback_data="ufc_fights"), InlineKeyboardButton("🔧 Status", callback_data="test_api")],
        [InlineKeyboardButton("🆘 SOS Red", callback_data="sos_help"), InlineKeyboardButton("📚 Dicionário", callback_data="help_msg")]
    ]
    await update.message.reply_text("🦁 <b>MENU COMPLETO V134</b>\nEscolha uma função:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Reiniciando...")
    os.execl(sys.executable, sys.executable, *sys.argv)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rep = await engine.test_all_connections()
    await update.message.reply_text(rep, parse_mode=ParseMode.HTML)

async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_soccer_grade()
    if not games: return
    top_games = games[:7]
    msg = f"🔥 <b>DOSSIÊ V134 (DEBUG)</b> 🔥\n\n"
    poll_data = None
    for i, g in enumerate(top_games):
        is_main = (i == 0)
        icon = "⭐ <b>JOGO DO DIA</b> ⭐\n" if is_main else ""
        if g['is_vip']: icon = "💎 <b>SUPER VIP</b> 💎\n"
        if is_main: 
            poll_data = {"h": g['home'], "a": g['away']}
            await enviar_audio_narracao(context, g)
        block = "\n".join(g['report'])
        msg += f"{icon}🏆 <b>{g['league']}</b> • ⏰ {g['time']}\n⚔️ <b>{g['match']}</b>\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
    bilhete = gerar_texto_bilhete(engine.daily_accumulator)
    await enviar_com_botao(context, msg, poll_data, bilhete)

async def daily_nba_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_nba_games()
    if not games: return
    msg = f"🏀 <b>NBA PRIME V134</b> 🏀\n\n"
    for g in games[:3]:
        block = "\n".join(g['report'])
        msg += f"🏟 <b>{g['league']}</b> • ⏰ {g['time']}\n⚔️ <b>{g['match']}</b>\n{block}\n━━━━━━━━━━━━━━━━━━━━\n"
    await enviar_com_botao(context, msg)

async def daily_ufc_job(context: ContextTypes.DEFAULT_TYPE):
    fights = await engine.get_ufc_games()
    if not fights: return
    msg = "🥊 <b>UFC FIGHT DAY (V134)</b> 🥊\n\n"
    for f in fights[:6]: msg += f"⏰ {f['time']} | ⚔️ <b>{f['match']}</b>\n👊 {f['home']}: @{f['odd_h']}\n👊 {f['away']}: @{f['odd_a']}\n━━━━━━━━━━━━━━━━━━━━\n"
    await enviar_com_botao(context, msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🦁 ABRIR MENU COMPLETO", callback_data="open_menu")]]
    await update.message.reply_text("🦁 <b>PAINEL V134</b>\nModo Debug Ativado.\n<i>Se der erro, ele vai te avisar.</i>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = q.data
    # AVISA QUE ESTÁ RODANDO NO CHAT PRIVADO
    if data != "open_menu": await q.message.reply_text("⏳ <b>Processando... Aguarde...</b>", parse_mode=ParseMode.HTML)

    if data == "open_menu":
        kb = [
            [InlineKeyboardButton("🔥 Futebol", callback_data="top_jogos"), InlineKeyboardButton("🏀 NBA", callback_data="nba_hoje")],
            [InlineKeyboardButton("🥊 UFC Manual", callback_data="ufc_fights"), InlineKeyboardButton("🔧 Status", callback_data="test_api")],
            [InlineKeyboardButton("🆘 SOS Red", callback_data="sos_help"), InlineKeyboardButton("📚 Dicionário", callback_data="help_msg")]
        ]
        await q.edit_message_text("🦁 <b>MENU PRINCIPAL</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML); return

    if data == "test_api": rep = await engine.test_all_connections(); kb = [[InlineKeyboardButton("Voltar", callback_data="open_menu")]]; await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML); return
    if data == "help_msg": msg = "📚 <b>Dicionário</b>\n\n🟢 <b>Segura:</b> Odd baixa.\n🟡 <b>Valor:</b> Odd média.\n📊 <b>Prob:</b> Chance."; await q.message.reply_text(msg, parse_mode=ParseMode.HTML); return
    if data == "sos_help": msg = "🆘 <b>SOS RED</b>\nRespire fundo. Siga sua gestão de banca."; await q.message.reply_text(msg, parse_mode=ParseMode.HTML); return
    
    if data == "ufc_fights":
        fights = await engine.get_ufc_games(); 
        if not fights: await q.message.reply_text("⚠️ Sem lutas (API Retornou Vazio)."); return
        msg = "🥊 <b>UFC MANUAL</b> 🥊\n\n"; 
        for f in fights[:6]: msg += f"⏰ {f['time']} | ⚔️ <b>{f['match']}</b>\n👊 {f['home']}: @{f['odd_h']}\n👊 {f['away']}: @{f['odd_a']}\n━━━━━━━━━━━━━━━━━━━━\n"; await enviar_com_botao(context, msg); await q.message.reply_text("✅ Postado!"); return
    
    if data == "nba_hoje":
        games = await engine.get_nba_games(); 
        if not games: await q.message.reply_text("⚠️ Sem jogos (API Retornou Vazio)."); return
        msg = f"🏀 <b>NBA MANUAL</b> 🏀\n\n"; 
        for g in games[:3]: blk = "\n".join(g['report']); msg += f"🏟 <b>{g['league']}</b> • ⏰ {g['time']}\n⚔️ <b>{g['match']}</b>\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"; await enviar_com_botao(context, msg); await q.message.reply_text("✅ Postado!"); return
    
    if data == "top_jogos":
        games = await engine.get_soccer_grade(); 
        if not games: await q.message.reply_text("⚠️ Sem jogos (API Retornou Vazio)."); return
        msg = f"🔥 <b>GRADE MANUAL V134</b>\n\n"; poll_data = None
        for i, g in enumerate(games[:7]):
            is_main = (i == 0); icon = "⭐ <b>JOGO DO DIA</b> ⭐\n" if is_main else ""; 
            if g['is_vip']: icon = "💎 <b>SUPER VIP</b> 💎\n"
            if is_main: poll_data = {"h": g['home'], "a": g['away']}; await enviar_audio_narracao(context, g)
            blk = "\n".join(g['report']); msg += f"{icon}🏆 <b>{g['league']}</b> • ⏰ {g['time']}\n⚔️ <b>{g['match']}</b>\n{blk}\n━━━━━━━━━━━━━━━━━━━━\n"
        bilhete = gerar_texto_bilhete(engine.daily_accumulator); await enviar_com_botao(context, msg, poll_data, bilhete); await q.message.reply_text("✅ Postado!")

def main():
    if not BOT_TOKEN: return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reboot", reboot_command))
    app.add_handler(CommandHandler("sosred", sos_red_command))
    app.add_handler(CommandHandler("ajuda", help_command))
    app.add_handler(CommandHandler("testar_news", testar_news_command))
    app.add_handler(CallbackQueryHandler(button))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(auto_news_job, interval=1800, first=10)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=12, minute=0, tzinfo=timezone.utc))
        app.job_queue.run_daily(daily_nba_job, time=time(hour=21, minute=0, tzinfo=timezone.utc))
        app.job_queue.run_daily(daily_ufc_job, time=time(hour=15, minute=0, tzinfo=timezone.utc), days=(4, 5))
    app.run_polling()

if __name__ == "__main__":
    main()