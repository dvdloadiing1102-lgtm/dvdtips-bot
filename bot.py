# ================= BOT V331 (ARQUITETURA BLINDADA: CACHE + ALERTAS) =================
import os
import logging
import asyncio
import threading
import httpx
import html
import feedparser
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 2. MEMÓRIA GLOBAL (CACHE) ---
# Aqui ficam os dados. O botão lê daqui. O loop escreve aqui.
DATA_CACHE = {
    "soccer": [],
    "nba": [],
    "ufc": [],
    "last_update": None
}

# Memória para controle de alertas (para não repetir gol)
ALERT_MEMORY = {}

# Lista VIP para destaque visual
VIP_FILTER = [
    "flamengo", "palmeiras", "corinthians", "são paulo", "santos", "grêmio",
    "internacional", "atlético-mg", "cruzeiro", "botafogo", "fluminense", "vasco",
    "bahia", "fortaleza", "athletico-pr", "sport", "ceará", "vitória",
    "arsenal", "liverpool", "man city", "real madrid", "barcelona", "bayern", 
    "inter", "milan", "juventus", "psg", "chelsea", "dortmund", "benfica", "porto", "napoli", 
    "roma", "lazio", "atletico madrid", "boca juniors", "river plate", "tottenham", "man utd"
]

# --- 3. HELPERS ---

def get_real_server_date():
    br_tz = timezone(timedelta(hours=-3))
    return datetime.now(br_tz)

def get_display_date():
    return get_real_server_date().strftime("%d/%m/%Y")

def get_api_date_str():
    return get_real_server_date().strftime("%Y%m%d")

def safe_html(text):
    if not text: return ""
    return html.escape(str(text))

def american_to_decimal(american_str):
    try:
        val = float(american_str)
        if val == 0: return 1.0
        if val < 0: return round((100 / abs(val)) + 1, 2)
        else: return round((val / 100) + 1, 2)
    except: return 0.0

def is_vip(team_name):
    return any(vip in team_name.lower() for vip in VIP_FILTER)

# --- 4. LÓGICA DE ODDS E PICKS ---

def parse_game_data(details_str, home_name, away_name):
    pick = "Aguardando Odds"
    odd_decimal = 0.0
    icon = "⏳"
    is_favorite = False # Flag para saber se é um palpite confiável

    if not details_str or details_str == '-': 
        return pick, odd_decimal, icon, is_favorite

    try:
        # Ex: FLA -475
        parts = details_str.split(' ')
        abbr = parts[0]
        number_str = parts[1] if len(parts) > 1 else "0"
        
        odd_decimal = american_to_decimal(number_str)
        
        # Identifica o time da sigla
        team_focused = None
        if abbr.lower() in home_name.lower()[:4]: team_focused = home_name
        elif abbr.lower() in away_name.lower()[:4]: team_focused = away_name
        
        # Lógica do Sinal (-) = Favorito
        if "-" in number_str:
            if team_focused:
                pick = f"Vitória do {team_focused}"
                icon = "🏠" if team_focused == home_name else "🔥"
                is_favorite = True
            else:
                # Se não identificou o nome, mas tem -, é o favorito da linha
                pick = "Favorito (Verificar Odd)"
                icon = "🔥"
                is_favorite = True
        else:
            if team_focused:
                pick = f"Zebra: {team_focused}"
                icon = "🦓"
                is_favorite = False

    except: pass
    return pick, odd_decimal, icon, is_favorite

# --- 5. BUSCA DE DADOS (MOTOR) ---

async def update_data():
    """
    Função Mestra: Busca dados e atualiza o Cache.
    NÃO responde ao usuário, apenas atualiza a memória.
    """
    global DATA_CACHE
    date_str = get_api_date_str()
    br_tz = timezone(timedelta(hours=-3))
    
    # --- FUTEBOL ---
    leagues = {
        'bra.1': '🇧🇷 Brasileirão', 'bra.copa_do_brasil': '🏆 Copa do Brasil',
        'bra.camp.paulista': '🇧🇷 Paulistão', 'bra.camp.carioca': '🇧🇷 Carioca',
        'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 
        'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'ksa.1': '🇸🇦 Sauditão'
    }
    
    new_soccer_list = []
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        # FUTEBOL
        for code, name in leagues.items():
            try:
                url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}"
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        status = event['status']['type']['state'] # pre, in, post
                        clock = event['status']['type']['detail']
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']
                        away = comp[1]['team']['name']
                        sh = int(comp[0]['score'])
                        sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', 'Local a definir')
                        
                        # TV
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv_channels = [b['names'][0] for b in broadcasts if 'names' in b]
                        tv_str = ", ".join(tv_channels) if tv_channels else ""
                        if 'bra' in code and not tv_str: tv_str = "Premiere / Globo"
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        new_soccer_list.append({
                            "id": event['id'],
                            "raw": event, # Guarda dados brutos para odds
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status, "clock": clock,
                            "score_home": sh, "score_away": sa,
                            "venue": venue, "tv": tv_str
                        })
            except: pass

        # NBA
        new_nba_list = []
        try:
            url_nba = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
            r = await client.get(url_nba)
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    clock = event['status']['type']['detail']
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    tv_list = comp.get('broadcasts', [])
                    tv_str = tv_list[0]['names'][0] if tv_list else "NBA League Pass"
                    
                    new_nba_list.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"), "clock": clock,
                        "tv": tv_str, "odds": odds,
                        "pick": f"Vitória do {t_home['team']['name']}" # Simplificado
                    })
        except: pass

    # ATUALIZAÇÃO SEGURA DO CACHE
    # Só atualiza se encontrou algo OU se for a primeira vez.
    # Evita zerar a lista por erro de API momentâneo.
    if new_soccer_list:
        DATA_CACHE['soccer'] = sorted(new_soccer_list, key=lambda x: x['time'])
    
    if new_nba_list:
        DATA_CACHE['nba'] = new_nba_list
        
    DATA_CACHE['last_update'] = datetime.now()
    logger.info(f"🔄 Cache Atualizado. Futebol: {len(DATA_CACHE['soccer'])} | NBA: {len(DATA_CACHE['nba'])}")

# --- 6. SISTEMA DE ALERTAS (INTEGRADO NO LOOP) ---

async def check_alerts(app):
    global ALERT_MEMORY
    
    for game in DATA_CACHE['soccer']:
        gid = game['id']
        status = game['status']
        sh = game['score_home']
        sa = game['score_away']
        home = game['home']
        away = game['away']
        
        # Inicializa memória
        if gid not in ALERT_MEMORY:
            ALERT_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}
            continue
        
        old = ALERT_MEMORY[gid]
        
        # 1. GOL
        if status == 'in' and (sh > old['h'] or sa > old['a']):
            scorer = home if sh > old['h'] else away
            msg = f"⚽ <b>GOOOOOOL DO {scorer.upper()}!</b>\n\n🏟️ {game['match']}\n⏱️ {game['clock']}\n🔢 Placar: {sh} - {sa}"
            try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
            except: pass
            
        # 2. GREEN/RED (FIM DE JOGO)
        if status == 'post' and old['status'] == 'in':
            # Analisa Odd Final
            odds_list = game['raw']['competitions'][0].get('odds', [])
            details = odds_list[0].get('details', '-') if odds_list else '-'
            pick, _, _, is_fav = parse_game_data(details, home, away)
            
            if is_fav:
                is_green = False
                if "Vitória do" in pick:
                    if home in pick and sh > sa: is_green = True
                    elif away in pick and sa > sh: is_green = True
                
                res = "✅ GREEN" if is_green else "❌ RED"
                msg = f"{res} <b>FINALIZADO</b>\n\n⚽ {game['match']}\n🔢 {sh} - {sa}\n🎯 Pick: {pick}"
                try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                except: pass

        # Atualiza memória
        ALERT_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}

# --- 7. ROTINA PRINCIPAL (O CORAÇÃO) ---

async def master_loop(app):
    """Roda a cada 60s: Atualiza Dados -> Checa Alertas -> Checa Horários"""
    logger.info("🚀 Master Loop Iniciado")
    while True:
        await update_data() # Atualiza o Cache
        await check_alerts(app) # Verifica Gols com base no Cache novo
        
        # Automações de Horário (08h e 16h)
        now = datetime.now(timezone(timedelta(hours=-3)))
        
        if now.hour == 8 and now.minute == 0:
            # Envia Grade Futebol
            if DATA_CACHE['soccer']:
                txt = f"🦁 <b>BOM DIA! GRADE VIP | {get_display_date()}</b> 🦁\n\n"
                for g in DATA_CACHE['soccer']:
                    # Formata apenas se for VIP ou se tiver poucos jogos
                    # Aqui usamos a função de formatação
                    c = format_soccer_card(g)
                    if len(txt) + len(c) > 4000:
                        await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                    txt += c
                if txt: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
            await asyncio.sleep(65)
            
        elif now.hour == 16 and now.minute == 0:
            # Envia NBA
            if DATA_CACHE['nba']:
                txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
                for g in DATA_CACHE['nba']:
                    txt += format_nba_card(g)
                await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)
            await asyncio.sleep(65)

        await asyncio.sleep(60)

async def news_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                msg = f"🌍 <b>GIRO DE NOTÍCIAS</b>\n\n📰 {safe_html(entry.title)}\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass
        await asyncio.sleep(14400) # 4h

# --- 8. FORMATADORES VISUAIS ---

def format_soccer_card(game):
    odds_list = game['raw']['competitions'][0].get('odds', [])
    details = odds_list[0].get('details', '-') if odds_list else '-'
    
    pick, odd, icon, _ = parse_game_data(details, game['home'], game['away'])
    
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    odd_str = f"@{odd:.2f}" if odd > 0 else "(S/ Odd)"
    
    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ {safe_html(game['venue'])}\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{game['tv']}\n"
        f"{icon} <b>{pick}</b>\n"
        f"💰 Odd: <b>{odd_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_nba_card(game):
    return (
        f"🏀 <b>NBA | {game['clock']}</b>\n"
        f"⚔️ <b>{safe_html(game['match'])}</b>\n"
        f"{game['tv']}\n"
        f"✅ {safe_html(game['pick'])}\n"
        f"📊 {safe_html(game['odds'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

# --- 9. MENU E COMANDOS (LÊ DO CACHE) ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>PAINEL V331</b>\nSistema Híbrido Ativo.", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut")],
        [InlineKeyboardButton("🎫 Bilhete de Ouro", callback_data="ticket")],
        [InlineKeyboardButton("🏀 NBA", callback_data="nba")]
    ]), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        # LÊ DO CACHE (INSTANTÂNEO)
        if not DATA_CACHE['soccer']:
            await q.message.reply_text("⏳ Atualizando dados... Tente em 1 minuto.")
            # Força update se estiver vazio
            await update_data()
            return
            
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in DATA_CACHE['soccer']:
            card = format_soccer_card(g)
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ticket":
        if not DATA_CACHE['soccer']:
            await q.message.reply_text("⏳ Carregando dados...")
            return
            
        cands = []
        for g in DATA_CACHE['soccer']:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            details = odds_list[0].get('details', '-') if odds_list else '-'
            p, odd, _, is_fav = parse_game_data(details, g['home'], g['away'])
            if is_fav and odd >= 1.20 and odd <= 2.20:
                cands.append({'m': g['match'], 'p': p, 'o': odd})
        
        if len(cands) < 3:
            await q.message.reply_text("❌ Sem jogos seguros suficientes.")
            return
            
        import random
        random.shuffle(cands)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        tot = 1.0
        for cc in cands[:3]:
            tot *= cc['o']
            msg += f"✅ <b>{cc['m']}</b>\n🎯 {cc['p']} (@{cc['o']:.2f})\n\n"
        msg += f"🔥 <b>ODD FINAL: {tot:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        if not DATA_CACHE['nba']:
            await q.message.reply_text("❌ Sem jogos da NBA hoje.")
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in DATA_CACHE['nba']: txt += format_nba_card(g)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)

# --- SERVER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE - V331 CACHE")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    logger.info("🚀 INICIANDO V331...")
    # Inicia com dados frescos
    await update_data()
    # Inicia loops
    asyncio.create_task(master_loop(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
