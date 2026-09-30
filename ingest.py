import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from supabase import Client, create_client
from urllib3.util.retry import Retry


PNCP_CONTRATACOES_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
PNCP_ITENS_URL = "https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens"
PAGE_SIZE = 50
REQUEST_TIMEOUT = 60
BATCH_SIZE = 100
ITEM_WORKERS = 8
_thread_local = threading.local()

KEYWORD_GROUPS = {
    "Informática": (
        "informatica", "computador", "notebook", "desktop", "monitor", "periferico",
        "equipamento de ti", "tecnologia da informacao", "ssd", "memoria ram", "nobreak",
        "roteador", "switch", "webcam", "teclado", "mouse",
    ),
    "Impressão e suprimentos": ("toner", "impressora", "cartucho", "multifuncional"),
    "Material de Escritório": (
        "material de escritorio", "papelaria", "papel a4", "caneta", "envelope",
        "grampeador", "arquivo", "expediente",
    ),
    "Elétrica e ferramentas": (
        "material eletrico", "lampada", "cabo eletrico", "ferramenta", "furadeira",
        "parafuso", "eletrico",
    ),
    "Móveis": ("moveis", "cadeira", "armario", "mesa de escritorio", "estante"),
    "Material de Limpeza": (
        "material de limpeza", "limpeza", "saneante", "detergente", "desinfetante",
        "papel higienico",
    ),
    "Óptica, telefonia e audiovisual": (
        "optica", "telefonia", "telefone", "audio", "video", "projetor", "televisor",
    ),
    "Eletrodomésticos": (
        "eletrodomestico", "geladeira", "refrigerador", "micro-ondas", "bebedouro",
        "ventilador", "ar condicionado",
    ),
    "Material de Construção": (
        "material de construcao", "cimento", "tinta", "argamassa", "hidraulico",
        "tubo pvc", "torneira",
    ),
    "EPI e uniformes": (
        "equipamento de protecao individual", "epi", "uniforme", "bota", "luva",
        "capacete de seguranca",
    ),
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


def categorizar_texto(texto: str) -> str | None:
    texto_normalizado = normalizar(texto)
    categorias = [
        categoria
        for categoria, palavras in NORMALIZED_KEYWORDS.items()
        if any(contem_termo(texto_normalizado, palavra) for palavra in palavras)
    ]
    return ", ".join(categorias) if categorias else None


def categorizar(item: dict) -> str | None:
    return categorizar_texto(
        " ".join(
            filter(None, (item.get("objetoCompra"), item.get("informacaoComplementar")))
        )
    )


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
    sessao.headers.update({"Accept": "application/json", "User-Agent": "friday-monitor/2.0"})
    return sessao


def sessao_da_thread() -> requests.Session:
    if not hasattr(_thread_local, "sessao"):
        _thread_local.sessao = criar_sessao_http()
    return _thread_local.sessao


def modalidades_configuradas() -> list[int]:
    valor = os.environ.get("PNCP_MODALIDADES") or os.environ.get("PNCP_MODALIDADE") or "4,6,7,8,12"
    modalidades = []
    for parte in valor.split(","):
        parte = parte.strip()
        if parte:
            modalidades.append(int(parte))
    return list(dict.fromkeys(modalidades))


def consultar_pagina(
    sessao: requests.Session,
    data_inicial: str,
    data_final: str,
    modalidade: int,
    uf: str,
    pagina: int,
) -> tuple[list[dict], int, int]:
    parametros = {
        "dataInicial": data_inicial,
        "dataFinal": data_final,
        "codigoModalidadeContratacao": modalidade,
        "pagina": pagina,
        "tamanhoPagina": PAGE_SIZE,
    }
    if uf:
        parametros["uf"] = uf
    resposta = sessao.get(
        PNCP_CONTRATACOES_URL,
        params=parametros,
        timeout=REQUEST_TIMEOUT,
    )
    resposta.raise_for_status()
    payload = resposta.json()
    return (
        payload.get("data") or [],
        int(payload.get("totalPaginas") or pagina),
        int(payload.get("totalRegistros") or 0),
    )


def buscar_modalidade(
    modalidade: int,
    data_inicial: str,
    data_final: str,
    uf: str,
    max_paginas: int,
    atraso_pagina: float,
) -> dict[str, dict]:
    sessao = criar_sessao_http()
    por_id: dict[str, dict] = {}
    total_paginas = 1
    total_registros = 0
    pagina = 1
    carregados = 0
    while pagina <= min(total_paginas, max_paginas):
        dados, total_paginas, total_registros = consultar_pagina(
            sessao, data_inicial, data_final, modalidade, uf, pagina
        )
        for item in dados:
            identificador = item.get("numeroControlePNCP")
            if identificador:
                por_id[str(identificador)] = item
        carregados += len(dados)
        if pagina == 1 or pagina % 50 == 0 or pagina == total_paginas:
            print(
                f"PNCP modalidade {modalidade}: página {pagina}/{total_paginas}, "
                f"{carregados} registros carregados."
            )
        if not dados:
            break
        pagina += 1
        time.sleep(atraso_pagina)
    if total_paginas > max_paginas:
        print(
            f"AVISO: modalidade {modalidade} retornou {total_paginas} páginas; "
            f"foram processadas as {max_paginas} primeiras."
        )
    print(f"PNCP modalidade {modalidade}: {carregados}/{total_registros} carregados.")
    return por_id


def buscar_contratacoes() -> list[dict]:
    dias = int(os.environ.get("PNCP_LOOKBACK_DAYS", "15"))
    uf = os.environ.get("PNCP_UF", "").strip().upper()
    max_paginas = int(os.environ.get("PNCP_MAX_PAGES_PER_MODALITY", "1500"))
    atraso_pagina = float(os.environ.get("PNCP_PAGE_DELAY", "0.05"))
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    data_final = agora.strftime("%Y%m%d")
    data_inicial = (agora - timedelta(days=dias)).strftime("%Y%m%d")
    modalidades = modalidades_configuradas()
    por_id: dict[str, dict] = {}
    # O PNCP aplica limite de requisições; modalidades seguem em fila para evitar HTTP 429.
    with ThreadPoolExecutor(max_workers=1) as executor:
        tarefas = {
            executor.submit(
                buscar_modalidade,
                modalidade,
                data_inicial,
                data_final,
                uf,
                max_paginas,
                atraso_pagina,
            ): modalidade
            for modalidade in modalidades
        }
        for tarefa in as_completed(tarefas):
            modalidade = tarefas[tarefa]
            try:
                por_id.update(tarefa.result())
            except Exception as exc:
                raise RuntimeError(f"Falha ao consultar a modalidade {modalidade}: {exc}") from exc
    print(f"PNCP: {len(por_id)} contratações únicas carregadas.")
    return list(por_id.values())


def construir_link(item: dict) -> str:
    orgao = item.get("orgaoEntidade") or {}
    cnpj = orgao.get("cnpj")
    ano = item.get("anoCompra")
    sequencial = item.get("sequencialCompra")
    if cnpj and ano and sequencial:
        return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}"
    return "https://pncp.gov.br/app/editais"


def interpretar_data(valor) -> datetime | None:
    if not valor:
        return None
    try:
        texto = str(valor).strip().replace("Z", "+00:00")
        data = datetime.fromisoformat(texto)
        if data.tzinfo is None:
            data = data.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
        return data.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def analisar_janela_disputa(item: dict) -> tuple[bool, str]:
    agora = datetime.now(timezone.utc)
    abertura = interpretar_data(item.get("dataAberturaProposta"))
    encerramento = interpretar_data(item.get("dataEncerramentoProposta"))
    situacao = normalizar(item.get("situacaoCompraNome") or "")
    indisponiveis = (
        "anulad", "revogad", "suspens", "cancelad", "encerrad",
        "homologad", "fracassad", "desert",
    )
    if any(termo in situacao for termo in indisponiveis):
        return False, item.get("situacaoCompraNome") or "Indisponível"
    if not encerramento or encerramento <= agora:
        return False, "Prazo encerrado ou não informado"
    if abertura and abertura > agora:
        return True, f"Abre em {abertura.astimezone(ZoneInfo('America/Sao_Paulo')):%d/%m/%Y %H:%M}"
    return True, f"Aberta até {encerramento.astimezone(ZoneInfo('America/Sao_Paulo')):%d/%m/%Y %H:%M}"


def codigo_modalidade(item: dict) -> int | None:
    valor = item.get("modalidadeId") or item.get("codigoModalidadeContratacao")
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def transformar(item: dict) -> dict | None:
    categoria = categorizar(item)
    identificador = item.get("numeroControlePNCP")
    objeto = item.get("objetoCompra")
    aberta, janela = analisar_janela_disputa(item)
    if not categoria or not identificador or not objeto or not aberta:
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
        "modalidade_codigo": codigo_modalidade(item),
        "modo_disputa": item.get("modoDisputaNome"),
        "instrumento_convocatorio": item.get("tipoInstrumentoConvocatorioNome"),
        "data_abertura_proposta": item.get("dataAberturaProposta"),
        "data_encerramento_proposta": item.get("dataEncerramentoProposta"),
        "situacao_compra": item.get("situacaoCompraNome"),
        "janela_disputa": janela,
        "oportunidade_aberta": True,
        "municipio": unidade.get("municipioNome"),
        "uf": unidade.get("ufSigla") or os.environ.get("PNCP_UF", ""),
        "link": construir_link(item),
        "link_sistema_origem": item.get("linkSistemaOrigem") or item.get("linkProcessoEletronico"),
        "data_publicacao": item.get("dataPublicacaoPncp"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "_cnpj_orgao": orgao.get("cnpj"),
        "_ano_compra": item.get("anoCompra"),
        "_sequencial_compra": item.get("sequencialCompra"),
    }


def buscar_itens_pncp(oportunidade: dict) -> list[dict]:
    cnpj = oportunidade.get("_cnpj_orgao")
    ano = oportunidade.get("_ano_compra")
    sequencial = oportunidade.get("_sequencial_compra")
    if not cnpj or not ano or not sequencial:
        return []
    url = PNCP_ITENS_URL.format(cnpj=cnpj, ano=ano, sequencial=sequencial)
    resposta = sessao_da_thread().get(url, timeout=REQUEST_TIMEOUT)
    if resposta.status_code == 404:
        return []
    resposta.raise_for_status()
    payload = resposta.json()
    if isinstance(payload, list):
        return payload
    return payload.get("data") or payload.get("itens") or []


def transformar_item(oportunidade_id: str, item: dict) -> dict | None:
    numero = item.get("numeroItem")
    descricao = item.get("descricao")
    if numero is None or not descricao:
        return None
    return {
        "oportunidade_id": oportunidade_id,
        "numero_item": int(numero),
        "descricao": descricao,
        "material_ou_servico": item.get("materialOuServico"),
        "quantidade": item.get("quantidade"),
        "unidade_medida": item.get("unidadeMedida"),
        "valor_unitario_estimado": item.get("valorUnitarioEstimado"),
        "valor_total_estimado": item.get("valorTotal"),
        "beneficio_me_epp": item.get("tipoBeneficioNome"),
        "criterio_julgamento": item.get("criterioJulgamentoNome"),
        "situacao": item.get("situacaoCompraItemNome"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def categoria_habilitada(categoria: str, habilitadas: list[str]) -> bool:
    categoria_norm = normalizar(categoria)
    return any(normalizar(valor) in categoria_norm or categoria_norm in normalizar(valor) for valor in habilitadas)


def classificar_mei(oportunidade: dict, itens: list[dict], configuracao: dict) -> None:
    score = 0
    motivos = []
    habilitadas = configuracao.get("categorias_habilitadas") or []
    if categoria_habilitada(oportunidade["categoria"], habilitadas):
        score += 25
        motivos.append("categoria habilitada no perfil")

    beneficios = " ".join(str(item.get("beneficio_me_epp") or "") for item in itens)
    beneficio_norm = normalizar(beneficios)
    exclusiva = "exclusiv" in beneficio_norm or "reservad" in beneficio_norm
    if exclusiva:
        score += 30
        motivos.append("item com benefício para ME/EPP")

    try:
        valor = float(oportunidade.get("valor_estimado") or 0)
        limite = float(configuracao.get("limite_oportunidade") or 80000)
    except (TypeError, ValueError):
        valor, limite = 0, 80000
    if 0 < valor <= limite:
        score += 20
        motivos.append(f"valor até R$ {limite:,.0f}")

    modalidade = oportunidade.get("modalidade_codigo")
    if modalidade in {6, 8}:
        score += 15
        motivos.append("pregão/dispensa")
    elif modalidade in {4, 7, 12}:
        score += 8
        motivos.append("modalidade monitorada")

    disputa = normalizar(oportunidade.get("modo_disputa") or "")
    if "eletronic" in normalizar(oportunidade.get("modalidade") or "") or "aberto" in disputa:
        score += 10
        motivos.append("participação eletrônica/aberta")

    score = min(score, 100)
    if score >= 70:
        classificacao = "Alta aderência"
    elif score >= 45:
        classificacao = "Avaliar edital"
    else:
        classificacao = "Baixa aderência"
    oportunidade["score_mei"] = score
    oportunidade["classificacao_mei"] = classificacao
    oportunidade["motivo_classificacao"] = "; ".join(motivos) or "faltam evidências para classificação"
    oportunidade["exclusiva_me_epp"] = exclusiva


def carregar_configuracao(supabase: Client) -> dict:
    resposta = supabase.table("configuracao_empresa").select("*").eq("id", 1).limit(1).execute()
    return (resposta.data or [{}])[0]


def carregar_status_existentes(supabase: Client) -> dict[str, str]:
    todos = []
    inicio = 0
    while True:
        pagina = supabase.table("oportunidades").select("id,status").range(inicio, inicio + 999).execute().data or []
        todos.extend(pagina)
        if len(pagina) < 1000:
            break
        inicio += 1000
    return {str(item["id"]): item.get("status") or "Em Análise" for item in todos}


def enriquecer_oportunidades(
    oportunidades: list[dict], configuracao: dict
) -> tuple[list[dict], list[dict]]:
    itens_por_oportunidade: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=ITEM_WORKERS) as executor:
        tarefas = {executor.submit(buscar_itens_pncp, oportunidade): oportunidade for oportunidade in oportunidades}
        concluidas = 0
        for tarefa in as_completed(tarefas):
            oportunidade = tarefas[tarefa]
            try:
                brutos = tarefa.result()
            except Exception as exc:
                print(f"Itens {oportunidade['id']}: consulta falhou ({exc}).")
                brutos = []
            itens = [
                transformado
                for bruto in brutos
                if (transformado := transformar_item(oportunidade["id"], bruto))
            ]
            itens_por_oportunidade[oportunidade["id"]] = itens
            concluidas += 1
            if concluidas % 25 == 0 or concluidas == len(oportunidades):
                print(f"PNCP itens: {concluidas}/{len(oportunidades)} oportunidades consultadas.")

    todos_itens = []
    for oportunidade in oportunidades:
        itens = itens_por_oportunidade.get(oportunidade["id"], [])
        if itens:
            texto_itens = " ".join(item["descricao"] for item in itens)
            categoria_itens = categorizar_texto(texto_itens)
            if categoria_itens:
                oportunidade["categoria"] = categoria_itens
        classificar_mei(oportunidade, itens, configuracao)
        todos_itens.extend(itens)
    return oportunidades, todos_itens


def limpar_campos_internos(oportunidade: dict) -> dict:
    return {chave: valor for chave, valor in oportunidade.items() if not chave.startswith("_")}


def salvar_dados(
    supabase: Client,
    oportunidades: list[dict],
    itens: list[dict],
) -> tuple[int, int]:
    agora = datetime.now(timezone.utc).isoformat()
    supabase.table("oportunidades").update(
        {"oportunidade_aberta": False, "janela_disputa": "Prazo encerrado"}
    ).lt("data_encerramento_proposta", agora).execute()
    status_existentes = carregar_status_existentes(supabase)
    registros = []
    for oportunidade in oportunidades:
        oportunidade["status"] = status_existentes.get(oportunidade["id"], "Em Análise")
        registros.append(limpar_campos_internos(oportunidade))

    for inicio in range(0, len(registros), BATCH_SIZE):
        lote = registros[inicio : inicio + BATCH_SIZE]
        supabase.table("oportunidades").upsert(lote, on_conflict="id").execute()
        print(f"Supabase oportunidades: {min(inicio + len(lote), len(registros))}/{len(registros)}.")

    for inicio in range(0, len(itens), BATCH_SIZE):
        lote = itens[inicio : inicio + BATCH_SIZE]
        supabase.table("oportunidade_itens").upsert(
            lote, on_conflict="oportunidade_id,numero_item"
        ).execute()
        print(f"Supabase itens: {min(inicio + len(lote), len(itens))}/{len(itens)}.")
    return len(registros), len(itens)


def main() -> None:
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_KEY", "").strip()
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_KEY precisam estar configuradas nos GitHub Secrets.")

    supabase: Client = create_client(supabase_url, supabase_key)
    configuracao = carregar_configuracao(supabase)
    contratacoes = buscar_contratacoes()
    oportunidades = [oportunidade for item in contratacoes if (oportunidade := transformar(item))]
    por_id = {oportunidade["id"]: oportunidade for oportunidade in oportunidades}
    oportunidades = list(por_id.values())
    print(f"Filtro: {len(oportunidades)} oportunidades compatíveis encontradas.")
    oportunidades, itens = enriquecer_oportunidades(oportunidades, configuracao)
    total_oportunidades, total_itens = salvar_dados(supabase, oportunidades, itens)
    print(
        f"Concluído: {total_oportunidades} oportunidades e {total_itens} itens "
        "sincronizados com o Supabase."
    )


if __name__ == "__main__":
    main()
