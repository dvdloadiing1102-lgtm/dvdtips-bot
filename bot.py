# ================= BOT V216 (NOVO SCRAPER PLACAR + IA JSON) =================
import os
import logging
import asyncio
import httpx
import threading
import random
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import google.generativeai as genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # Flash é rápido, mas Pro é mais obediente com JSON. Vamos de Flash com prompt reforçado.
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

# ================= FONTE 1: API (DADOS ESTRUTURADOS) =================
async def fetch_api_schedule():
    if not FOOTBALL_DATA_KEY: return []
    headers = {'X-Auth-Token': FOOTBALL_DATA_KEY}
    br_tz = timezone(timedelta(hours=-3))
    hoje = datetime.now(br_tz).strftime("%Y-%m-%d")
    amanha = (datetime.now(br_tz) + timedelta(days=1)).strftime("%Y-%m-%d")
    
    url = f"http://api.football-data.org/v4/matches?dateFrom={hoje}&dateTo={amanha}"
    jogos = []
    
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                matches = r.json().get('matches', [])
                for m in matches:
                    if m['status'] not in ['SCHEDULED', 'TIMED', 'IN_PLAY', 'PAUSED']: continue
                    
                    home = m['homeTeam']['name']
                    away = m['awayTeam']['name']
                    # Correção nomes
                    if home == "CA Paranaense": home = "Athletico-PR"
                    if away == "CA Paranaense": away = "Athletico-PR"
                    
                    dt = datetime.strptime(m['utcDate'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    if dt.date() != datetime.now(br_tz).date(): continue

                    jogos.append({
                        "id": f"{home}x{away}".replace(" ", "").lower(),
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "time": dt.strftime("%H:%M"),
                        "source": "API"
                    })
        except: pass
    return jogos

# ================= FONTE 2: NOVO SCRAPER (PLACAR DE FUTEBOL) =================
async def fetch_placar_scraper():
    """
    Raspa o site placardefutebol.com.br (Mais leve que o GE)
    """
    url = "https://www.placardefutebol.com.br/jogos-de-hoje"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    jogos = []
    
    try:
        r = await asyncio.to_thread(requests.get, url, headers=headers)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # O site organiza em containers de jogos
            container_jogos = soup.find_all('div', class_='match-card')
            
            for card in container_jogos[:20]: # Pega até 20 jogos
                try:
                    # Status (Pega só o que não acabou)
                    status = card.find('span', class_='status-name')
                    if status and "ENCERRADO" in status.text.upper(): continue

                    # Horário
                    hora_elem = card.find('span', class_='match-time')
                    hora = hora_elem.text.strip() if hora_elem else "Hoje"
                    
                    # Times
                    times = card.find_all('span', class_='team-name')
                    if len(times) >= 2:
                        home = times[0].text.strip()
                        away = times[1].text.strip()
                    else: continue
                    
                    # Filtro de qualidade (ignora sub-20, feminino se quiser, etc)
                    if "Sub-" in home or "Feminino" in home: continue

                    jogos.append({
                        "id": f"{home}x{away}".replace(" ", "").lower(),
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "time": hora,
                        "source": "WEB"
                    })
                except: continue
    except Exception as e:
        logging.error(f"Erro Scraper: {e}")
        
    return jogos

# ================= UNIFICADOR =================
async def get_hybrid_schedule():
    task1 = fetch_api_schedule()
    task2 = fetch_placar_scraper()
    results = await asyncio.gather(task1, task2)
    
    lista_api = results[0]
    lista_web = results[1]
    
    # DEDUPLICAÇÃO (Prioriza WEB pois tem mais chance de ter a grade completa visual)
    agenda_final = {}
    
    for j in lista_api: agenda_final[j['id']] = j
    for j in lista_web: agenda_final[j['id']] = j # Web sobrescreve API se duplicar
            
    lista_limpa = list(agenda_final.values())
    
    # Ordena por horário (gambiarra pra lidar com "Hoje" e "19:00")
    def sort_key(x):
        return x['time'] if ":" in x['time'] else "23:59"
    
    lista_limpa.sort(key=sort_key)
    
    return lista_limpa[:15]

# ================= IA - MODO JSON ESTRITO (SEM DESCULPAS) =================
async def analyze_match_json(home, away):
    if not model: return {"player": "Artilheiro", "market": "Over 2.5 Gols"}
    
    prompt = f"""
    Analyze the football match: {home} x {away} (Date: Feb 2026).
    
    Return a JSON object with exactly two keys:
    1. "player": The name of the BEST striker for {home}. (NO SENTENCES. JUST THE NAME).
       - If unsure, name the most famous forward in the squad history.
       - If Athletico-PR, use "Canobbio" or "Pablo" (if returned).
    2. "market": The best statistical market (Choose one: "Vitória do Mandante", "Over 2.5 Gols", "Mais de 8.5 Escanteios", "Ambas Marcam").
    
    Output format example:
    {{
        "player": "Vinicius Jr",
        "market": "Over 2.5 Gols"
    }}
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip()
        # Limpa formatação markdown se a IA colocar
        if text.startswith("```json"): text = text.replace("```json", "").replace("```", "")
        
        data = json.loads(text)
        return data
    except:
        # Fallback manual se o JSON falhar
        p = "Canobbio" if "Athletico" in home else "Camisa 9"
        return {"player": p, "market": "Over 2.5 Gols"}

def format_game(game, analysis):
    icon = "🌐" if game['source'] == "WEB" else "📡"
    return (
        f"{icon} <b>Fonte: {game['source']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🎯 <b>Prop:</b> {analysis.get('player', 'Destaque')} p/ marcar\n"
        f"📊 <b>Tendência:</b> {analysis.get('market', 'Over 2.5 Gols')}\n"
    )

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V216 JSON")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Grade V216 (Web + API)", callback_data="fut_deep")]])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>BOT V216 ONLINE</b>\nScraper novo e IA formatada.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "fut_deep":
        msg = await q.message.reply_text("🔎 <b>Buscando grade completa...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await get_hybrid_schedule()
        
        if not jogos:
            await msg.edit_text("❌ <b>Grade Vazia.</b> (Nem API nem Site retornaram jogos).")
            return

        txt = f"🔥 <b>JOGOS ENCONTRADOS ({len(jogos)})</b> 🔥\n\n"
        for i, g in enumerate(jogos, 1):
            await msg.edit_text(f"⏳ <b>Analisando {i}/{len(jogos)}...</b>\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            analysis = await analyze_match_json(g['home'], g['away'])
            txt += format_game(g, analysis) + "━━━━━━━━━━━━━━━━\n"
            await asyncio.sleep(2)

        await msg.edit_text("✅ <b>Postado!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
