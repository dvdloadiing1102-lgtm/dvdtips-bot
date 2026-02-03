import os
import sys
import json
import logging
import uuid
import threading
import time
import random
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- AUTO-INSTALAÇÃO ---
try:
    import requests
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import google.generativeai as genai
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler
    from telegram.error import Conflict
except ImportError:
    print("⚠️ Instalando dependências...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot", "flask", "matplotlib", "requests", "google-generativeai"])
    os.execv(sys.executable, ['python'] + sys.argv)

# ================= CONFIGURAÇÃO =================
TOKEN = os.getenv("BOT_TOKEN") 
ADMIN_ID = os.getenv("ADMIN_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_URL")
DB_FILE = "dvd_tips_v9.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuração IA (Com tratamento de erro silencioso)
USE_GEMINI = False
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        USE_GEMINI = True
        logger.info("✅ Gemini API Ativa")
    except:
        logger.warning("⚠️ Gemini Falhou. Usando Backup Local.")

# Estados
INPUT_ANALISE, INPUT_CALC, INPUT_GESTAO, INPUT_GURU, VIP_KEY = range(5)

# ================= BANCO DE DADOS =================
def load_db():
    default = {"users": {}, "keys": {}, "last_run": "", "api_cache": None, "api_cache_time": None}
    if not os.path.exists(DB_FILE): return default
    try:
        with open(DB_FILE, "r") as f: return json.load(f)
    except: return default

def save_db(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=2)

db = load_db()

# ================= SERVIDOR WEB =================
def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DVD TIPS V9.0 ONLINE")
        def do_HEAD(self): 
            self.send_response(200)
            self.end_headers()
    try: HTTPServer(("0.0.0.0", port), Handler).serve_forever()
    except: pass

def run_pinger():
    if not RENDER_URL: return
    while True:
        time.sleep(600)
        try: requests.get(RENDER_URL, timeout=10)
        except: pass

threading.Thread(target=start_web_server, daemon=True).start()
threading.Thread(target=run_pinger, daemon=True).start()

# ================= SISTEMA HÍBRIDO DE INTELIGÊNCIA =================
# Se o Gemini falhar, usamos estas frases para não deixar o usuário na mão
BACKUP_PHRASES = [
    "Tendência forte de gols baseada no histórico recente.",
    "Time da casa muito forte jogando em seus domínios.",
    "Defesas instáveis, grande chance de Over.",
    "Favorito deve confirmar a vitória sem sustos.",
    "Jogo truncado, ideal para explorar o mercado de under.",
    "Estatísticas apontam superioridade clara do mandante.",
    "Confronto direto favorece muito essa aposta.",
    "Ataque visitante vive fase iluminada, olho neles.",
    "Must win game! O time precisa vencer a qualquer custo.",
    "Odd desajustada, valor alto encontrado aqui."
]

def get_smart_analysis(match, tip, context="tip"):
    # 1. Tenta Gemini (Se ativado)
    if USE_GEMINI:
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            if context == "tip":
                prompt = f"Jogo: {match}. Tip: {tip}. Justifique em 1 frase curta e técnica (PT-BR)."
            elif context == "guru":
                prompt = f"Responda curto sobre apostas: {match}"
            elif context == "analise":
                prompt = f"Analise {match}. Dê vencedor e gols. PT-BR."
            
            response = model.generate_content(prompt)
            if response.text: return response.text.strip()
        except:
            pass # Falhou silenciosamente, vai para o backup
    
    # 2. Backup Local (Nunca falha)
    if context == "analise":
        return "Análise Indisponível no momento. Siga a gestão de banca."
    
    return random.choice(BACKUP_PHRASES)

# ================= MOTOR DE ODDS (THE ODDS API) =================
def get_real_matches(force_refresh=False):
    if not ODDS_API_KEY: return generate_simulated_matches()
    
    # Cache 45 min
    if not force_refresh and db.get("api_cache") and db.get("api_cache_time"):
        last = datetime.strptime(db["api_cache_time"], "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - last).total_seconds() < 2700: return db["api_cache"]
    
    matches = []
    
    # Busca "Upcoming" (Próximos Jogos) - Mais seguro para API Free
    url = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu,us&markets=h2h,totals&oddsFormat=decimal"
    
    try:
        response = requests.get(url)
        if response.status_code != 200: return generate_simulated_matches()
        
        data = response.json()
        now_utc = datetime.now(timezone.utc)
        
        for game in data:
            sport = game['sport_key']
            
            # FILTRO: Só Futebol e Basquete
            if 'soccer' not in sport and 'basketball' not in sport: continue
            
            # FILTRO: Ignora ligas "estranhas" se possível (Filtro simples por nome)
            league = game['sport_title']
            
            game_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            
            # Janela: Jogos entre agora e +24h
            if not (now_utc < game_time < now_utc + timedelta(hours=24)): continue
            
            # Horário BR
            time_str = (game_time - timedelta(hours=3)).strftime("%H:%M")
            
            bookmakers = game.get('bookmakers', [])
            if not bookmakers: continue
            
            # Pega odds
            market_h2h = next((m for m in bookmakers[0]['markets'] if m['key'] == 'h2h'), None)
            market_tot = next((m for m in bookmakers[0]['markets'] if m['key'] == 'totals'), None)
            
            tip, odd = None, 0
            
            # LÓGICA DE TIP
            if market_h2h:
                outcomes = sorted(market_h2h['outcomes'], key=lambda x: x['price'])
                fav = outcomes[0]
                # Futebol: Fav entre 1.25 e 2.10
                # Basquete: Fav entre 1.10 e 2.50
                min_odd = 1.10 if 'basketball' in sport else 1.25
                if min_odd <= fav['price'] <= 2.20:
                    tip = f"Vence {fav['name']}"
                    odd = fav['price']
            
            # Se não achou vencedor bom, tenta Over
            if (not tip) and market_tot:
                # Pega a linha principal
                line = market_tot['outcomes'][0] 
                # Se for basquete, pega qualquer over razoável
                if 'basketball' in sport:
                     over = next((o for o in market_tot['outcomes'] if o['name'] == 'Over'), None)
                     if over: 
                         point = line.get('point', 0)
                         tip = f"Over {point} Pontos"
                         odd = over['price']
                else:
                    # Futebol: Busca Over 2.5
                    over25 = next((o for o in market_tot['outcomes'] if o['name'] == 'Over' and o['point'] == 2.5), None)
                    if over25 and 1.50 <= over25['price'] <= 2.20:
                        tip = "Over 2.5 Gols"
                        odd = over25['price']

            if tip and odd > 1.0:
                matches.append({
                    "match": f"{game['home_team']} x {game['away_team']}",
                    "tip": tip,
                    "odd": odd,
                    "league": league,
                    "time": time_str
                })

        # Ordena por horário
        matches.sort(key=lambda x: x['time'])
        
        # Salva se encontrou pelo menos 3 jogos
        if len(matches) >= 3:
            db["api_cache"] = matches
            db["api_cache_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_db(db)
            return matches
            
        return generate_simulated_matches()

    except Exception as e:
        logger.error(f"Erro API: {e}")
        return generate_simulated_matches()

def generate_simulated_matches():
    # Fallback bonito caso a API falhe
    return [
        {"match": "Flamengo x Fluminense", "tip": "Ambas Marcam", "odd": 1.80, "league": "Simulado (API Off)", "time": "21:00"},
        {"match": "Lakers x Celtics", "tip": "Over 220 Pontos", "odd": 1.90, "league": "Simulado (API Off)", "time": "22:00"},
        {"match": "Man City x Chelsea", "tip": "Vence Man City", "odd": 1.45, "league": "Simulado (API Off)", "time": "16:00"}
    ]

# ================= MENUS =================
def get_main_keyboard():
    keyboard = [
        ["🔮 Analisar Jogo", "🧮 Calculadora"],
        ["🦓 Zebra do Dia", "🛡️ Aposta Segura"],
        ["💰 Gestão Banca", "🤖 Guru IA"],
        ["🏆 Ligas", "📋 Jogos Hoje"],
        ["📚 Glossário", "🎫 Meu Status"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# ================= FUNÇÕES DO BOT =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if uid not in db["users"]: db["users"][uid] = {"vip_expiry": ""}
    save_db(db)
    # Limpa conflitos
    await context.bot.delete_webhook(drop_pending_updates=True)
    await update.message.reply_text("👋 **DVD TIPS V9.0**\nPronto para lucrar!", reply_markup=get_main_keyboard())

# --- FUNÇÕES DE ANÁLISE ---
async def start_analise(u, c): await u.message.reply_text("⚽/🏀 **Qual jogo analisar?**\n(Digite Times)"); return INPUT_ANALISE
async def handle_analise(u, c):
    msg = u.message.text
    await u.message.reply_text("🧠 _Analisando..._")
    # Tenta IA real, se falhar, avisa
    res = get_smart_analysis(msg, "", "analise")
    await u.message.reply_text(f"🤖 **Análise:**\n{res}", parse_mode="Markdown")
    return ConversationHandler.END

# --- LISTAS DE JOGOS (Corrigido: Agora mostra as Tips!) ---
async def direct_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache") or get_real_matches(True)
    if not tips: return await update.message.reply_text("📭 Sem jogos no momento.")
    
    # CORREÇÃO: Agora mostra a TIP na lista
    txt = ""
    for t in tips[:12]:
        txt += f"⏰ {t['time']} | {t['match']}\n   👉 **{t['tip']}** (@{t['odd']})\n\n"
        
    await update.message.reply_text(f"📋 **Agenda do Dia:**\n\n{txt}", parse_mode="Markdown")

async def direct_ligas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tips = db.get("api_cache") or get_real_matches(True)
    if not tips: return await update.message.reply_text("📭 Sem dados.")
    ligas = sorted(list(set([t['league'] for t in tips])))
    await update.message.reply_text(f"🏆 **Ligas na Grade:**\n" + "\n".join([f"• {l}" for l in ligas]))

# --- OUTRAS FUNÇÕES ---
async def start_calc(u, c): await u.message.reply_text("🧮 `Valor Odd`"); return INPUT_CALC
async def handle_calc(u, c): 
    try: v,o=map(float,u.message.text.replace(",", ".").split()); await u.message.reply_text(f"✅ Lucro: R$ {v*(o-1):.2f}")
    except: await u.message.reply_text("❌ Erro")
    return ConversationHandler.END

async def start_gestao(u, c): await u.message.reply_text("💰 Banca Total?"); return INPUT_GESTAO
async def handle_gestao(u, c):
    try: b=float(u.message.text.replace(",", ".")); await u.message.reply_text(f"📊 Aposta (2%): R$ {b*0.02:.2f}")
    except: await u.message.reply_text("❌ Erro")
    return ConversationHandler.END

async def start_guru(u, c): await u.message.reply_text("🤖 Pergunte:"); return INPUT_GURU
async def handle_guru(u, c): await u.message.reply_text(get_smart_analysis(u.message.text, "", "guru")); return ConversationHandler.END

async def direct_zebra(u, c):
    t = db.get("api_cache") or get_real_matches(True)
    m = max(t, key=lambda x: x['odd']) if t else None
    if m: await u.message.reply_text(f"🦓 **ZEBRA:**\n⚽ {m['match']}\n🎯 {m['tip']} (@{m['odd']})")
    else: await u.message.reply_text("📭 Nada.")

async def direct_segura(u, c):
    t = db.get("api_cache") or get_real_matches(True)
    m = min(t, key=lambda x: x['odd']) if t else None
    if m: await u.message.reply_text(f"🛡️ **SEGURA:**\n⚽ {m['match']}\n🎯 {m['tip']} (@{m['odd']})")
    else: await u.message.reply_text("📭 Nada.")

async def direct_glossario(u, c): await u.message.reply_text("📚 **Glossário:**\nOver: Mais\nUnder: Menos\nML: Vencedor")
async def direct_status(u, c): await u.message.reply_text(f"🎫 ID: `{u.effective_user.id}`")

# --- ADMIN ---
def check_admin(uid): return str(uid) == str(ADMIN_ID)
def generate_key(d): k="K-"+secrets.token_hex(4).upper(); db["keys"][k]=d; save_db(db); return k

async def admin_cmd(u, c):
    if not check_admin(u.effective_user.id): return
    kb = [[InlineKeyboardButton("🚀 Enviar Tips", callback_data="force_tips")], [InlineKeyboardButton("🔑 Chave", callback_data="gen_key")], [InlineKeyboardButton("🔍 Debug", callback_data="debug")]]
    await u.message.reply_text("👑 Admin", reply_markup=InlineKeyboardMarkup(kb))

async def force_tips(u, c):
    await u.callback_query.message.reply_text("🚀 Enviando...")
    tips = get_real_matches(True)
    if not tips: await u.callback_query.message.reply_text("❌ API Vazia."); return
    
    for uid in db["users"]:
        try:
            await c.bot.send_message(uid, "📅 **TIPS DE HOJE:**")
            for t in tips[:6]:
                # Usa backup se IA falhar
                reason = get_smart_analysis(t['match'], t['tip'], "tip")
                msg = f"🏆 {t['league']}\n⏰ {t['time']} | ⚔️ {t['match']}\n🎯 **{t['tip']}** (@{t['odd']})\n🧠 _{reason}_"
                await c.bot.send_message(uid, msg, parse_mode="Markdown")
        except: pass
    await u.callback_query.message.reply_text("✅ Feito.")

async def debug_cmd(u, c):
    status_ia = "✅ ON" if USE_GEMINI else "❌ OFF (Backup Ativo)"
    status_api = "✅ Configurada" if ODDS_API_KEY else "❌ Faltando"
    games_cache = len(db.get("api_cache") or [])
    await u.callback_query.message.reply_text(f"🔍 **DEBUG:**\nIA: {status_ia}\nOdds API: {status_api}\nJogos em Cache: {games_cache}")

async def gen_key_h(u, c): await u.callback_query.message.reply_text(f"`{generate_key(30)}`", parse_mode="Markdown")
async def start_vip(u, c): 
    if u.callback_query: await u.callback_query.answer()
    await u.message.reply_text("🔑 Chave:"); return VIP_KEY
async def handle_vip(u, c):
    k=u.message.text.strip(); uid=str(u.effective_user.id)
    if k in db["keys"]:
        db["users"][uid]["vip_expiry"] = "Ativo"
        save_db(db)
        await u.message.reply_text("✅ VIP Ativo!")
    else: await u.message.reply_text("❌")
    return ConversationHandler.END
async def cancel(u, c): await u.message.reply_text("❌", reply_markup=get_main_keyboard()); return ConversationHandler.END

if __name__ == "__main__":
    if not TOKEN: sys.exit("Falta TOKEN")
    try:
        app = ApplicationBuilder().token(TOKEN).build()
        
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🔮 Analisar Jogo$"), start_analise)], states={INPUT_ANALISE: [MessageHandler(filters.TEXT, handle_analise)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🧮 Calculadora$"), start_calc)], states={INPUT_CALC: [MessageHandler(filters.TEXT, handle_calc)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^💰 Gestão Banca$"), start_gestao)], states={INPUT_GESTAO: [MessageHandler(filters.TEXT, handle_gestao)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🤖 Guru IA$"), start_guru)], states={INPUT_GURU: [MessageHandler(filters.TEXT, handle_guru)]}, fallbacks=[CommandHandler("cancel", cancel)]))
        app.add_handler(ConversationHandler(entry_points=[CommandHandler("vip", start_vip), CallbackQueryHandler(start_vip, pattern="^enter_key$")], states={VIP_KEY: [MessageHandler(filters.TEXT, handle_vip)]}, fallbacks=[]))

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("admin", admin_cmd))
        app.add_handler(MessageHandler(filters.Regex("^🦓 Zebra do Dia$"), direct_zebra))
        app.add_handler(MessageHandler(filters.Regex("^🛡️ Aposta Segura$"), direct_segura))
        app.add_handler(MessageHandler(filters.Regex("^🏆 Ligas$"), direct_ligas))
        app.add_handler(MessageHandler(filters.Regex("^📋 Jogos Hoje$"), direct_jogos))
        app.add_handler(MessageHandler(filters.Regex("^📚 Glossário$"), direct_glossario))
        app.add_handler(MessageHandler(filters.Regex("^🎫 Meu Status$"), direct_status))
        
        app.add_handler(CallbackQueryHandler(force_tips, pattern="^force_tips$"))
        app.add_handler(CallbackQueryHandler(gen_key_h, pattern="^gen_key$"))
        app.add_handler(CallbackQueryHandler(debug_cmd, pattern="^debug$"))

        print("🤖 V9.0 ONLINE")
        app.run_polling(drop_pending_updates=True)
    except Conflict:
        print("🚨 CONFLITO! Reiniciando...")
        time.sleep(5)
        os.execv(sys.executable, ['python'] + sys.argv)