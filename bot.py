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
ADMIN_ID = os.getenv("ADMIN_ID")
PORT = int(os.getenv("PORT", 10000))
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY")

AFFILIATE_LINKS = ["https://www.bet365.com", "https://br.betano.com", "https://stake.com"]
def get_random_link(): return random.choice(AFFILIATE_LINKS)

SENT_LINKS = set()
LATEST_HEADLINES = []

# --- BANCO DE DADOS DE TENDÊNCIAS (Para variar mercados) ---
TEAM_STATS = {
    # Times de Escanteios/Pressão
    "MANCHESTER CITY": "🚩 Over Cantos", "LIVERPOOL": "🚩 Over Cantos", "ARSENAL": "🚩 Over Cantos",
    "FLAMENGO": "🚩 Over Cantos", "PALMEIRAS": "🚩 Over Cantos", "BAYERN MUNICH": "🚩 Over Cantos",
    "REAL MADRID": "⚽ Over 2.5 Gols", "BARCELONA": "⚽ Over 2.5 Gols",
    
    # Times de Cartões/Jogo Duro
    "ATLETICO MADRID": "🟨 Over Cartões", "GETAFE": "🟨 Over Cartões", 
    "CORINTHIANS": "🟨 Jogo Truncado (Under)", "VASCO": "🟨 Over Cartões",
    "BOCA JUNIORS": "🟨 Over Cartões", "URUGUAY": "🟨 Over Cartões",
    
    # Times de Gols
    "PSG": "⚽ Ambas Marcam", "DORTMUND": "⚽ Over 2.5 Gols", "LEVERKUSEN": "⚽ Over 2.5 Gols"
}

VIP_TEAMS = list(TEAM_STATS.keys()) + ["SAO PAULO", "BOTAFOGO", "INTER MILAN", "JUVENTUS", "CHELSEA", "TOTTENHAM"]

# LIGAS DE HOJE
SOCCER_LEAGUES = [
    {"key": "soccer_uefa_champs_league", "name": "CHAMPIONS LEAGUE"},
    {"key": "soccer_england_premier_league", "name": "PREMIER LEAGUE"},
    {"key": "soccer_england_championship", "name": "CHAMPIONSHIP"},
    {"key": "soccer_england_league1", "name": "LEAGUE ONE"},
    {"key": "soccer_england_fa_cup", "name": "FA CUP"},
    {"key": "soccer_england_efl_cup", "name": "EFL CUP"},
    {"key": "soccer_brazil_campeonato", "name": "BRASILEIRÃO A"},
    {"key": "soccer_spain_la_liga", "name": "LA LIGA"},
    {"key": "soccer_italy_serie_a", "name": "SERIE A"},
    {"key": "soccer_italy_coppa_italia", "name": "COPA DA ITÁLIA"},
    {"key": "soccer_germany_bundesliga", "name": "BUNDESLIGA"},
    {"key": "soccer_germany_dfb_pokal", "name": "COPA DA ALEMANHA"},
    {"key": "soccer_france_ligue_one", "name": "LIGUE 1"},
    {"key": "soccer_conmebol_libertadores", "name": "LIBERTADORES"},
    {"key": "soccer_uefa_europa_league", "name": "EUROPA LEAGUE"}
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V138 - MULTI MERCADO")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception:", exc_info=context.error)

# --- MOTOR ---
class SportsEngine:
    def __init__(self): self.daily_accumulator = []

    async def test_all_connections(self):
        report = "📊 <b>STATUS V138</b>\n"
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: report += "✅ API Odds: OK\n"
                    else: report += f"❌ API Odds: Erro {r.status_code}\n"
                except: report += "❌ API Odds: Off\n"
        return report

    async def fetch_odds(self, sport_key, display_name):
        if not THE_ODDS_API_KEY: return []
        # URL H2H (Vamos derivar os outros mercados matematicamente ou por tendencia)
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=us&oddsFormat=decimal&markets=h2h&apiKey={THE_ODDS_API_KEY}"
        
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                r = await client.get(url)
                data = r.json()
                if not isinstance(data, list): return []
                
                games = []
                now_utc = datetime.now(timezone.utc)
                br_timezone = timezone(timedelta(hours=-3))
                now_br = now_utc.astimezone(br_timezone)
                today_date = now_br.date()

                for event in data:
                    try:
                        evt_time_utc = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        evt_time_br = evt_time_utc.astimezone(br_timezone)
                        
                        # TRAVA DE DATA: SÓ HOJE
                        if evt_time_br.date() != today_date: continue
                        
                        time_str = evt_time_br.strftime("%H:%M")
                        h, a = event['home_team'], event['away_team']
                        h_norm = normalize_name(h); a_norm = normalize_name(a)
                        is_vip = any(vip in h_norm or vip in a_norm for vip in VIP_TEAMS)
                        
                        odds_h, odds_a, odds_d = 0, 0, 0
                        for book in event['bookmakers']:
                            for m in book['markets']:
                                if m['key'] == 'h2h':
                                    for o in m['outcomes']:
                                        if o['name'] == h: odds_h = max(odds_h, o['price'])
                                        if o['name'] == a: odds_a = max(odds_a, o['price'])
                                        if o['name'] == 'Draw': odds_d = max(odds_d, o['price'])
                        
                        if odds_h > 1.05 and odds_a > 1.05:
                            games.append({
                                "match": f"{h} x {a}", "league": display_name, 
                                "time": time_str, "datetime": evt_time_br, 
                                "odd_h": odds_h, "odd_a": odds_a, "odd_d": odds_d, 
                                "home": h, "away": a, "is_vip": is_vip
                            })
                    except: continue
                return games
            except: return []

    def analyze_game(self, game):
        lines = []
        best_pick = None
        oh, oa, od = game['odd_h'], game['odd_a'], game['odd_d']
        
        # 1. TENDÊNCIAS ESTATÍSTICAS (Baseada no time)
        h_norm = normalize_name(game['home'])
        a_norm = normalize_name(game['away'])
        trend_msg = ""
        
        for team, trend in TEAM_STATS.items():
            if team in h_norm or team in a_norm:
                trend_msg = f"💡 <i>{trend}</i>"
                break
        
        # 2. ANÁLISE MATEMÁTICA DE MERCADO
        
        # Super Favorito (Sugerir Gols ou Handicap indireto)
        if oh < 1.35:
            lines.append(f"🔥 <b>Favoritaço:</b> {game['home']} (@{oh})")
            lines.append(f"🥅 <i>Provável Over 2.5 Gols</i>")
            best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}
        
        elif oa < 1.35:
            lines.append(f"🔥 <b>Favoritaço:</b> {game['away']} (@{oa})")
            lines.append(f"🥅 <i>Provável Over 2.5 Gols</i>")
            best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}
            
        # Jogo Equilibrado com leve favorito (Sugerir Dupla Chance/Empate Anula)
        elif 1.80 < oh < 2.30 and od > 0:
            # Calculo Dupla Chance Casa
            dc = round(1 / (1/oh + 1/od), 2)
            dnb = round(oh * (1 - (1/od)), 2) # Aprox Empate Anula
            lines.append(f"🛡️ <b>Dupla Chance:</b> 1X (@{dc})")
            lines.append(f"♻️ <b>DNB:</b> {game['home']} 0.0 (@{dnb})")
            if not best_pick: best_pick = {"pick": "1X", "odd": dc, "match": game['match']}
            
        elif 1.80 < oa < 2.30 and od > 0:
            dc = round(1 / (1/oa + 1/od), 2)
            dnb = round(oa * (1 - (1/od)), 2)
            lines.append(f"🛡️ <b>Dupla Chance:</b> X2 (@{dc})")
            lines.append(f"♻️ <b>DNB:</b> {game['away']} 0.0 (@{dnb})")
            if not best_pick: best_pick = {"pick": "X2", "odd": dc, "match": game['match']}
            
        # Padrão (Vitória Seca de Valor)
        else:
            if oh < 2.10: 
                lines.append(f"💎 <b>Valor:</b> {game['home']} (@{oh})")
                best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}
            elif oa < 2.10: 
                lines.append(f"💎 <b>Valor:</b> {game['away']} (@{oa})")
                best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}
            else:
                lines.append("⚖️ <b>Equilibrado</b>")

        if trend_msg: lines.append(trend_msg)
        return lines, best_pick

    async def get_soccer_grade(self):
        all_games = []
        self.daily_accumulator = []
        
        for league in SOCCER_LEAGUES:
            games = await self.fetch_odds(league['key'], league['name'])
            for g in games:
                report, pick = self.analyze_game(g)
                g['report'] = report
                if pick: self.daily_accumulator.append(pick)
                all_games.append(g)
            await asyncio.sleep(0.2)
        
        if not all_games: return []
        
        # Ordenação: VIPs -> Horário
        all_games.sort(key=lambda x: (not x['is_vip'], x['datetime']))
        return all_games

    async def get_nba_games(self):
        games = await self.fetch_odds("basketball_nba", "NBA")
        processed = []
        for g in games: report, _ = self.analyze_game(g); g['report'] = report; processed.append(g)
        return processed

engine = SportsEngine()

# --- GERADOR DE MÚLTIPLA (10x a 20x) ---
def gerar_bilhete(palpites):
    if len(palpites) < 3: return ""
    
    # Tenta 50 vezes achar uma combinação que bata entre 10 e 20
    for _ in range(50):
        random.shuffle(palpites)
        selected = []
        total_odd = 1.0
        
        for p in palpites:
            # Se a odd for muito baixa (1.10) nem põe, atrapalha
            if p['odd'] < 1.20: continue 
            
            # Se adicionar esse jogo passar de 21, ignora
            if total_odd * p['odd'] > 21.0: continue
            
            selected.append(p)
            total_odd *= p['odd']
            
            # Se já passou de 10 e é menor que 20, PARA! Achamos a boa.
            if 10.0 <= total_odd <= 20.0:
                txt = f"\n🎟️ <b>MÚLTIPLA SNIPER (ODD {total_odd:.2f})</b> 🎯\n"
                for s in selected: txt += f"🔹 {s['match']}: {s['pick']} (@{s['odd']})\n"
                txt += "⚠️ <i>Aposte com responsabilidade.</i>\n"
                return txt
                
    return "\n⚠️ <i>Não foi possível gerar uma múltipla segura (10x-20x) hoje.</i>"

async def enviar_audio(context, game):
    text = f"Destaque de hoje: {game['match']}. "
    bet = game['report'][0].replace("<b>","").replace("</b>","").replace("🔥","").replace("🛡️","")
    text += f"Análise principal: {bet}. Verifique também mercados de gols e cantos."
    try:
        tts = gTTS(text=text, lang='pt'); tts.save("audio.mp3")
        with open("audio.mp3", "rb") as f: await context.bot.send_voice(chat_id=CHANNEL_ID, voice=f)
        os.remove("audio.mp3")
    except: pass

async def enviar_post(context, text, bilhete=""):
    kb = [[InlineKeyboardButton("📲 APOSTAR AGORA", url=get_random_link())]]
    try: await context.bot.send_message(chat_id=CHANNEL_ID, text=text+bilhete, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e: logger.error(f"Erro envio: {e}")

# --- COMANDOS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🦁 ABRIR MENU", callback_data="menu")]]
    await update.message.reply_text("🦁 <b>BOT V138 ONLINE</b>\nFiltro: HOJE + MULTI MERCADOS.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "menu":
        kb = [[InlineKeyboardButton("⚽ Futebol Hoje", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
              [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Forçar Update", callback_data="force")]]
        await q.edit_message_text("🦁 <b>MENU V138</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    
    elif q.data == "status":
        rep = await engine.test_all_connections()
        await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data="menu")]]), parse_mode=ParseMode.HTML)

    elif q.data == "fut":
        await q.message.reply_text("⏳ <b>Buscando jogos de HOJE com Variedade...</b>", parse_mode=ParseMode.HTML)
        games = await engine.get_soccer_grade()
        if not games: await q.message.reply_text("❌ Nenhum jogo relevante HOJE (UTC-3)."); return
        
        chunks = [games[i:i + 8] for i in range(0, len(games), 8)] # Blocos de 8 jogos
        
        for i, chunk in enumerate(chunks):
            header = "🔥 <b>GRADE DE HOJE</b> 🔥\n\n" if i == 0 else "👇 <b>MAIS JOGOS...</b>\n\n"
            msg = header
            for g in chunk:
                icon = "💎" if g['is_vip'] else "⚽"
                if i==0 and g == chunk[0]: await enviar_audio(context, g); icon = "⭐ <b>DESTAQUE</b>\n"
                
                # MONTAGEM DA MENSAGEM COM HORARIO E ODD EM TUDO
                reports = "\n".join(g['report'])
                msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
            
            bilhete = gerar_bilhete(engine.daily_accumulator) if i == len(chunks)-1 else ""
            await enviar_post(context, msg, bilhete)
        
        await q.message.reply_text("✅ Enviado!")

    elif q.data == "force":
        await q.message.reply_text("🔄 <b>Atualizando...</b>", parse_mode=ParseMode.HTML)
        await daily_soccer_job(context)
        await q.message.reply_text("✅ Feito.")

# --- JOBS ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    games = await engine.get_soccer_grade()
    if not games: return
    chunks = [games[i:i + 8] for i in range(0, len(games), 8)]
    for i, chunk in enumerate(chunks):
        header = "☀️ <b>BOM DIA! GRADE V138</b> ☀️\n\n" if i == 0 else "👇 <b>CONTINUAÇÃO...</b>\n\n"
        msg = header
        for g in chunk:
            icon = "💎" if g['is_vip'] else "⚽"
            if i==0 and g == chunk[0]: await enviar_audio(context, g); icon = "⭐ <b>DESTAQUE</b>\n"
            reports = "\n".join(g['report'])
            msg += f"{icon} <b>{g['league']}</b> | ⏰ <b>{g['time']}</b>\n⚔️ {g['match']}\n{reports}\n━━━━━━━━━━━━━━━━\n"
        bilhete = gerar_bilhete(engine.daily_accumulator) if i == len(chunks)-1 else ""
        await enviar_post(context, msg, bilhete)

def main():
    if not BOT_TOKEN: print("ERRO: Configure o BOT_TOKEN no .env"); return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_error_handler(error_handler)
    
    if app.job_queue:
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=11, minute=0, tzinfo=timezone(timedelta(hours=-3))))
    
    print("BOT V138 RODANDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
