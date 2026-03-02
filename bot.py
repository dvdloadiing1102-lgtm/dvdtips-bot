# ================= BOT V334 (JOB QUEUE - ARQUITETURA DE SERVIDOR) =================
import os
import logging
import threading
import html
import random
import json
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# Importações com tratamento de erro
try:
    import httpx
    import feedparser
    from dotenv import load_dotenv
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults
except ImportError as e:
    print(f"❌ ERRO: Faltam bibliotecas! Rode: pip install python-telegram-bot httpx feedparser python-dotenv")
    exit(1)

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

# Configuração de Logs (Para ver o erro se acontecer)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# CACHE GLOBAL (Onde os dados vivem)
DATA_CACHE = {
    "soccer": [],
    "nba": [],
    "ufc": [],
    "last_update": "Aguardando...",
}

# MEMÓRIA DE ALERTAS
ALERT_MEMORY = {}

# --- 2. HELPERS ---

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
        if "EV" in str(american_str).upper(): return 2.00
        val = float(american_str)
        if val == 0: return 1.0
        if val < 0: return round((100 / abs(val)) + 1, 2)
        else: return round((val / 100) + 1, 2)
    except: return 0.0

# --- 3. PARSERS E LÓGICA ---

def parse_odds_string(details_str, home_name, away_name):
    pick = "Aguardando Odds"; odd_decimal = 0.0; icon = "⏳"; is_favorite = False
    if not details_str or details_str == '-': return pick, odd_decimal, icon, is_favorite

    try:
        parts = details_str.split(' ')
        abbr = parts[0]; number_str = parts[1] if len(parts) > 1 else "0"
        odd_decimal = american_to_decimal(number_str)
        
        team_focused = None; type_team = ""
        if abbr.lower() in home_name.lower()[:4]: team_focused = home_name; type_team = "HOME"
        elif abbr.lower() in away_name.lower()[:4]: team_focused = away_name; type_team = "AWAY"
        else:
            if "-" in number_str: return f"Favorito: {abbr}", odd_decimal, "🔥", True
            return pick, odd_decimal, icon, False

        if "-" in number_str:
            pick = f"Vitória do {team_focused}"
            icon = "🏠" if type_team == "HOME" else "🔥"
            is_favorite = True
        else:
            pick = f"Zebra: {team_focused}"; icon = "🦓"; is_favorite = False
    except: pass
    return pick, odd_decimal, icon, is_favorite

# --- 4. TAREFAS DE ATUALIZAÇÃO (JOBS) ---

async def update_data_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Esta função roda a cada 60 segundos automaticamente pelo JobQueue.
    Ela atualiza os dados e checa gols.
    """
    global DATA_CACHE, ALERT_MEMORY
    
    date_str = get_api_date_str()
    br_tz = timezone(timedelta(hours=-3))
    logger.info(f"🔄 Job: Atualizando dados... ({date_str})")

    # 1. FUTEBOL
    soccer_list = []
    leagues = {'bra.1': '🇧🇷 Brasileirão', 'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga'}
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        # FUTEBOL
        for code, name in leagues.items():
            try:
                r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}")
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        status = event['status']['type']['state']
                        status = 'in' if status == 'in' else ('post' if status == 'post' else 'agendado')
                        clock = event['status']['type']['detail']
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        sh = int(comp[0]['score']); sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', '-')
                        
                        # TV
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv = broadcasts[0]['names'][0] if broadcasts else ("Premiere/Globo" if 'bra' in code else "")
                        
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        game_obj = {
                            "id": event['id'], "raw": event,
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status, "clock": clock,
                            "sh": sh, "sa": sa, "venue": venue, "tv": tv
                        }
                        soccer_list.append(game_obj)
                        
                        # --- VERIFICAÇÃO DE GOL IMEDIATA ---
                        gid = event['id']
                        if gid in ALERT_MEMORY:
                            old = ALERT_MEMORY[gid]
                            if status == 'in' and (sh > old['h'] or sa > old['a']):
                                scorer = home if sh > old['h'] else away
                                msg = f"⚽ <b>GOOOOOOL DO {scorer.upper()}!</b>\n\n🏟️ {game_obj['match']}\n⏱️ {clock}\n🔢 {sh} - {sa}"
                                try: await context.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                                except: pass
                        
                        ALERT_MEMORY[gid] = {'h': sh, 'a': sa}

            except Exception as e:
                logger.error(f"Erro Liga {code}: {e}")

        # 2. UFC (Simples)
        ufc_list = []
        try:
            r = await client.get("https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    for comp in event['competitions']:
                        fighters = comp['competitors']
                        red = fighters[0]['athlete']['fullName']; blue = fighters[1]['athlete']['fullName']
                        ufc_list.append({
                            "red": red, "blue": blue, "time": dt.strftime("%d/%m %H:%M"),
                            "card": comp.get('card', 'main'),
                            "red_odds": "-200", "blue_odds": "+150"
                        })
        except: pass

        # 3. NBA
        nba_list = []
        try:
            r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    nba_list.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"), "odds": odds,
                        "pick": f"Vitória do {t_home['team']['name']}"
                    })
        except: pass

    # Atualiza Cache
    if soccer_list: DATA_CACHE['soccer'] = sorted(soccer_list, key=lambda x: x['time'])
    if ufc_list: DATA_CACHE['ufc'] = ufc_list
    if nba_list: DATA_CACHE['nba'] = nba_list
    DATA_CACHE['last_update'] = datetime.now().strftime("%H:%M:%S")

# --- 5. COMANDOS DO BOT ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    # Resposta Imediata
    await u.message.reply_text(
        f"🦁 <b>PAINEL V334 (JOB QUEUE)</b>\n"
        f"Última atualização: {DATA_CACHE['last_update']}\n\n"
        f"O sistema roda em segundo plano. Clique à vontade:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚽ Grade", callback_data="fut"), InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
            [InlineKeyboardButton("🎫 Bilhete", callback_data="ticket"), InlineKeyboardButton("🏀 NBA", callback_data="nba")]
        ]),
        parse_mode=ParseMode.HTML
    )

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        if not DATA_CACHE['soccer']:
            await q.message.reply_text("⏳ O robô está baixando os dados... Tente em 10 segundos.")
            return

        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in DATA_CACHE['soccer']:
            # Formatação Inline para economizar linhas
            odds_list = g['raw']['competitions'][0].get('odds', [])
            d = odds_list[0].get('details', '-') if odds_list else '-'
            pick, odd, icon, _ = parse_odds_string(d, g['home'], g['away'])
            odd_str = f"@{odd:.2f}" if odd > 0 else "(S/ Odd)"
            
            card = (
                f"{safe_html(g['league'])} | {g['clock']}\n"
                f"⚽ <b>{g['match']}</b>\n"
                f"{icon} {pick} | 💰 {odd_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ufc":
        if not DATA_CACHE['ufc']:
            await q.message.reply_text("🥊 Sem dados de UFC no momento.")
            return
        txt = "🥊 <b>CARD UFC</b>\n\n"
        for f in DATA_CACHE['ufc']:
            txt += f"🔴 {f['red']} x 🔵 {f['blue']}\nℹ️ {f['time']} | {f['card']}\n━━━━━━━━━━━━━━━━━━━━\n"
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()
        
    elif q.data == "ticket":
        if not DATA_CACHE['soccer']: 
            await q.message.reply_text("⏳ Carregando...")
            return
        
        cands = []
        for g in DATA_CACHE['soccer']:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            d = odds_list[0].get('details', '-') if odds_list else '-'
            p, o, _, fav = parse_odds_string(d, g['home'], g['away'])
            if fav and 1.20 <= o <= 2.20:
                cands.append({'m': g['match'], 'p': p, 'o': o})
        
        if len(cands) < 3:
            await q.message.reply_text("❌ Jogos insuficientes para bilhete.")
            return
            
        random.shuffle(cands)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        tot = 1.0
        for x in cands[:3]:
            tot *= x['o']
            msg += f"✅ <b>{x['m']}</b>\n🎯 {x['p']} (@{x['o']:.2f})\n\n"
        msg += f"🔥 <b>TOTAL: {tot:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

    elif q.data == "nba":
        if not DATA_CACHE['nba']:
            await q.message.reply_text("🏀 Sem NBA.")
            return
        txt = "🏀 <b>NBA HOJE</b>\n\n"
        for g in DATA_CACHE['nba']:
            txt += f"⚔️ {g['match']}\n⏰ {g['time']} | Spread: {g['odds']}\n━━━━━━━━━━━━━━━━━━━━\n"
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

# --- 6. ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- SERVER PARA O RENDER (PORTA) ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE V334")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def main():
    threading.Thread(target=run_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # 1. Adiciona Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.add_error_handler(error_handler)
    
    # 2. Adiciona Jobs (A Mágica da Estabilidade)
    # Roda update_data_job imediatamente (first=1) e depois a cada 60s
    job_queue = app.job_queue
    job_queue.run_repeating(update_data_job, interval=60, first=1)
    
    print("✅ BOT V334 (JOB QUEUE) INICIADO!")
    
    # 3. Roda (Bloqueante, mas seguro)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
