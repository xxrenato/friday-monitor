import html
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import requests


BOADICA_API = "https://boadica.com.br/api"
BOADICA_SITE = "https://boadica.com.br"
TIMEOUT = 20

TERMOS_INFORMATICA = (
    "computador", "notebook", "monitor", "teclado", "mouse", "impressora",
    "toner", "cartucho", "ssd", "hd ", "memoria", "roteador", "switch",
    "servidor", "nobreak", "webcam", "headset", "informatica", "periferico",
    "smartphone", "celular", "telefone movel", "telefonia", "tablet",
)

LOJAS_CONFIAVEIS = {
    "Amazon": "https://www.amazon.com.br/s?k={q}",
    "Mercado Livre": "https://lista.mercadolivre.com.br/{q}",
    "Shopee": "https://shopee.com.br/search?keyword={q}",
    "AliExpress": "https://pt.aliexpress.com/w/wholesale-{q}.html",
    "Magazine Luiza": "https://www.magazineluiza.com.br/busca/{q}/",
    "KaBuM!": "https://www.kabum.com.br/busca/{q}",
    "Pichau": "https://www.pichau.com.br/search?q={q}",
    "Terabyte": "https://www.terabyteshop.com.br/busca?str={q}",
    "Kalunga": "https://www.kalunga.com.br/busca/1?q={q}",
    "Leroy Merlin": "https://www.leroymerlin.com.br/busca?q={q}",
}

DOMINIOS_CONFIAVEIS = (
    "amazon.com.br", "mercadolivre.com.br", "magazineluiza.com.br",
    "shopee.com.br", "aliexpress.com", "pt.aliexpress.com",
    "kabum.com.br", "pichau.com.br", "terabyteshop.com.br",
    "kalunga.com.br", "leroymerlin.com.br", "casasbahia.com.br",
    "carrefour.com.br", "fastshop.com.br", "dell.com", "lenovo.com",
)

# As lojas abaixo são consultadas diretamente. Algumas podem bloquear leitura automatizada;
# nesse caso, o link da pesquisa continua disponível para conferência pelo usuário.
LOJAS_COTACAO_DIRETA = tuple(LOJAS_CONFIAVEIS)


def _normalizar(texto: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto or "")
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", sem_acentos.casefold()).strip()


def eh_informatica(categoria: str, descricao: str) -> bool:
    texto = _normalizar(f"{categoria} {descricao}")
    return any(termo in texto for termo in TERMOS_INFORMATICA)


def consulta_enxuta(descricao: str, limite_palavras: int = 14) -> str:
    texto = re.sub(r"[^\wÀ-ÿ.\-/ ]+", " ", descricao or "")
    palavras = [p for p in texto.split() if len(p) > 1]
    return " ".join(palavras[:limite_palavras]).strip()


def links_de_pesquisa(consulta: str, incluir_informatica: bool = True) -> dict[str, str]:
    q = quote_plus(consulta_enxuta(consulta))
    lojas = dict(LOJAS_CONFIAVEIS)
    if not incluir_informatica:
        for nome in ("KaBuM!", "Pichau", "Terabyte"):
            lojas.pop(nome, None)
    return {nome: url.format(q=q) for nome, url in lojas.items()}


def _tipo_jsonld(valor) -> set[str]:
    tipos = valor.get("@type") if isinstance(valor, dict) else None
    if isinstance(tipos, str):
        return {tipos.casefold()}
    if isinstance(tipos, list):
        return {str(tipo).casefold() for tipo in tipos}
    return set()


def _produtos_jsonld(valor):
    """Percorre JSON-LD e devolve produtos publicados pela própria página da loja."""
    if isinstance(valor, dict):
        if "product" in _tipo_jsonld(valor):
            yield valor
        for filho in valor.values():
            yield from _produtos_jsonld(filho)
    elif isinstance(valor, list):
        for filho in valor:
            yield from _produtos_jsonld(filho)


def _ofertas_jsonld(produto: dict):
    ofertas = produto.get("offers") or produto.get("Offers") or []
    if isinstance(ofertas, dict):
        ofertas = ofertas.get("offers") or ofertas.get("itemListElement") or [ofertas]
    if not isinstance(ofertas, list):
        ofertas = [ofertas]
    return [oferta for oferta in ofertas if isinstance(oferta, dict)]


def buscar_loja_direta(nome_loja: str, url_busca: str, max_ofertas: int = 4) -> list[dict]:
    """Lê ofertas em JSON-LD diretamente da página pública de pesquisa de uma loja."""
    resposta = requests.get(
        url_busca,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
        },
        timeout=12,
    )
    resposta.raise_for_status()
    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        resposta.text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    resultados, vistos = [], set()
    for bloco in blocos:
        try:
            payload = json.loads(html.unescape(bloco).strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for produto in _produtos_jsonld(payload):
            nome_produto = produto.get("name") or produto.get("headline") or nome_loja
            for oferta in _ofertas_jsonld(produto):
                preco = _valor_numerico(
                    _primeiro(oferta, "price", "lowPrice", "highPrice")
                )
                link = _primeiro(oferta, "url", padrao=produto.get("url"))
                link = urljoin(resposta.url, str(link or ""))
                if preco is None or preco <= 0 or not link or not _dominio_confiavel(link):
                    continue
                chave = (nome_loja, link, round(preco, 2))
                if chave in vistos:
                    continue
                vistos.add(chave)
                resultados.append(
                    {
                        "fonte": nome_loja,
                        "fornecedor": nome_loja,
                        "produto": str(nome_produto),
                        "url": link,
                        "preco_unitario": round(preco, 2),
                        "frete": 0,
                        "entrega": True,
                        "retirada_local": None,
                        "localidade": None,
                        "prazo": None,
                        "forma_pagamento": None,
                        "automatica": True,
                        "consultado_em": datetime.now(timezone.utc).isoformat(),
                        "observacao": (
                            "Preço coletado diretamente da página pública da loja; "
                            "confirme frete, estoque e valor no carrinho."
                        ),
                    }
                )
                if len(resultados) >= max_ofertas:
                    return resultados
    return resultados


def buscar_marketplaces(consulta: str, incluir_informatica: bool) -> tuple[list[dict], list[str]]:
    """Consulta em paralelo as páginas públicas das lojas e informa bloqueios sem interromper a busca."""
    links = links_de_pesquisa(consulta, incluir_informatica=incluir_informatica)
    selecionados = {
        nome: link for nome, link in links.items() if nome in LOJAS_COTACAO_DIRETA
    }
    resultados, indisponiveis = [], []
    with ThreadPoolExecutor(max_workers=min(len(selecionados), 8)) as executor:
        tarefas = {
            executor.submit(buscar_loja_direta, nome, link): nome
            for nome, link in selecionados.items()
        }
        for tarefa in as_completed(tarefas):
            nome = tarefas[tarefa]
            try:
                resultados.extend(tarefa.result())
            except Exception:
                indisponiveis.append(nome)
    avisos = []
    if indisponiveis:
        avisos.append(
            "Pesquisa direta bloqueada ou sem preço legível em: "
            + ", ".join(sorted(indisponiveis))
            + ". Use os botões exibidos para conferir essas lojas."
        )
    return resultados, avisos


def classificar_custo(custo_unitario: float, estimativa_unitaria: float, folga: float = 5.0) -> str:
    """Classifica o custo completo em relação à estimativa unitária do edital."""
    custo = max(float(custo_unitario or 0), 0)
    estimativa = max(float(estimativa_unitaria or 0), 0)
    if estimativa <= 0:
        return "⚪ Sem estimativa unitária"
    if custo > estimativa:
        return "🔴 Acima da estimativa"
    if custo <= estimativa - max(folga, 0):
        return "🟢 R$ 5,00 ou mais abaixo"
    return "🟡 Até R$ 5,00 da estimativa"


def _valor_numerico(valor) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)
    if not valor:
        return None
    texto = re.sub(r"[^0-9,.-]", "", str(valor))
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def _todos_dicionarios(valor):
    if isinstance(valor, dict):
        yield valor
        for filho in valor.values():
            yield from _todos_dicionarios(filho)
    elif isinstance(valor, list):
        for filho in valor:
            yield from _todos_dicionarios(filho)


def _primeiro(dados: dict, *chaves, padrao=None):
    for chave in chaves:
        valor = dados.get(chave)
        if valor not in (None, "", []):
            return valor
    return padrao


def _booleano(valor) -> bool | None:
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return None
    return _normalizar(str(valor)) in {"1", "sim", "s", "true", "yes"}


def buscar_boadica(consulta: str, max_ofertas: int = 12) -> list[dict]:
    """Consulta a API pública usada pelo próprio site BoaDica."""
    sessao = requests.Session()
    sessao.headers.update({"Accept": "application/json", "User-Agent": "friday-monitor/2.0"})

    resposta = sessao.get(
        f"{BOADICA_API}/busca/sugestoes",
        params={"termo": consulta_enxuta(consulta, 10)},
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()
    candidatos = [
        d for d in _todos_dicionarios(resposta.json())
        if _primeiro(d, "codProduto", "codigoProduto", "idProduto")
    ]
    if not candidatos:
        return []

    produto_sugerido = candidatos[0]
    codigo = _primeiro(produto_sugerido, "codProduto", "codigoProduto", "idProduto")
    detalhe = sessao.get(f"{BOADICA_API}/produto/{codigo}", timeout=TIMEOUT)
    detalhe.raise_for_status()
    payload = detalhe.json()

    raiz = payload if isinstance(payload, dict) else {}
    nome_produto = _primeiro(
        raiz, "descricao", "nomeProduto", "produto", "nome",
        padrao=_primeiro(produto_sugerido, "descricao", "nomeProduto", "nome", padrao=consulta),
    )
    ofertas = raiz.get("ofertas") or []
    if not ofertas:
        ofertas = [
            d for d in _todos_dicionarios(payload)
            if _primeiro(d, "preco", "valor", "precoVenda") is not None
            and _primeiro(d, "loja", "nomeLoja", "fornecedor") is not None
        ]

    resultados = []
    for oferta in ofertas:
        if not isinstance(oferta, dict):
            continue
        preco = _valor_numerico(_primeiro(oferta, "preco", "valor", "precoVenda"))
        if preco is None or preco <= 0:
            continue
        loja = _primeiro(oferta, "loja", "nomeLoja", "fornecedor", padrao="Loja no BoaDica")
        if isinstance(loja, dict):
            loja = _primeiro(loja, "nome", "razaoSocial", "fantasia", padrao="Loja no BoaDica")
        endereco = " - ".join(
            str(v) for v in (
                _primeiro(oferta, "endereco", "logradouro"),
                _primeiro(oferta, "bairro"),
                _primeiro(oferta, "cidade"),
                _primeiro(oferta, "uf"),
            ) if v
        )
        pagamento = []
        if _booleano(_primeiro(oferta, "pix", "aceitaPix")):
            pagamento.append("Pix")
        if _booleano(_primeiro(oferta, "cartao", "cartaoCredito", "aceitaCartao")):
            pagamento.append("Cartão")
        entrega = _booleano(_primeiro(oferta, "entrega", "fazEntrega"))
        resultados.append(
            {
                "fonte": "BoaDica",
                "fornecedor": str(loja),
                "produto": str(nome_produto),
                "url": f"{BOADICA_SITE}/produtos/p{codigo}",
                "preco_unitario": round(preco, 2),
                "frete": 0,
                "entrega": entrega,
                "retirada_local": bool(endereco),
                "localidade": endereco or None,
                "prazo": None,
                "forma_pagamento": ", ".join(pagamento) or None,
                "automatica": True,
                "consultado_em": datetime.now(timezone.utc).isoformat(),
                "observacao": "Frete deve ser confirmado com a loja antes da proposta.",
            }
        )
    return sorted(resultados, key=lambda item: item["preco_unitario"])[:max_ofertas]


def _dominio_confiavel(url: str) -> bool:
    dominio = urlparse(url).netloc.casefold().removeprefix("www.")
    return any(dominio == d or dominio.endswith(f".{d}") for d in DOMINIOS_CONFIAVEIS)


def buscar_serper(consulta: str, api_key: str, max_ofertas: int = 15) -> list[dict]:
    """Busca opcional no Google Shopping via Serper, limitada a lojas conhecidas."""
    if not api_key:
        return []
    resposta = requests.post(
        "https://google.serper.dev/shopping",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": consulta_enxuta(consulta), "gl": "br", "hl": "pt-br", "num": 30},
        timeout=TIMEOUT,
    )
    resposta.raise_for_status()
    resultados = []
    for oferta in resposta.json().get("shopping") or []:
        url = oferta.get("link") or ""
        preco = _valor_numerico(oferta.get("price"))
        if not url or not _dominio_confiavel(url) or preco is None or preco <= 0:
            continue
        resultados.append(
            {
                "fonte": "Google Shopping",
                "fornecedor": oferta.get("source") or urlparse(url).netloc,
                "produto": oferta.get("title") or consulta,
                "url": url,
                "preco_unitario": round(preco, 2),
                "frete": 0,
                "entrega": True,
                "retirada_local": None,
                "localidade": None,
                "prazo": oferta.get("delivery"),
                "forma_pagamento": None,
                "automatica": True,
                "consultado_em": datetime.now(timezone.utc).isoformat(),
                "observacao": "Preço e frete devem ser reconfirmados no carrinho da loja.",
            }
        )
    return sorted(resultados, key=lambda item: item["preco_unitario"])[:max_ofertas]


def buscar_cotacoes(
    consulta: str,
    categoria: str = "",
    serper_api_key: str = "",
) -> tuple[list[dict], list[str]]:
    informatica = eh_informatica(categoria, consulta)
    provedores = [
        (
            "Lojas e marketplaces",
            lambda: buscar_marketplaces(consulta, incluir_informatica=informatica),
        )
    ]
    if informatica:
        provedores.append(("BoaDica", lambda: buscar_boadica(consulta)))
    if serper_api_key:
        provedores.append(("Lojas confiáveis", lambda: buscar_serper(consulta, serper_api_key)))

    cotacoes, avisos = [], []
    with ThreadPoolExecutor(max_workers=len(provedores)) as executor:
        tarefas = {executor.submit(funcao): nome for nome, funcao in provedores}
        for tarefa in as_completed(tarefas):
            nome = tarefas[tarefa]
            try:
                resultado = tarefa.result()
                if nome == "Lojas e marketplaces":
                    ofertas_diretas, avisos_diretos = resultado
                    cotacoes.extend(ofertas_diretas)
                    avisos.extend(avisos_diretos)
                else:
                    cotacoes.extend(resultado)
            except Exception as exc:
                avisos.append(f"{nome}: consulta indisponível ({exc}).")
    if not serper_api_key:
        avisos.append(
            "A pesquisa complementar de preços não está configurada. "
            "As buscas diretas dos marketplaces continuam disponíveis abaixo."
        )
    cotacoes_unicas = {}
    for cotacao in cotacoes:
        chave = (
            cotacao.get("fonte"), cotacao.get("fornecedor"), cotacao.get("url"),
            round(float(cotacao.get("preco_unitario") or 0), 2),
        )
        cotacoes_unicas[chave] = cotacao
    cotacoes = list(cotacoes_unicas.values())
    cotacoes.sort(key=lambda item: item["preco_unitario"] + item.get("frete", 0))
    return cotacoes, avisos


def calcular_resultado(
    valor_unitario_venda: float,
    custo_unitario: float,
    quantidade: float,
    tributos_pct: float = 0,
    risco_pct: float = 0,
) -> dict:
    receita = max(valor_unitario_venda, 0) * max(quantidade, 0)
    custo_produtos = max(custo_unitario, 0) * max(quantidade, 0)
    tributos = receita * max(tributos_pct, 0) / 100
    reserva = receita * max(risco_pct, 0) / 100
    lucro = receita - custo_produtos - tributos - reserva
    margem = (lucro / receita * 100) if receita else 0
    return {
        "receita": receita,
        "custo_produtos": custo_produtos,
        "tributos": tributos,
        "reserva": reserva,
        "lucro": lucro,
        "margem": margem,
    }

