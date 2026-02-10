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

# LISTA VIP (Apenas para destaque visual, não exclui os outros)
VIP_TEAMS = [
    "FLAMENGO", "PALMEIRAS", "CORINTHIANS", "SAO PAULO", "VASCO", "BOTAFOGO",
    "REAL MADRID", "BARCELONA", "LIVERPOOL", "MANCHESTER CITY", "ARSENAL", 
    "MANCHESTER UNITED", "CHELSEA", "PSG", "BAYERN MUNICH", "JUVENTUS", 
    "INTER MILAN", "NAPOLI", "BOCA JUNIORS", "RIVER PLATE", "AL NASSR", "INTER MIAMI"
]

# LIGAS (Ordem de prioridade para busca)
SOCCER_LEAGUES = [
    {"key": "soccer_uefa_champs_league", "name": "CHAMPIONS LEAGUE"},
    {"key": "soccer_england_premier_league", "name": "PREMIER LEAGUE"},
    {"key": "soccer_brazil_campeonato", "name": "BRASILEIRÃO A"},
    {"key": "soccer_spain_la_liga", "name": "LA LIGA"},
    {"key": "soccer_italy_serie_a", "name": "SERIE A"},
    {"key": "soccer_germany_bundesliga", "name": "BUNDESLIGA"},
    {"key": "soccer_france_ligue_one", "name": "LIGUE 1"},
    {"key": "soccer_conmebol_libertadores", "name": "LIBERTADORES"},
    {"key": "soccer_uefa_europa_league", "name": "EUROPA LEAGUE"}
]

def normalize_name(name):
    if not name: return ""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if unicodedata.category(c) != 'Mn').upper()

# --- SERVIDOR FAKE ---
class FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"BOT V136 - ALIVE")
def run_web_server():
    try: HTTPServer(('0.0.0.0', PORT), FakeHandler).serve_forever()
    except: pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- MOTOR DE APOSTAS ---
class SportsEngine:
    def __init__(self): self.daily_accumulator = []

    async def test_all_connections(self):
        report = "📊 <b>STATUS V136</b>\n\n"
        if THE_ODDS_API_KEY:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    r = await client.get(f"https://api.the-odds-api.com/v4/sports?apiKey={THE_ODDS_API_KEY}")
                    if r.status_code == 200: 
                        rem = r.headers.get("x-requests-remaining", "?")
                        report += f"✅ API Odds: OK ({rem} rest.)\n"
                    else: report += f"❌ API Odds: Erro {r.status_code}\n"
                except: report += "❌ API Odds: Falha Conexão\n"
        else: report += "❌ API Key Ausente\n"
        return report

    async def fetch_odds(self, sport_key, display_name):
        if not THE_ODDS_API_KEY: return []
        # URL ajustada para pegar jogos de hoje e amanhã
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds?regions=us&oddsFormat=decimal&markets=h2h&apiKey={THE_ODDS_API_KEY}"
        
        async with httpx.AsyncClient(timeout=25) as client:
            try:
                r = await client.get(url)
                data = r.json()
                
                # DEBUG NO LOG DO RENDER
                if isinstance(data, list):
                    print(f"[DEBUG] {display_name}: Encontrou {len(data)} eventos brutos.")
                else:
                    print(f"[DEBUG] {display_name}: Erro ou vazio. Resposta: {str(data)[:50]}")

                if not isinstance(data, list): return []
                
                games = []
                now = datetime.now(timezone.utc)
                # Aumentei para 36h para garantir que pegue o dia todo
                limit_time = now + timedelta(hours=36) 
                
                for event in data:
                    try:
                        evt_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        # Filtra jogos muito antigos ou muito futuros
                        if evt_time > limit_time or evt_time < (now - timedelta(hours=3)): continue 
                        
                        time_str = evt_time.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
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
                        
                        # Filtro Básico de Odd Válida
                        if odds_h > 1.01 and odds_a > 1.01:
                            games.append({
                                "match": f"{h} x {a}", 
                                "league": display_name, 
                                "time": time_str, 
                                "datetime": evt_time, 
                                "odd_h": odds_h, "odd_a": odds_a, "odd_d": odds_d, 
                                "home": h, "away": a, 
                                "is_vip": is_vip
                            })
                    except: continue
                return games
            except Exception as e: 
                print(f"[ERRO] Falha ao buscar {display_name}: {e}")
                return []

    def analyze_game(self, game):
        # Lógica simplificada e direta para garantir palpites
        lines = []
        best_pick = None
        oh, oa, od = game['odd_h'], game['odd_a'], game['odd_d']
        pick_odd = 0
        
        if 1.20 < oh < 1.70:
            lines.append(f"🔥 <b>Favorito:</b> {game['home']} (@{oh})")
            best_pick = {"pick": game['home'], "odd": oh, "match": game['match']}; pick_odd = oh
        elif 1.20 < oa < 1.70:
            lines.append(f"🔥 <b>Favorito:</b> {game['away']} (@{oa})")
            best_pick = {"pick": game['away'], "odd": oa, "match": game['match']}; pick_odd = oa
        elif 1.30 < oh < 2.30 and od > 0: # Dupla Chance
            dc = round(1 / (1/oh + 1/od), 2)
            if 1.25 < dc < 1.60:
                 lines.append(f"🛡️ <b>Segurança:</b> {game['home']} ou Empate (@{dc})")
                 if not best_pick: best_pick = {"pick": "1X", "odd": dc, "match": game['match']}; pick_odd = dc
        
        if not lines: # Se não achou favorito, procura valor
            if oh < 2.10: lines.append(f"💎 <b>Valor:</b> {game['home']} (@{oh})")
            elif oa < 2.10: lines.append(f"💎 <b>Valor:</b> {game['away']} (@{oa})")
            else: lines.append("⚖️ <b>Equilibrado</b>")

        return lines, best_pick

    async def get_soccer_grade(self):
        all_games = []
        self.daily_accumulator = []
        
        print("--- INICIANDO BUSCA DE JOGOS ---")
        
        # 1. Busca jogos de TODAS as ligas
        for league in SOCCER_LEAGUES:
            games = await self.fetch_odds(league['key'], league['name'])
            # Pega TODOS os jogos válidos dessa liga, não limita aqui
            for g in games:
                report, pick = self.analyze_game(g)
                g['report'] = report
                if pick: self.daily_accumulator.append(pick)
                all_games.append(g)
            await asyncio.sleep(0.3) 
        
        if not all_games: return []

        # 2. ESTRATÉGIA DE SELEÇÃO "VARIEDADE" (O Pulo do Gato)
        final_list = []
        leagues_included = set()
        
        # Passo A: Pega o MELHOR jogo de CADA liga disponível (para garantir variedade)
        # Ordena temporariamente por 'VIP' para pegar o melhor de cada liga se houver
        temp_sorted = sorted(all_games, key=lambda x: x['is_vip'], reverse=True)
        
        for g in temp_sorted:
            if g['league'] not in leagues_included:
                final_list.append(g)
                leagues_included.add(g['league'])
        
        # Passo B: Preenche o resto da lista com os jogos VIP restantes
        for g in all_games:
            if g not in final_list and g['is_vip']:
                final_list.append(g)
        
        # Passo C: Se ainda tiver espaço (queremos até 12 jogos), preenche por horário
        remaining = [g for g in all_games if g not in final_list]
        remaining.sort(key=lambda x: x['datetime']) # Os mais cedo primeiro
        
        while len(final_list) < 12 and remaining:
            final_list.append(remaining.pop(0))
            
        # 3. Ordenação Final para Exibição: VIPs no topo, depois por Horário
        final_list.sort(key=lambda x: (not x['is_vip'], x['datetime']))
        
        return final_list[:12] # Retorna Top 12 Garantido

    async def get_nba_games(self):
        games = await self.fetch_odds("basketball_nba", "NBA")
        processed = []
        for g in games: report, _ = self.analyze_game(g); g['report'] = report; processed.append(g)
        return processed

engine = SportsEngine()

# --- UTILS ---
def gerar_bilhete(palpites):
    if len(palpites) < 2: return ""
    random.shuffle(palpites)
    selection = palpites[:4] # Max 4 jogos
    odd_total = 1.0
    for p in selection: odd_total *= p['odd']
    if odd_total > 20: return "" # Odd muito alta é red na certa
    txt = f"\n🎟️ <b>MÚLTIPLA DO DIA (ODD {odd_total:.2f})</b>\n"
    for p in selection: txt += f"🎯 {p['match']}: {p['pick']} (@{p['odd']})\n"
    return txt

async def enviar_audio(context, game):
    text = f"Destaque do dia! {game['match']} pela {game['league']}. "
    bet = game['report'][0].replace("<b>","").replace("</b>","").replace("🔥","").replace("🛡️","")
    text += f"A nossa análise indica: {bet}. Boa sorte!"
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
    await update.message.reply_text("🦁 <b>BOT V136 ONLINE</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "menu":
        kb = [[InlineKeyboardButton("⚽ Futebol", callback_data="fut"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
              [InlineKeyboardButton("📊 Status", callback_data="status"), InlineKeyboardButton("🔄 Forçar Update", callback_data="force")]]
        await q.edit_message_text("🦁 <b>MENU V136</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    
    elif q.data == "status":
        rep = await engine.test_all_connections()
        await q.edit_message_text(rep, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Voltar", callback_data="menu")]]), parse_mode=ParseMode.HTML)

    elif q.data == "fut":
        await q.message.reply_text("⏳ <b>Buscando melhores jogos... (Isso leva uns segundos)</b>", parse_mode=ParseMode.HTML)
        games = await engine.get_soccer_grade()
        if not games: await q.message.reply_text("❌ Nenhum jogo encontrado."); return
        
        msg = "🔥 <b>GRADE DE HOJE (TOP 12)</b> 🔥\n\n"
        for i, g in enumerate(games):
            icon = "💎" if g['is_vip'] else "⚽"
            if i==0: await enviar_audio(context, g); icon = "⭐ <b>JOGO DO DIA</b>\n"
            msg += f"{icon} <b>{g['league']}</b> | {g['time']}\n⚔️ {g['match']}\n{g['report'][0]}\n━━━━━━━━━━━━━━━━\n"
        
        await enviar_post(context, msg, gerar_bilhete(engine.daily_accumulator))
        await q.message.reply_text("✅ Enviado!")

    elif q.data == "force":
        await q.message.reply_text("🔄 <b>Forçando atualização geral...</b>", parse_mode=ParseMode.HTML)
        await daily_soccer_job(context)
        await q.message.reply_text("✅ Feito.")

# --- JOBS ---
async def daily_soccer_job(context: ContextTypes.DEFAULT_TYPE):
    print("EXECUTANDO JOB DE FUTEBOL...")
    games = await engine.get_soccer_grade()
    if not games: return
    msg = "☀️ <b>BOM DIA! PALPITES V136</b> ☀️\n\n"
    for i, g in enumerate(games):
        icon = "💎" if g['is_vip'] else "⚽"
        if i==0: await enviar_audio(context, g); icon = "⭐ <b>DESTAQUE</b>\n"
        msg += f"{icon} <b>{g['league']}</b> | {g['time']}\n⚔️ {g['match']}\n{g['report'][0]}\n━━━━━━━━━━━━━━━━\n"
    await enviar_post(context, msg, gerar_bilhete(engine.daily_accumulator))

def main():
    if not BOT_TOKEN: print("ERRO: Configure o BOT_TOKEN no .env"); return
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_error_handler(error_handler)
    
    if app.job_queue:
        # Define horario fixo para meio-dia (ajuste o timezone se necessario)
        app.job_queue.run_daily(daily_soccer_job, time=time(hour=12, minute=0, tzinfo=timezone(timedelta(hours=-3))))
    
    print("BOT V136 RODANDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
