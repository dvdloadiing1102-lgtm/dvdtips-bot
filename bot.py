# ================= BOT V215 (API + SITE GE: A REDUNDÂNCIA MÁXIMA) =================
import os
import logging
import asyncio
import httpx
import threading
import random
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
    model = genai.GenerativeModel("gemini-2.5-flash")
else:
    model = None

# ================= FONTE 1: API (ESTRUTURADA) =================
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
                    # Correção API
                    if home == "CA Paranaense": home = "Athletico-PR"
                    if away == "CA Paranaense": away = "Athletico-PR"
                    
                    dt = datetime.strptime(m['utcDate'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(br_tz)
                    if dt.date() != datetime.now(br_tz).date(): continue

                    jogos.append({
                        "id": f"{home}x{away}".replace(" ", "").lower(), # ID único pra deduplicar
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "time": dt.strftime("%H:%M"),
                        "source": "API"
                    })
        except: pass
    return jogos

# ================= FONTE 2: SITE GE (RASPAGEM) =================
async def fetch_ge_scraper():
    url = "https://ge.globo.com/agenda/"
    headers = {'User-Agent': 'Mozilla/5.0'}
    jogos = []
    
    try:
        r = await asyncio.to_thread(requests.get, url, headers=headers)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            # Busca cards de jogos
            cards = soup.find_all('div', class_='b-game-card')
            if not cards: cards = soup.find_all('li', class_='b-game-card')
            
            for card in cards[:15]: # Limite de segurança
                if "Encerrado" in card.text: continue # Pula jogos acabados
                
                try:
                    hora_elem = card.find('span', class_='b-game-card__time')
                    hora = hora_elem.text.strip() if hora_elem else "Hoje"
                    
                    # Times (tenta imagem alt, se falhar tenta texto)
                    imgs = card.find_all('img', class_='b-game-card__team-image')
                    if len(imgs) >= 2:
                        home = imgs[0].get('alt')
                        away = imgs[1].get('alt')
                    else:
                        names = card.find_all('span', class_='b-game-card__team-name')
                        if len(names) >= 2:
                            home = names[0].text.strip()
                            away = names[1].text.strip()
                        else: continue

                    jogos.append({
                        "id": f"{home}x{away}".replace(" ", "").lower(),
                        "match": f"{home} x {away}",
                        "home": home,
                        "away": away,
                        "time": hora,
                        "source": "GE"
                    })
                except: continue
    except: pass
    return jogos

# ================= UNIFICADOR (O GESTOR) =================
async def get_hybrid_schedule():
    # Roda os dois ao mesmo tempo (Paralelismo)
    task1 = fetch_api_schedule()
    task2 = fetch_ge_scraper()
    results = await asyncio.gather(task1, task2)
    
    lista_api = results[0]
    lista_ge = results[1]
    
    # DEDUPLICAÇÃO (Prioriza GE porque costuma ser mais atualizado no horário BR)
    agenda_final = {}
    
    # Adiciona API primeiro
    for j in lista_api:
        agenda_final[j['id']] = j
        
    # Adiciona GE (sobrescreve ou adiciona novos)
    for j in lista_ge:
        # Se já existe, atualizamos só se a fonte anterior não era GE (GE tem prioridade visual)
        agenda_final[j['id']] = j
            
    # Converte de volta para lista
    lista_limpa = list(agenda_final.values())
    
    # Ordena por horário (se possível)
    lista_limpa.sort(key=lambda x: x['time'])
    
    return lista_limpa[:15] # Manda os 15 primeiros jogos do dia

# ================= IA - ANÁLISE BLINDADA (2026) =================
async def analyze_match(home, away):
    if not model: return {"player": "Destaque", "market": "Over 2.5 Gols"}
    
    prompt = f"""
    Data: 19 de Fevereiro de 2026.
    Jogo: {home} x {away}.
    
    Tarefa 1: Nome do ATACANTE TITULAR do {home} HOJE.
    (Cuidado: Mastriani saiu do Athletico. Pablo saiu. Use dados de 2026. Se for Athletico, pense em Canobbio ou o 9 atual).
    
    Tarefa 2: Mercado Estatístico (Apenas 1):
    [Vitória Casa, Vitória Fora, +8.5 Escanteios, +4.5 Cartões, Over 2.5 Gols, Ambas Marcam].
    
    Responda: JOGADOR | MERCADO
    """
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip().replace('*', '')
        if "|" in text:
            p = text.split("|")
            return {"player": p[0].strip(), "market": p[1].strip()}
        return {"player": "Camisa 9", "market": "Over 2.5 Gols"}
    except:
        return {"player": "Destaque", "market": "Over 2.5 Gols"}

def format_game(game, analysis):
    icon = "🌐" if game['source'] == "GE" else "📡"
    return (
        f"{icon} <b>Fonte: {game['source']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🎯 <b>Prop:</b> {analysis['player']} p/ marcar\n"
        f"📊 <b>Tendência:</b> {analysis['market']}\n"
    )

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V215 HIBRIDO")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Grade Híbrida (Site + API)", callback_data="fut_deep")]])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>BOT V215 ONLINE</b>\nO Híbrido: API + Site do Globo Esporte juntos.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "fut_deep":
        msg = await q.message.reply_text("🔎 <b>Acessando API e Site do GE...</b>", parse_mode=ParseMode.HTML)
        
        jogos = await get_hybrid_schedule()
        
        if not jogos:
            await msg.edit_text("❌ <b>Grade vazia em AMBAS as fontes.</b>\n(Verifique se há jogos hoje ou se o site mudou).")
            return

        txt = f"🔥 <b>JOGOS ENCONTRADOS ({len(jogos)})</b> 🔥\n\n"
        for i, g in enumerate(jogos, 1):
            await msg.edit_text(f"⏳ <b>Analisando {i}/{len(jogos)}...</b>\n👉 <i>{g['match']}</i>", parse_mode=ParseMode.HTML)
            
            analysis = await analyze_match(g['home'], g['away'])
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
