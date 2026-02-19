# ================= BOT V224 (CIENTISTA DE DADOS - 100% ESTATÍSTICO) =================
import os
import logging
import asyncio
import threading
import httpx
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# ================= 1. CACHE DE ESTATÍSTICAS =================
# Guarda os artilheiros da liga para não precisar baixar toda hora
STATS_CACHE = {}

async def get_league_scorers(league, client):
    """
    Vai na página de ESTATÍSTICAS OFICIAIS da liga e pega quem está fazendo gol.
    """
    if league in STATS_CACHE:
        return STATS_CACHE[league]
        
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/statistics"
    scorers = {}
    
    try:
        r = await client.get(url)
        if r.status_code == 200:
            data = r.json()
            stats = data.get('stats', [])
            
            for stat in stats:
                if stat.get('name') == 'scoring': # Aba de Gols
                    leaders = stat.get('leaders', [])
                    for leader in leaders:
                        athlete_name = leader.get('athlete', {}).get('displayName', '')
                        team_name = leader.get('team', {}).get('name', '')
                        goals = leader.get('value', 0)
                        
                        if team_name and athlete_name:
                            # Salva o melhor artilheiro de cada time
                            if team_name not in scorers:
                                scorers[team_name] = f"{athlete_name} ({int(goals)} Gols na Liga)"
    except Exception as e:
        logging.error(f"Erro ao buscar stats da liga {league}: {e}")

    STATS_CACHE[league] = scorers
    return scorers

# ================= 2. MOTOR DE ANÁLISE MATEMÁTICA =================
async def fetch_and_analyze_games():
    # As ligas que importam (incluindo a Recopa que faltou antes)
    leagues = [
        'uefa.europa', 'uefa.conference', 'uefa.champions', 
        'conmebol.libertadores', 'conmebol.sudamericana', 'conmebol.recopa',
        'bra.1', 'bra.camp.paulista', 'bra.camp.carioca',
        'eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1', 'arg.1', 'ksa.1'
    ]
    
    jogos_analisados = []
    br_tz = timezone(timedelta(hours=-3))
    
    async with httpx.AsyncClient(timeout=20) as client:
        for league in leagues:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
            try:
                r = await client.get(url)
                if r.status_code != 200: continue
                
                data = r.json()
                league_name = data['leagues'][0].get('name', 'Futebol') if data.get('leagues') else 'Futebol'
                
                # Baixa a tabela de artilheiros dessa liga
                league_scorers = await get_league_scorers(league, client)
                
                for event in data.get('events', []):
                    status = event['status']['type']['state']
                    if status not in ['pre', 'in']: continue
                    
                    competitors = event['competitions'][0]['competitors']
                    
                    # Dados Casa (Home)
                    comp_home = competitors[0] if competitors[0]['homeAway'] == 'home' else competitors[1]
                    home_name = comp_home['team']['name']
                    # Pega o histórico: Vitórias-Empates-Derrotas
                    home_record = comp_home.get('records', [{'summary': '0-0-0'}])[0].get('summary', '0-0-0')
                    
                    # Dados Visitante (Away)
                    comp_away = competitors[1] if competitors[1]['homeAway'] == 'away' else competitors[0]
                    away_name = comp_away['team']['name']
                    away_record = comp_away.get('records', [{'summary': '0-0-0'}])[0].get('summary', '0-0-0')
                    
                    dt_utc = datetime.strptime(event['date'], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                    dt_br = dt_utc.astimezone(br_tz)
                    
                    if dt_br.date() != datetime.now(br_tz).date(): continue
                    
                    # === CALCULADORA INTELIGENTE DE MERCADO ===
                    def parse_record(rec_str):
                        try:
                            # A API manda "10-5-2" (V-E-D)
                            v, e, d = map(int, rec_str.split('-'))
                            total = v + e + d
                            win_rate = (v / total) * 100 if total > 0 else 0
                            return win_rate, total
                        except:
                            return 0, 0
                            
                    home_win_rate, h_tot = parse_record(home_record)
                    away_win_rate, a_tot = parse_record(away_record)
                    
                    # O Algoritmo
                    if h_tot == 0 and a_tot == 0:
                        mercado = "Ambas Marcam (Início de Fase/Copa)"
                    elif home_win_rate > 55 and away_win_rate < 35:
                        mercado = f"Vitória do {home_name} (Forma Fortíssima)"
                    elif away_win_rate > 55 and home_win_rate < 35:
                        mercado = f"Vitória do {away_name} (Visitante Superior)"
                    elif home_win_rate > 45 and away_win_rate > 45:
                        mercado = "Over 2.5 Gols (Equilíbrio Ofensivo)"
                    elif home_win_rate < 30 and away_win_rate < 30:
                        mercado = "Under 2.5 Gols (Times em Má Fase)"
                    else:
                        mercado = "Dupla Chance Casa ou Empate"

                    # === BUSCA O ARTILHEIRO REAL ===
                    prop_player = "Dados de artilharia indisponíveis"
                    if home_name in league_scorers:
                        prop_player = f"{league_scorers[home_name]} (Mandante)"
                    elif away_name in league_scorers:
                        prop_player = f"{league_scorers[away_name]} (Visitante)"
                        
                    jogos_analisados.append({
                        "id": f"{home_name}x{away_name}",
                        "time": dt_br.strftime("%H:%M"),
                        "match": f"{home_name} x {away_name}",
                        "league": league_name,
                        "player": prop_player,
                        "market": mercado
                    })
            except Exception as e:
                logging.error(f"Erro ao processar liga {league}: {e}")
                continue
                
    # Remove duplicatas e ordena por horário
    unicos = {j['id']: j for j in jogos_analisados}
    lista_final = list(unicos.values())
    lista_final.sort(key=lambda x: x['time'])
    
    return lista_final[:20]

# ================= 3. FORMATAÇÃO E SERVIDOR =================
def format_game(game):
    return (
        f"🏆 <b>{game['league']}</b>\n"
        f"⏰ <b>{game['time']}</b> | ⚔️ <b>{game['match']}</b>\n"
        f"🎯 <b>Prop:</b> {game['player']} p/ marcar\n"
        f"📊 <b>Estatística de Mercado:</b> {game['market']}\n"
    )

class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"ONLINE - V224 DADOS REAIS")
def run_server(): HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

def get_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton("⚽ Analisar Grade com Estatísticas", callback_data="fut_deep")]])

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text("🦁 <b>BOT V224 ONLINE</b>\nIA Desligada. Operando 100% com dados matemáticos e artilheiros oficiais.", reply_markup=get_menu(), parse_mode=ParseMode.HTML)

async def menu(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "fut_deep":
        msg = await q.message.reply_text("🔎 <b>Baixando relatórios de desempenho e artilharia...</b>\nIsso cruza dados reais e não trava.", parse_mode=ParseMode.HTML)
        
        jogos = await fetch_and_analyze_games()
        
        if not jogos:
            await msg.edit_text("❌ <b>Nenhum jogo encontrado no momento.</b>")
            return

        txt = f"🔥 <b>GRADE MATEMÁTICA ({len(jogos)} Jogos)</b> 🔥\n\n"
        
        for g in jogos:
            txt += format_game(g) + "━━━━━━━━━━━━━━━━\n"

        await msg.edit_text("✅ <b>Grade Finalizada com Fatos e Dados Reais!</b>", parse_mode=ParseMode.HTML)
        await c.bot.send_message(CHANNEL_ID, txt, parse_mode=ParseMode.HTML)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu))
    app.run_polling()

if __name__ == "__main__":
    main()
