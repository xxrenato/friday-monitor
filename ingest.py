import os
import requests
import time
from datetime import datetime, timedelta
from supabase import create_client, Client

# Puxa variáveis do ambiente no GitHub Actions e limpa espaços ocultos
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("⚠️ SUPABASE_URL ou SUPABASE_KEY não foram encontradas nos GitHub Secrets!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Palavras-chave associadas aos CNAEs do MEI
KEYWORDS = [
    "informatica", "computador", "toner", "nobreak", "perifericos",
    "material eletrico", "lampada", "cabo", "ferramentas", "furadeira",
    "moveis", "cadeira", "armario", "limpeza", "saneante", "optica",
    "telefonia", "eletrodomestico", "audio", "video", "material medico"
]

def buscar_e_salvar_pncp():
    data_hoje = datetime.now().strftime("%Y%m%d")
    data_ontem = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    
    url = f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicas?dataInicial={data_ontem}&dataFinal={data_hoje}&codigoModalidadeContratacao=8&uf=RJ&pagina=1"
    
    # Headers para simular um navegador real e evitar bloqueios do servidor do PNCP
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    dados = []
    max_tentativas = 3

    # Tenta conectar até 3 vezes caso a API feche a conexão
    for tentativa in range(1, max_tentativas + 1):
        try:
            print(f"Tentativa {tentativa} de consulta à API do PNCP...")
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                dados = response.json().get('data', [])
                break
            else:
                print(f"Aviso: PNCP retornou status {response.status_code}")
        except Exception as e:
            print(f"Erro de conexão na tentativa {tentativa}: {e}")
            if tentativa < max_tentativas:
                print("Aguardando 5 segundos antes de tentar novamente...")
                time.sleep(5)
            else:
                print("❌ Não foi possível obter dados do PNCP após 3 tentativas.")
                return

    novas_oportunidades = []
    for item in dados:
        objeto = (item.get('objetoContratacao') or '').lower()
        
        # Filtra pelas palavras-chave das atividades da empresa
        if any(kw in objeto for kw in KEYWORDS):
            pncp_id = item.get('numeroContratacaoPNCP') or item.get('id')
            
            registro = {
                "id": str(pncp_id),
                "orgao": item.get('orgaoEntidade', {}).get('razaoSocial', 'Órgão Não Informado'),
                "objeto": item.get('objetoContratacao'),
                "categoria": "Geral/Multiatividade",
                "valor_estimado": item.get('valorTotalEstimado', 0.0),
                "modalidade": "Dispensa Eletrônica",
                "uf": "RJ",
                "link": item.get('linkSistemaOrigem', 'https://pncp.gov.br'),
                "status": "Em Análise"
            }
            novas_oportunidades.append(registro)

    print(f"Oportunidades filtradas e prontas para salvar: {len(novas_oportunidades)}")

    # Salva no Supabase (ignorando duplicados)
    for op in novas_oportunidades:
        try:
            supabase.table("oportunidades").upsert(op, on_conflict="id").execute()
        except Exception as e:
            print(f"Erro ao salvar oportunidade {op['id']}: {e}")

if __name__ == "__main__":
    buscar_e_salvar_pncp()
