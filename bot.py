# ================= BOT V337 (GREEN/RED + FECHAMENTO DO DIA) =================
import os
import logging
import asyncio
import threading
import html
import random
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import httpx
    import feedparser
    from dotenv import load_dotenv
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, Defaults
except ImportError:
    print("❌ ERRO: Faltam bibliotecas. Instale: pip install python-telegram-bot httpx feedparser python-dotenv")
    exit(1)

# --- 1. CONFIGURAÇÃO ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- VARIÁVEIS GLOBAIS ---
TODAYS_GAMES = []
TODAYS_NBA = []
TODAYS_UFC = []
ALERT_MEMORY = {}

# O COFRE: Guarda o placar de Greens e Reds do dia
DAILY_STATS = {
    "date": "",
    "green": 0,
    "red": 0,
    "closed": False
}

# --- 2. HELPERS MATEMÁTICOS ---

def american_to_decimal(american_str):
    try:
        if "EV" in str(american_str).upper(): return 2.00
        val = float(american_str)
        if val == 0: return 1.0
        if val < 0: return round((100 / abs(val)) + 1, 2)
        else: return round((val / 100) + 1, 2)
    except: return 0.0

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

# --- 3. PARSERS E FORMATADORES ---

def parse_odds_string(details_str, home_name, away_name):
    pick = "Aguardando Odds"; odd_decimal = 0.0; icon = "⏳"; is_favorite = False
    if not details_str or details_str == '-': return pick, odd_decimal, icon, is_favorite

    try:
        if "EV" in str(details_str).upper(): return "Jogo Equilibrado", 1.90, "⚖️", False
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

def format_card(game, api_raw):
    odds_list = api_raw['competitions'][0].get('odds', [])
    details = odds_list[0].get('details', '-') if odds_list else '-'
    pick, odd, icon, _ = parse_odds_string(details, game['home'], game['away'])
    
    tv_str = f"📺 {game['tv']}" if game['tv'] else ""
    clock_str = f"⏰ {game['clock']}" if game['status'] == 'in' else f"⏰ {game['time']}"
    odd_display = f"@{odd:.2f}" if odd > 0 else "(S/ Odd)"
    
    return (
        f"{safe_html(game['league'])} | {clock_str}\n"
        f"🏟️ <i>{safe_html(game['venue'])}</i>\n"
        f"⚽ <b>{safe_html(game['match'])}</b>\n"
        f"{tv_str}\n"
        f"{icon} <b>{pick}</b>\n"
        f"💰 Odd: <b>{odd_display}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

def format_ufc_card(fight):
    red_odd = american_to_decimal(fight['red_odds'])
    blue_odd = american_to_decimal(fight['blue_odds'])
    odds_str = f"💰 {fight['red']}: @{red_odd}\n💰 {fight['blue']}: @{blue_odd}" if red_odd > 0 else "⚠️ Aguardando Odds"
    title_str = "🏆 <b>VALENDO CINTURÃO</b>\n" if fight['title'] else ""
    return f"🥊 <b>UFC | {fight['time']}</b>\n📍 {safe_html(fight['venue'])}\nℹ️ {fight['card']}\n{title_str}🔴 {safe_html(fight['red'])}\n          Vs\n🔵 {safe_html(fight['blue'])}\n{odds_str}\n━━━━━━━━━━━━━━━━━━━━\n"

def format_nba_card(game):
    return f"🏀 <b>NBA | {game['clock']}</b>\n⚔️ <b>{safe_html(game['match'])}</b>\n{game['tv']}\n✅ {safe_html(game['pick'])}\n📊 Spread: {safe_html(game['odds'])}\n━━━━━━━━━━━━━━━━━━━━\n"

# --- 4. MOTORES DE BUSCA ---

async def fetch_espn_soccer():
    global TODAYS_GAMES
    date_str = get_api_date_str()
    leagues = {'bra.1': '🇧🇷 Brasileirão', 'uefa.champions': '🇪🇺 UCL', 'eng.1': '🇬🇧 Premier', 'esp.1': '🇪🇸 La Liga', 'ita.1': '🇮🇹 Serie A', 'ger.1': '🇩🇪 Bundesliga', 'bra.copa_do_brasil': '🏆 Copa BR'}
    found_games = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        for code, name in leagues.items():
            try:
                r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard?dates={date_str}")
                if r.status_code == 200:
                    data = r.json()
                    for event in data.get('events', []):
                        status = event['status']['type']['state']
                        clock = event['status']['type']['detail']
                        status = 'in' if status == 'in' else ('post' if status == 'post' else 'agendado')
                        
                        comp = event['competitions'][0]['competitors']
                        home = comp[0]['team']['name']; away = comp[1]['team']['name']
                        sh = int(comp[0]['score']); sa = int(comp[1]['score'])
                        venue = event['competitions'][0].get('venue', {}).get('fullName', '-')
                        
                        broadcasts = event['competitions'][0].get('broadcasts', [])
                        tv = broadcasts[0]['names'][0] if broadcasts else ("Premiere/Globo" if 'bra' in code else "")
                        dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                        
                        found_games.append({
                            "id": event['id'], "raw": event,
                            "match": f"{home} x {away}", "home": home, "away": away,
                            "time": dt.strftime("%H:%M"), "league": name,
                            "status": status, "clock": clock,
                            "score_home": sh, "score_away": sa,
                            "venue": venue, "tv": tv
                        })
            except: pass

    found_games.sort(key=lambda x: x['time'])
    TODAYS_GAMES = found_games
    return found_games

async def fetch_espn_ufc():
    global TODAYS_UFC
    ufc_list = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
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
                            "venue": comp.get('venue', {}).get('fullName', '-'),
                            "card": comp.get('card', 'main'), "title": comp.get('type', {}).get('slug') == 'title-fight',
                            "red_odds": "-200", "blue_odds": "+150"
                        })
    except: pass
    TODAYS_UFC = ufc_list
    return ufc_list

async def fetch_espn_nba():
    global TODAYS_NBA
    date_str = get_api_date_str()
    nba_list = []
    br_tz = timezone(timedelta(hours=-3))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}")
            if r.status_code == 200:
                data = r.json()
                for event in data.get('events', []):
                    comp = event['competitions'][0]
                    t_home = comp['competitors'][0]; t_away = comp['competitors'][1]
                    odds = comp.get('odds', [{}])[0].get('details', '-')
                    dt = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    pick = f"Vitória do {t_home['team']['name']}"
                    if odds != '-' and len(odds.split(' ')) > 1:
                        if odds.split(' ')[0] in t_away['team']['abbreviation']: pick = f"Vitória do {t_away['team']['name']}"

                    nba_list.append({
                        "match": f"{t_away['team']['name']} @ {t_home['team']['name']}",
                        "time": dt.strftime("%H:%M"), "clock": event['status']['type']['detail'], 
                        "tv": "NBA League Pass", "pick": pick, "odds": odds
                    })
    except: pass
    TODAYS_NBA = nba_list
    return nba_list

# --- 5. O CÉREBRO: ALERTAS, GREEN/RED E FECHAMENTO ---

async def master_automation_loop(app):
    print("🤖 MASTER LOOP: Vigiando Placar, Green/Red e Horários...")
    global ALERT_MEMORY, DAILY_STATS
    
    while True:
        await asyncio.sleep(60)
        try:
            games = await fetch_espn_soccer()
            now = datetime.now(timezone(timedelta(hours=-3)))
            current_date_str = get_api_date_str()
            
            # Zera o Cofre se virou o dia
            if DAILY_STATS["date"] != current_date_str:
                DAILY_STATS = {"date": current_date_str, "green": 0, "red": 0, "closed": False}
            
            # --- LOOP DE JOGOS (ALERTAS) ---
            for game in games:
                gid = game['id']; status = game['status']
                sh = game['score_home']; sa = game['score_away']
                clock = game['clock']
                
                if gid not in ALERT_MEMORY:
                    ALERT_MEMORY[gid] = {'h': sh, 'a': sa, 'status': status}
                    continue
                old = ALERT_MEMORY[gid]
                
                # GATILHO: GOL
                if status == 'in' and (sh > old['h'] or sa > old['a']):
                    scorer = game['home'] if sh > old['h'] else game['away']
                    msg = f"⚽ <b>GOOOOOOL DO {scorer.upper()}!</b>\n\n🏟️ {game['match']}\n⏱️ {clock}\n🔢 {sh} - {sa}"
                    try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                    except: pass
                
                # GATILHO: ALERTA DE PRESSÃO
                if status == 'in' and ("7" in clock or "8" in clock) and 'alerted' not in old:
                    if sh == sa:
                        msg = f"🔥 <b>ALERTA DE PRESSÃO!</b>\n\n🏟️ {game['match']}\n⏱️ {clock} | Empate!\n💡 <i>Fique atento para gol no final!</i>"
                        try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                        except: pass
                        old['alerted'] = True
                
                # GATILHO: CÁLCULO DE GREEN E RED (O JOGO ACABOU)
                if status == 'post' and old['status'] == 'in':
                    odds_list = game['raw']['competitions'][0].get('odds', [])
                    details = odds_list[0].get('details', '-') if odds_list else '-'
                    pick, _, _, is_fav = parse_odds_string(details, game['home'], game['away'])
                    
                    if is_fav:
                        # Validação matemática da aposta
                        is_green = False
                        is_red = False
                        
                        if "Vitória" in pick:
                            if game['home'] in pick:
                                if sh > sa: is_green = True
                                else: is_red = True
                            elif game['away'] in pick:
                                if sa > sh: is_green = True
                                else: is_red = True
                                
                        if is_green:
                            DAILY_STATS["green"] += 1
                            res_icon = "✅✅ GREEN ABSOLUTO"
                        elif is_red:
                            DAILY_STATS["red"] += 1
                            res_icon = "❌ RED"
                        else:
                            res_icon = "🏁 FINALIZADO" # Fallback
                            
                        print(f"💰 [RESULTADO] {game['match']} -> {res_icon}")
                        
                        msg = f"{res_icon}\n\n⚽ {game['match']}\n🔢 Placar Final: {sh} - {sa}\n🎯 Aposta: {pick}"
                        try: await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                        except: pass
                
                old['h'] = sh; old['a'] = sa; old['status'] = status
                ALERT_MEMORY[gid] = old

            # --- AUTOMAÇÕES DE HORÁRIO FIXO ---
            
            # GRADE DA MANHÃ
            if now.hour == 8 and now.minute == 0:
                if TODAYS_GAMES:
                    txt = f"🦁 <b>BOM DIA! GRADE VIP | {get_display_date()}</b> 🦁\n\n"
                    for g in TODAYS_GAMES:
                        card = format_card(g, g['raw'])
                        if len(txt)+len(card) > 4000:
                            await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML); txt = ""
                        txt += card
                    if txt: await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)

            # NBA A TARDE
            if now.hour == 16 and now.minute == 0:
                await fetch_espn_nba()
                if TODAYS_NBA:
                    txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
                    for g in TODAYS_NBA: txt += format_nba_card(g)
                    await app.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)

            # 📊 FECHAMENTO DO DIA (Balanço de Greens e Reds)
            if now.hour == 23 and now.minute == 50 and not DAILY_STATS["closed"]:
                g_count = DAILY_STATS["green"]
                r_count = DAILY_STATS["red"]
                total = g_count + r_count
                
                if total > 0:
                    win_rate = round((g_count / total) * 100, 1)
                    relatorio = (
                        f"📊 <b>FECHAMENTO DO DIA | {get_display_date()}</b> 📊\n\n"
                        f"✅ <b>GREENS:</b> {g_count}\n"
                        f"❌ <b>REDS:</b> {r_count}\n"
                        f"📈 <b>Taxa de Acerto:</b> {win_rate}%\n\n"
                        f"🦁 <i>O mercado nunca dorme. Voltamos amanhã!</i>"
                    )
                    try: await app.bot.send_message(CHANNEL_ID, relatorio, parse_mode=ParseMode.HTML)
                    except: pass
                
                DAILY_STATS["closed"] = True

        except Exception as e:
            print(f"⚠️ ERRO NO MASTER LOOP: {e}")

async def news_loop(app):
    while True:
        await asyncio.sleep(14400)
        try:
            feed = await asyncio.to_thread(feedparser.parse, "https://ge.globo.com/rss/ge/futebol/")
            if feed.entries:
                entry = feed.entries[0]
                msg = f"🌍 <b>GIRO DE NOTÍCIAS</b>\n\n📰 {safe_html(entry.title)}\n🔗 {entry.link}"
                await app.bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
        except: pass

# --- 6. MENU INTERATIVO ---

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    # Botões organizados em linhas
    botoes = [
        [InlineKeyboardButton("⚽ Grade VIP", callback_data="fut"), InlineKeyboardButton("🥊 UFC", callback_data="ufc")],
        [InlineKeyboardButton("🎫 Bilhete de Ouro", callback_data="ticket"), InlineKeyboardButton("🏀 NBA", callback_data="nba")],
        [InlineKeyboardButton("📊 Resumo do Dia (Green/Red)", callback_data="relatorio")] # Botão novo!
    ]
    await u.message.reply_text("🦁 <b>PAINEL V337 (SISTEMA CONTÁBIL ATIVO)</b>\nControle de Green/Red 100% operante.", reply_markup=InlineKeyboardMarkup(botoes), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    
    if q.data == "fut":
        await q.message.reply_text("🔄 Buscando jogos na ESPN...")
        await fetch_espn_soccer()
        if not TODAYS_GAMES:
            await q.message.reply_text("❌ Sem jogos hoje.")
            return
        txt = f"🦁 <b>GRADE VIP | {get_display_date()}</b> 🦁\n\n"
        for g in TODAYS_GAMES:
            card = format_card(g, g['raw'])
            if len(txt)+len(card) > 4000:
                await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML); txt = ""
            txt += card
        if txt: await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "ufc":
        await q.message.reply_text("🔄 Buscando Lutas...")
        await fetch_espn_ufc()
        if not TODAYS_UFC:
            await q.message.reply_text("❌ Sem UFC hoje.")
            return
        txt = "🥊 <b>CARD UFC</b>\n\n"
        for f in TODAYS_UFC: txt += format_ufc_card(f)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()

    elif q.data == "nba":
        await q.message.reply_text("🔄 Buscando NBA...")
        await fetch_espn_nba()
        if not TODAYS_NBA:
            await q.message.reply_text("❌ Sem NBA na API.")
            return
        txt = f"🏀 <b>NBA | {get_display_date()}</b>\n\n"
        for g in TODAYS_NBA: txt += format_nba_card(g)
        await c.bot.send_message(q.message.chat_id, txt, parse_mode=ParseMode.HTML)
        await q.message.delete()
        
    elif q.data == "ticket":
        if not TODAYS_GAMES: await fetch_espn_soccer()
        cands = [g for g in TODAYS_GAMES if g['status'] != 'post']
        valid = []
        for g in cands:
            odds_list = g['raw']['competitions'][0].get('odds', [])
            d = odds_list[0].get('details', '-') if odds_list else '-'
            p, o, _, fav = parse_odds_string(d, g['home'], g['away'])
            if fav and 1.20 <= o <= 2.20: valid.append({'m': g['match'], 'p': p, 'o': o})
        
        if len(valid) < 2:
            await q.message.reply_text("❌ Jogos seguros insuficientes para gerar a múltipla.")
            return
        random.shuffle(valid)
        msg = "🎫 <b>BILHETE DE OURO</b> 🎫\n\n"
        t = 1.0
        for v in valid[:3]:
            t *= v['o']
            msg += f"✅ <b>{v['m']}</b>\n🎯 {v['p']} (@{v['o']:.2f})\n\n"
        msg += f"🔥 <b>TOTAL: {t:.2f}</b>"
        await c.bot.send_message(q.message.chat_id, msg, parse_mode=ParseMode.HTML)

    elif q.data == "relatorio":
        # Botão novo para checar o cofre na hora
        g_count = DAILY_STATS["green"]
        r_count = DAILY_STATS["red"]
        total = g_count + r_count
        if total == 0:
            await c.bot.send_message(q.message.chat_id, "ℹ️ Nenhum jogo finalizado com aposta hoje ainda.", parse_mode=ParseMode.HTML)
        else:
            win_rate = round((g_count / total) * 100, 1)
            relatorio = (
                f"📊 <b>BALANÇO PARCIAL | {get_display_date()}</b> 📊\n\n"
                f"✅ <b>GREENS:</b> {g_count}\n"
                f"❌ <b>REDS:</b> {r_count}\n"
                f"📈 <b>Taxa de Acerto:</b> {win_rate}%\n"
            )
            await c.bot.send_message(q.message.chat_id, relatorio, parse_mode=ParseMode.HTML)

# --- SERVER ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.wfile.write(b"ONLINE V337")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

async def post_init(app: Application):
    print("🚀 BOT V337 INICIADO! CONTABILIDADE ATIVA.")
    asyncio.create_task(master_automation_loop(app))
    asyncio.create_task(news_loop(app))

def main():
    threading.Thread(target=run_server, daemon=True).start()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).defaults(defaults).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
