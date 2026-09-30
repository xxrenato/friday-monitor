import os
import re
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from supabase import Client, create_client
from urllib3.util.retry import Retry


PNCP_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
PAGE_SIZE = 50  # maior tamanho aceito por este endpoint do PNCP
REQUEST_TIMEOUT = 60
BATCH_SIZE = 100

KEYWORD_GROUPS = {
    "Informática": (
        "informatica",
        "computador",
        "notebook",
        "periferico",
        "equipamento de ti",
        "tecnologia da informacao",
    ),
    "Impressão e suprimentos": ("toner", "impressora", "cartucho"),
    "Elétrica e ferramentas": (
        "material eletrico",
        "lampada",
        "cabo",
        "ferramenta",
        "furadeira",
        "parafuso",
    ),
    "Móveis": ("moveis", "cadeira", "armario", "mesa"),
    "Limpeza": ("limpeza", "saneante", "detergente"),
    "Óptica, telefonia e audiovisual": (
        "optica",
        "telefonia",
        "eletrodomestico",
        "audio",
        "video",
    ),
    "Material médico": ("material medico", "material hospitalar"),
}


def normalizar(texto: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(caractere)
    )
    return sem_acentos.casefold()


NORMALIZED_KEYWORDS = {
    categoria: tuple(normalizar(palavra) for palavra in palavras)
    for categoria, palavras in KEYWORD_GROUPS.items()
}


def contem_termo(texto: str, termo: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(termo)}(?!\w)", texto) is not None


def categorizar(item: dict) -> str | None:
    texto = normalizar(
        " ".join(
            filter(
                None,
                (
                    item.get("objetoCompra"),
                    item.get("informacaoComplementar"),
                ),
            )
        )
    )
    categorias = [
        categoria
        for categoria, palavras in NORMALIZED_KEYWORDS.items()
        if any(contem_termo(texto, palavra) for palavra in palavras)
    ]
    return ", ".join(categorias) if categorias else None


def criar_sessao_http() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sessao = requests.Session()
    sessao.mount("https://", adapter)
    sessao.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "friday-monitor/1.0",
        }
    )
    return sessao


def consultar_pagina(
    sessao: requests.Session,
    data_inicial: str,
    data_final: str,
    modalidade: int,
    uf: str,
    pagina: int,
) -> tuple[list[dict], int, int]:
    resposta = sessao.get(
        PNCP_URL,
        params={
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "codigoModalidadeContratacao": modalidade,
            "uf": uf,
            "pagina": pagina,
            "tamanhoPagina": PAGE_SIZE,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resposta.raise_for_status()
    payload = resposta.json()
    return (
        payload.get("data") or [],
        int(payload.get("totalPaginas") or pagina),
        int(payload.get("totalRegistros") or 0),
    )


def buscar_contratacoes() -> list[dict]:
    dias = int(os.environ.get("PNCP_LOOKBACK_DAYS", "15"))
    modalidade = int(os.environ.get("PNCP_MODALIDADE", "8"))
    uf = os.environ.get("PNCP_UF", "RJ").strip().upper()
    max_paginas = int(os.environ.get("PNCP_MAX_PAGES", "100"))

    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    data_final = agora.strftime("%Y%m%d")
    data_inicial = (agora - timedelta(days=dias)).strftime("%Y%m%d")

    sessao = criar_sessao_http()
    todas: list[dict] = []
    total_paginas = 1
    total_registros = 0
    pagina = 1

    while pagina <= min(total_paginas, max_paginas):
        dados, total_paginas, total_registros = consultar_pagina(
            sessao,
            data_inicial,
            data_final,
            modalidade,
            uf,
            pagina,
        )
        todas.extend(dados)
        print(
            f"PNCP: página {pagina}/{total_paginas}, "
            f"{len(dados)} registros recebidos."
        )
        if not dados:
            break
        pagina += 1
        time.sleep(0.25)

    if total_paginas > max_paginas:
        raise RuntimeError(
            f"A consulta retornou {total_paginas} páginas, acima do limite "
            f"PNCP_MAX_PAGES={max_paginas}. Aumente o limite para não perder dados."
        )

    print(
        f"PNCP: {len(todas)} de {total_registros} contratações carregadas "
        f"entre {data_inicial} e {data_final}."
    )
    return todas


def construir_link(item: dict) -> str:
    orgao = item.get("orgaoEntidade") or {}
    cnpj = orgao.get("cnpj")
    ano = item.get("anoCompra")
    sequencial = item.get("sequencialCompra")
    if cnpj and ano and sequencial:
        return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}"
    return (
        item.get("linkProcessoEletronico")
        or item.get("linkSistemaOrigem")
        or "https://pncp.gov.br/app/editais"
    )


def transformar(item: dict) -> dict | None:
    categoria = categorizar(item)
    identificador = item.get("numeroControlePNCP")
    objeto = item.get("objetoCompra")
    if not categoria or not identificador or not objeto:
        return None

    orgao = item.get("orgaoEntidade") or {}
    unidade = item.get("unidadeOrgao") or {}
    return {
        "id": str(identificador),
        "orgao": orgao.get("razaoSocial") or "Órgão não informado",
        "objeto": objeto,
        "categoria": categoria,
        "valor_estimado": item.get("valorTotalEstimado") or 0,
        "modalidade": item.get("modalidadeNome") or "Não informada",
        "uf": unidade.get("ufSigla") or os.environ.get("PNCP_UF", "RJ"),
        "link": construir_link(item),
        "data_publicacao": item.get("dataPublicacaoPncp"),
    }


def filtrar_oportunidades(contratacoes: list[dict]) -> list[dict]:
    por_id: dict[str, dict] = {}
    for item in contratacoes:
        oportunidade = transformar(item)
        if oportunidade:
            por_id[oportunidade["id"]] = oportunidade
    oportunidades = list(por_id.values())
    print(f"Filtro: {len(oportunidades)} oportunidades compatíveis encontradas.")
    return oportunidades


def carregar_status_existentes(supabase: Client) -> dict[str, str]:
    resposta = supabase.table("oportunidades").select("id,status").execute()
    return {
        str(item["id"]): item.get("status") or "Em Análise"
        for item in (resposta.data or [])
    }


def salvar_oportunidades(supabase: Client, oportunidades: list[dict]) -> int:
    if not oportunidades:
        return 0

    status_existentes = carregar_status_existentes(supabase)
    for oportunidade in oportunidades:
        oportunidade["status"] = status_existentes.get(
            oportunidade["id"], "Em Análise"
        )

    salvos = 0
    for inicio in range(0, len(oportunidades), BATCH_SIZE):
        lote = oportunidades[inicio : inicio + BATCH_SIZE]
        supabase.table("oportunidades").upsert(
            lote, on_conflict="id"
        ).execute()
        salvos += len(lote)
        print(f"Supabase: {salvos}/{len(oportunidades)} registros gravados.")
    return salvos


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_KEY", "").strip()
    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "SUPABASE_URL e SUPABASE_KEY precisam estar configuradas nos GitHub Secrets."
        )

    supabase: Client = create_client(supabase_url, supabase_key)
    contratacoes = buscar_contratacoes()
    oportunidades = filtrar_oportunidades(contratacoes)
    salvos = salvar_oportunidades(supabase, oportunidades)
    print(f"Concluído: {salvos} oportunidades sincronizadas com o Supabase.")


if __name__ == "__main__":
    main()

