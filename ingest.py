import os
import time
from datetime import datetime, timedelta
from curl_cffi import requests
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("⚠️ SUPABASE_URL ou SUPABASE_KEY não foram encontradas nos GitHub Secrets!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

KEYWORDS = [
    "informatica", "computador", "toner", "nobreak", "perifericos", "impressora",
    "material eletrico", "lampada", "cabo", "ferramentas", "furadeira", "parafuso",
    "moveis", "cadeira", "armario", "mesa", "limpeza", "saneante", "detergente",
    "optica", "telefonia", "eletrodomestico", "audio", "video", "material medico"
]

def buscar_e_salvar_pncp():
    data_hoje = datetime.now().strftime("%Y%m%d")
    data_inicial = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    
    todas_contratacoes = []
    
    for pagina in range(1, 6):
        url = f"https://pncp.gov.br/api/consulta/v1/contratacoes/publicas?dataInicial={data_inicial}&dataFinal={data_hoje}&codigoModalidadeContratacao=8&uf=RJ&pagina={pagina}"
        
        try:
            print(f"Consultando página {pagina} do PNCP...")
            # impersonate="chrome120" emula a handshake TLS de um navegador real
            response = requests.get(url, impersonate="chrome120", timeout=30)
            
            if response.status_code == 200:
                dados = response.json().get('data', [])
                if not dados:
                    print(f"Sem mais dados na página {pagina}.")
                    break
                todas_contratacoes.extend(dados)
                print(f"✅ Página {pagina} capturada com sucesso! ({len(dados)} compras encontradas)")
            else:
                print(f"Aviso: PNCP retornou status {response.status_code}")
            
            time.sleep(2)
        except Exception as e:
            print(f"Erro ao consultar página {pagina}: {e}")

    print(f"Total de contratações analisadas no período: {len(todas_contratacoes)}")

    novas_oportunidades = []
    for item in todas_contratacoes:
        objeto = (item.get('objetoContratacao') or '').lower()
        
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

    print(f"Oportunidades filtradas para as suas atividades: {len(novas_oportunidades)}")

    salvos = 0
    for op in novas_oportunidades:
        try:
            supabase.table("oportunidades").upsert(op, on_conflict="id").execute()
            salvos += 1
        except Exception as e:
            print(f"Erro ao salvar oportunidade {op['id']}: {e}")

    print(f"✅ Concluído! {salvos} oportunidades gravadas com sucesso no Supabase.")

if __name__ == "__main__":
    buscar_e_salvar_pncp()
