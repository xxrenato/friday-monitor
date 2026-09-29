import os
import requests
from datetime import datetime, timedelta
from supabase import create_client, Client

# Configuração do Supabase via Variáveis de Ambiente
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
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
    
    # Consulta a API do PNCP (Dispensa Eletrônica - Modalidade 8 / UF: RJ)
    url = f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicas?dataInicial={data_ontem}&dataFinal={data_hoje}&codigoModalidadeContratacao=8&uf=RJ&pagina=1"
    
    response = requests.get(url, timeout=20)
    if response.status_code != 200:
        print("Falha ao conectar na API do PNCP")
        return

    dados = response.json().get('data', [])
    
    novas_oportunidades = []
    for item in dados:
        objeto = (item.get('objetoContratacao') or '').lower()
        
        # Filtra palavras-chave
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

    # Inserção no Supabase (ignora duplicados pelo ID primário)
    for op in novas_oportunidades:
        try:
            supabase.table("oportunidades").upsert(op, on_conflict="id").execute()
        except Exception as e:
            print(f"Erro ao salvar item {op['id']}: {e}")

if __name__ == "__main__":
    buscar_e_salvar_pncp()
