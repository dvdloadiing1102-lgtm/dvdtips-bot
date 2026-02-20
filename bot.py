import asyncio
import httpx
from datetime import datetime

# CONFIGURAÇÃO DA DATA (Forçando a data que você viu os jogos: 20/02/2026)
# Formato YYYYMMDD
DATA_HOJE = "20260220" 

async def testar_api_espn():
    print(f"🕵️ INICIANDO DIAGNÓSTICO ESPN PARA A DATA: {DATA_HOJE}")
    print("="*50)

    # As ligas que você viu na lista das 06:19
    ligas_para_testar = [
        ('ksa.1', 'Arábia Saudita'),
        ('ger.1', 'Bundesliga'),
        ('ita.1', 'Serie A'),
        ('fra.1', 'Ligue 1'),
        ('esp.1', 'La Liga'),
        ('arg.1', 'Argentina'),
        ('tur.1', 'Turquia') # Teste extra
    ]

    async with httpx.AsyncClient(timeout=10) as client:
        total_jogos_encontrados = 0
        
        for codigo, nome in ligas_para_testar:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{codigo}/scoreboard?dates={DATA_HOJE}"
            
            try:
                print(f"⏳ Verificando {nome} ({codigo})...", end="")
                r = await client.get(url)
                
                if r.status_code == 200:
                    data = r.json()
                    eventos = data.get('events', [])
                    qtd = len(eventos)
                    
                    if qtd > 0:
                        print(f" ✅ SUCESSO! {qtd} Jogo(s) encontrado(s).")
                        for e in eventos:
                            try:
                                time = e['date'].split('T')[1][:5] # Hora UTC
                                c = e['competitions'][0]['competitors']
                                home = c[0]['team']['name']
                                away = c[1]['team']['name']
                                status = e['status']['type']['state']
                                print(f"    -> [{time} UTC] {home} x {away} (Status: {status})")
                            except:
                                print(f"    -> Erro ao ler detalhes do jogo.")
                        total_jogos_encontrados += qtd
                    else:
                        print(" ❌ Lista Vazia (ESPN retornou 0 eventos).")
                else:
                    print(f" ⚠️ Erro HTTP {r.status_code}")
            
            except Exception as e:
                print(f" ☠️ Erro de Conexão: {e}")
            
            print("-" * 30)

        print("="*50)
        print(f"📊 RESUMO FINAL: {total_jogos_encontrados} jogos encontrados na grade de hoje.")
        if total_jogos_encontrados > 0:
            print("CONCLUSÃO: A API está funcionando. O erro estava no filtro do Bot.")
        else:
            print("CONCLUSÃO: A API da ESPN está vazia ou bloqueada para essa data/hora.")

# Executa o teste
if __name__ == "__main__":
    asyncio.run(testar_api_espn())
