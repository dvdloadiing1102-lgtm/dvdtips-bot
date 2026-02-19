import os
import requests
import json
from datetime import datetime, timedelta

# COLE SUA CHAVE AQUI DENTRO DAS ASPAS SE NÃO TIVER NA VARIÁVEL DE AMBIENTE
CHAVE_TESTE = os.getenv("FOOTBALL_DATA_KEY", "SUA_CHAVE_AQUI") 

def testar_api():
    print("🔎 INICIANDO RAIO-X DA API FOOTBALL-DATA.ORG...")
    print(f"🔑 Usando chave: {CHAVE_TESTE[:4]}...****")

    # Data de Hoje e Amanhã (para garantir fuso horário)
    hoje = datetime.now().strftime("%Y-%m-%d")
    amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    url = f"http://api.football-data.org/v4/matches?dateFrom={hoje}&dateTo={amanha}"
    headers = {'X-Auth-Token': CHAVE_TESTE}

    try:
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            print(f"❌ ERRO GRAVE: Código {r.status_code}")
            print(f"Mensagem: {r.text}")
            return

        data = r.json()
        jogos = data.get('matches', [])
        
        print(f"\n📅 Jogos encontrados entre {hoje} e {amanha}: {len(jogos)}")
        print("="*40)
        
        if len(jogos) == 0:
            print("⚠️ AVISO: A API retornou ZERO jogos. Ou não tem jogos nas ligas cobertas, ou a chave tem restrição.")
        
        for jogo in jogos:
            liga = jogo['competition']['name']
            time_casa = jogo['homeTeam']['name']
            time_fora = jogo['awayTeam']['name']
            status = jogo['status']
            hora = jogo['utcDate']
            
            print(f"⚽ [{liga}] {time_casa} x {time_fora}")
            print(f"   Status: {status} | Hora UTC: {hora}")
            print("-" * 20)
            
    except Exception as e:
        print(f"❌ ERRO DE CONEXÃO: {e}")

if __name__ == "__main__":
    testar_api()
