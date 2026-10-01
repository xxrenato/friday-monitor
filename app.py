import importlib
import os
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import Client, create_client

import cotacao as cotacao_mod


# O Streamlit reaproveita módulos entre atualizações; recarregar evita código antigo em memória.
cotacao_mod = importlib.reload(cotacao_mod)
buscar_cotacoes = cotacao_mod.buscar_cotacoes
calcular_resultado = cotacao_mod.calcular_resultado
classificar_custo = cotacao_mod.classificar_custo
consulta_enxuta = cotacao_mod.consulta_enxuta
eh_informatica = cotacao_mod.eh_informatica
links_de_pesquisa = cotacao_mod.links_de_pesquisa


st.set_page_config(
    page_title="F.R.Y.D.A.Y. - Oportunidades para MEI",
    page_icon="🛡️",
    layout="wide",
)

STATUS_OPTIONS = [
    "Em Análise",
    "Cotando Fornecedor",
    "Proposta Cadastrada",
    "Vencida",
    "Descartada",
]

CATEGORIAS_DISPONIVEIS = [
    "Informática",
    "Impressão e suprimentos",
    "Material de Escritório",
    "Elétrica e ferramentas",
    "Móveis",
    "Material de Limpeza",
    "Óptica, telefonia e audiovisual",
    "Eletrodomésticos",
    "Material de Construção",
    "EPI e uniformes",
]


@st.cache_resource
def init_supabase(url: str, key: str) -> Client:
    return create_client(url, key)


def _buscar_todos(cliente: Client, tabela: str, colunas: str = "*", ordenar: str | None = None) -> list[dict]:
    registros = []
    inicio = 0
    while True:
        consulta = cliente.table(tabela).select(colunas)
        if ordenar:
            consulta = consulta.order(ordenar, desc=True)
        pagina = consulta.range(inicio, inicio + 999).execute().data or []
        registros.extend(pagina)
        if len(pagina) < 1000:
            break
        inicio += 1000
    return registros


@st.cache_data(ttl=180, show_spinner="Carregando oportunidades...")
def carregar_oportunidades(url: str, key: str) -> list[dict]:
    return _buscar_todos(create_client(url, key), "oportunidades", ordenar="data_publicacao")


@st.cache_data(ttl=120)
def carregar_itens(url: str, key: str, oportunidade_id: str) -> list[dict]:
    resposta = (
        create_client(url, key)
        .table("oportunidade_itens")
        .select("*")
        .eq("oportunidade_id", oportunidade_id)
        .order("numero_item")
        .execute()
    )
    return resposta.data or []


@st.cache_data(ttl=60)
def carregar_cotacoes(url: str, key: str, item_id: int) -> list[dict]:
    resposta = (
        create_client(url, key)
        .table("cotacoes")
        .select("*")
        .eq("oportunidade_item_id", item_id)
        .order("custo_total_unitario")
        .execute()
    )
    return resposta.data or []


def carregar_configuracao(cliente: Client) -> dict:
    resposta = cliente.table("configuracao_empresa").select("*").eq("id", 1).limit(1).execute()
    return (resposta.data or [{}])[0]


def numero(valor, padrao: float = 0.0) -> float:
    try:
        if valor is None or pd.isna(valor):
            return padrao
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def brl(valor) -> str:
    return f"R$ {numero(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def limpar_caches() -> None:
    carregar_oportunidades.clear()
    carregar_itens.clear()
    carregar_cotacoes.clear()


url = st.secrets.get("SUPABASE_URL", "").strip()
key = st.secrets.get("SUPABASE_KEY", "").strip()
if not url or not key:
    st.error("Configure SUPABASE_URL e SUPABASE_KEY nos Secrets do Streamlit.")
    st.stop()

supabase = init_supabase(url, key)
try:
    configuracao = carregar_configuracao(supabase)
    registros = carregar_oportunidades(url, key)
except Exception as exc:
    st.error(f"Não foi possível carregar os dados: {exc}")
    st.stop()

empresa = configuracao.get("razao_social") or "Seu MEI"
cnpj = configuracao.get("cnpj") or "CNPJ ainda não confirmado"

st.title("🛡️ F.R.Y.D.A.Y. | Oportunidades e Cotação")
st.caption(f"Perfil: {empresa} · {cnpj}")
st.info(
    "A aderência ao MEI é uma triagem automática. Antes de disputar, confirme no edital "
    "o CNAE exigido, a regularidade fiscal, o SICAF e as condições de entrega."
)

df = pd.DataFrame(registros)
if not df.empty:
    for coluna, padrao in {
        "status": "Em Análise",
        "classificacao_mei": "Ainda não classificada",
        "modalidade": "Não informada",
        "categoria": "Não informada",
    }.items():
        if coluna not in df:
            df[coluna] = padrao
        df[coluna] = df[coluna].fillna(padrao)
    df["valor_estimado"] = pd.to_numeric(df.get("valor_estimado"), errors="coerce").fillna(0)
    df["score_mei"] = pd.to_numeric(df.get("score_mei"), errors="coerce").fillna(0)
    df["data_publicacao"] = pd.to_datetime(df.get("data_publicacao"), errors="coerce")
    df["data_encerramento_proposta"] = pd.to_datetime(
        df.get("data_encerramento_proposta"), errors="coerce", utc=True
    )
    df["data_abertura_proposta"] = pd.to_datetime(
        df.get("data_abertura_proposta"), errors="coerce", utc=True
    )
    agora_utc = pd.Timestamp.now(tz="UTC")
    limite_semana = agora_utc + pd.Timedelta(days=7)
    situacao = df.get("situacao_compra", pd.Series("", index=df.index)).fillna("").astype(str)
    encerradas = situacao.str.contains(
        "anulad|revogad|suspens|cancelad|encerrad|homologad|fracassad|desert",
        case=False,
        regex=True,
    )
    df_monitoradas = df[
        df["data_encerramento_proposta"].notna()
        & (df["data_encerramento_proposta"] >= agora_utc)
        & ~encerradas
    ].copy()

    abertura = df_monitoradas["data_abertura_proposta"]
    aberta_agora = abertura.isna() | (abertura <= agora_utc)
    abre_na_semana = (abertura > agora_utc) & (abertura <= limite_semana)
    df_monitoradas["alerta_prazo"] = "🔴 Cadastrada para depois"
    df_monitoradas.loc[abre_na_semana, "alerta_prazo"] = "🟡 Abre nesta semana"
    df_monitoradas.loc[aberta_agora, "alerta_prazo"] = "🟢 Aberta agora"
    df_monitoradas["ordem_alerta"] = df_monitoradas["alerta_prazo"].map(
        {"🟢 Aberta agora": 0, "🟡 Abre nesta semana": 1, "🔴 Cadastrada para depois": 2}
    ).fillna(3)
else:
    df_monitoradas = df.copy()

rotulos_abas = ["🎯 Oportunidades", "🛒 Itens, cotações e margem", "🏢 Perfil do MEI"]
aba_ativa = st.radio(
    "Navegação principal",
    rotulos_abas,
    horizontal=True,
    key="aba_ativa",
    label_visibility="collapsed",
)

if aba_ativa == rotulos_abas[0]:
    if df.empty:
        st.info("Nenhuma oportunidade encontrada. Aguarde a próxima execução do robô.")
    else:
        st.caption(
            "🟢 recebendo propostas agora · 🟡 abre nos próximos 7 dias · "
            "🔴 cadastrada, com abertura mais adiante"
        )
        f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
        busca = f1.text_input("Buscar no órgão ou objeto", key="busca_oportunidades")
        categorias = f2.multiselect(
            "Categoria", sorted(df["categoria"].dropna().unique()), key="filtro_categoria"
        )
        modalidades = f3.multiselect(
            "Modalidade", sorted(df["modalidade"].dropna().unique()), key="filtro_modalidade"
        )
        classificacoes = f4.multiselect(
            "Aderência ao MEI",
            ["Alta aderência", "Avaliar edital", "Baixa aderência", "Ainda não classificada"],
            default=["Alta aderência", "Avaliar edital", "Baixa aderência"],
            key="filtro_aderencia",
        )
        s1, s2 = st.columns(2)
        status_selecionados = s1.multiselect(
            "Status interno", STATUS_OPTIONS,
            default=["Em Análise", "Cotando Fornecedor", "Proposta Cadastrada"],
        )
        alertas_selecionados = s2.multiselect(
            "Alerta de prazo",
            ["🟢 Aberta agora", "🟡 Abre nesta semana", "🔴 Cadastrada para depois"],
            default=["🟢 Aberta agora", "🟡 Abre nesta semana", "🔴 Cadastrada para depois"],
        )

        filtrado = df_monitoradas.copy()
        if busca:
            mascara = (
                filtrado["orgao"].str.contains(busca, case=False, regex=False, na=False)
                | filtrado["objeto"].str.contains(busca, case=False, regex=False, na=False)
            )
            filtrado = filtrado[mascara]
        if categorias:
            filtrado = filtrado[filtrado["categoria"].isin(categorias)]
        if modalidades:
            filtrado = filtrado[filtrado["modalidade"].isin(modalidades)]
        if classificacoes:
            filtrado = filtrado[filtrado["classificacao_mei"].isin(classificacoes)]
        if status_selecionados:
            filtrado = filtrado[filtrado["status"].isin(status_selecionados)]
        if alertas_selecionados:
            filtrado = filtrado[filtrado["alerta_prazo"].isin(alertas_selecionados)]
        filtrado = filtrado.sort_values(
            ["ordem_alerta", "data_abertura_proposta", "score_mei", "data_encerramento_proposta"],
            ascending=[True, True, False, True],
            na_position="last",
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Oportunidades filtradas", len(filtrado))
        m2.metric("🟢 Abertas agora", int((filtrado["alerta_prazo"] == "🟢 Aberta agora").sum()))
        m3.metric("🟡 Abrem na semana", int((filtrado["alerta_prazo"] == "🟡 Abre nesta semana").sum()))
        m4.metric("🔴 Cadastradas", int((filtrado["alerta_prazo"] == "🔴 Cadastrada para depois").sum()))
        st.caption(
            f"Pregões: {int(filtrado['modalidade'].str.contains('Pregão', case=False, na=False).sum())} · "
            f"Dispensas: {int(filtrado['modalidade'].str.contains('Dispensa', case=False, na=False).sum())} · "
            "abrangência: Brasil inteiro"
        )

        if filtrado.empty:
            if df_monitoradas.empty:
                st.warning(
                    "A busca semanal está sendo atualizada para o Brasil inteiro. Processos encerrados "
                    "ou sem prazo válido não são exibidos."
                )
            else:
                st.warning("Nenhum registro corresponde aos filtros selecionados.")
        else:
            mapa_atalho = filtrado.set_index(filtrado["id"].astype(str)).to_dict("index")
            oportunidade_atalho = st.selectbox(
                "Escolha uma oportunidade para abrir os itens e as cotações",
                list(mapa_atalho),
                format_func=lambda identificador: (
                    f"{mapa_atalho[identificador].get('alerta_prazo', '')} · "
                    f"{mapa_atalho[identificador].get('modalidade', '')} · "
                    f"{str(mapa_atalho[identificador].get('objeto') or '')[:115]}"
                ),
                key="atalho_oportunidade",
            )
            def abrir_cotacoes(identificador: str) -> None:
                """Mantém a oportunidade escolhida e troca a navegação de forma nativa."""
                st.session_state["oportunidade_cotacao"] = identificador
                st.session_state["aba_ativa"] = rotulos_abas[1]

            st.button(
                "🛒 Abrir itens, cotações e margem",
                type="primary",
                on_click=abrir_cotacoes,
                args=(oportunidade_atalho,),
            )

            colunas = [
                "alerta_prazo", "classificacao_mei", "score_mei", "modalidade", "janela_disputa",
                "data_abertura_proposta", "data_encerramento_proposta", "orgao", "objeto", "categoria",
                "valor_estimado_br", "municipio", "uf", "link", "link_sistema_origem",
                "status", "id",
            ]
            filtrado = filtrado.copy()
            filtrado["valor_estimado_br"] = filtrado["valor_estimado"].map(brl)
            colunas = [coluna for coluna in colunas if coluna in filtrado.columns]
            original = filtrado.set_index("id")["status"].to_dict()
            editado = st.data_editor(
                filtrado[colunas],
                column_config={
                    "id": st.column_config.TextColumn("Identificação PNCP", width="medium"),
                    "alerta_prazo": st.column_config.TextColumn("Alerta", width="medium"),
                    "classificacao_mei": st.column_config.TextColumn("Aderência ao MEI", width="small"),
                    "score_mei": st.column_config.ProgressColumn(
                        "Pontuação", min_value=0, max_value=100, width="small"
                    ),
                    "modalidade": st.column_config.TextColumn("Modalidade", width="small"),
                    "janela_disputa": st.column_config.TextColumn("Disponibilidade", width="medium"),
                    "orgao": st.column_config.TextColumn("Órgão", width="large"),
                    "objeto": st.column_config.TextColumn("Objeto", width="large"),
                    "categoria": st.column_config.TextColumn("Categoria", width="medium"),
                    "valor_estimado_br": st.column_config.TextColumn("Valor estimado", width="medium"),
                    "municipio": st.column_config.TextColumn("Município", width="medium"),
                    "data_abertura_proposta": st.column_config.DatetimeColumn(
                        "Início das propostas", format="DD/MM/YYYY HH:mm", width="medium"
                    ),
                    "data_encerramento_proposta": st.column_config.DatetimeColumn(
                        "Fim das propostas", format="DD/MM/YYYY HH:mm", width="medium"
                    ),
                    "link": st.column_config.LinkColumn(
                        "Edital no PNCP", display_text="Abrir edital", width="small"
                    ),
                    "link_sistema_origem": st.column_config.LinkColumn(
                        "Sistema da disputa", display_text="Ir para disputa", width="small"
                    ),
                    "status": st.column_config.SelectboxColumn(
                        "Status interno", options=STATUS_OPTIONS, required=True, width="medium"
                    ),
                },
                disabled=[coluna for coluna in colunas if coluna != "status"],
                hide_index=True,
                use_container_width=True,
                height=520,
                row_height=58,
            )
            if st.button("Salvar status", type="primary"):
                alteracoes = [
                    (str(linha["id"]), linha["status"])
                    for _, linha in editado.iterrows()
                    if original.get(str(linha["id"])) != linha["status"]
                ]
                if not alteracoes:
                    st.info("Nenhuma alteração de status para salvar.")
                else:
                    try:
                        for identificador, status in alteracoes:
                            supabase.table("oportunidades").update(
                                {"status": status, "updated_at": datetime.utcnow().isoformat()}
                            ).eq("id", identificador).execute()
                        limpar_caches()
                        st.success(f"{len(alteracoes)} status atualizado(s).")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Não foi possível salvar os status: {exc}")

if aba_ativa == rotulos_abas[1]:
    if df_monitoradas.empty:
        st.info("As cotações ficarão disponíveis quando a atualização das oportunidades abertas terminar.")
    else:
        opcoes = df_monitoradas["id"].astype(str).tolist()
        mapa = df_monitoradas.set_index(df_monitoradas["id"].astype(str)).to_dict("index")

        def rotulo_oportunidade(identificador: str) -> str:
            oportunidade = mapa.get(identificador, {})
            objeto = str(oportunidade.get("objeto") or "")
            return (
                f"{oportunidade.get('classificacao_mei', '')} · "
                f"{oportunidade.get('modalidade', '')} · {objeto[:105]}"
            )

        oportunidade_id = st.selectbox(
            "Escolha a oportunidade",
            opcoes,
            format_func=rotulo_oportunidade,
            key="oportunidade_cotacao",
        )
        oportunidade = mapa[oportunidade_id]
        topo1, topo2, topo3 = st.columns([2, 1, 1])
        topo1.markdown(f"**Órgão:** {oportunidade.get('orgao', '')}")
        topo2.metric("Valor estimado", brl(oportunidade.get("valor_estimado")))
        topo3.metric("Aderência", oportunidade.get("classificacao_mei", "—"))
        l1, l2 = st.columns(2)
        if oportunidade.get("link"):
            l1.link_button("Abrir edital no PNCP", oportunidade["link"], use_container_width=True)
        if oportunidade.get("link_sistema_origem"):
            l2.link_button(
                "Abrir sistema da disputa", oportunidade["link_sistema_origem"], use_container_width=True
            )
        st.caption(f"Critérios da classificação: {oportunidade.get('motivo_classificacao') or 'aguardando análise'}")

        try:
            itens = carregar_itens(url, key, oportunidade_id)
        except Exception as exc:
            st.error(f"Não foi possível carregar os itens: {exc}")
            itens = []

        if not itens:
            st.warning(
                "Os itens desta oportunidade ainda não foram importados. O robô fará o detalhamento "
                "na próxima execução."
            )
        else:
            item_por_id = {int(item["id"]): item for item in itens}
            item_id = st.selectbox(
                "Item para cotar",
                list(item_por_id),
                format_func=lambda codigo: (
                    f"Item {item_por_id[codigo]['numero_item']} · "
                    f"{item_por_id[codigo]['descricao'][:135]}"
                ),
            )
            item = item_por_id[item_id]
            i1, i2, i3, i4 = st.columns(4)
            i1.metric("Quantidade", f"{numero(item.get('quantidade')):g}")
            i2.metric("Unidade", item.get("unidade_medida") or "—")
            i3.metric("Estimativa unitária", brl(item.get("valor_unitario_estimado")))
            i4.metric("Benefício ME/EPP", item.get("beneficio_me_epp") or "Não informado")
            st.write(item.get("descricao"))

            consulta_padrao = item.get("consulta_cotacao") or consulta_enxuta(item.get("descricao") or "")
            consulta = st.text_input(
                "Termo de pesquisa (ajuste marca, modelo e especificação antes de cotar)",
                value=consulta_padrao,
                key=f"consulta_{item_id}",
            )
            if st.button("Pesquisar e gravar cotações", type="primary", key=f"pesquisar_{item_id}"):
                serper_key = st.secrets.get("SERPER_API_KEY", os.environ.get("SERPER_API_KEY", ""))
                with st.spinner("Comparando fornecedores em paralelo..."):
                    novas, avisos = buscar_cotacoes(
                        consulta,
                        oportunidade.get("categoria") or "",
                        serper_key,
                    )
                    try:
                        supabase.table("oportunidade_itens").update(
                            {"consulta_cotacao": consulta, "updated_at": datetime.utcnow().isoformat()}
                        ).eq("id", item_id).execute()
                        if novas:
                            registros_cotacao = [
                                {**cotacao, "oportunidade_item_id": item_id} for cotacao in novas
                            ]
                            supabase.table("cotacoes").upsert(
                                registros_cotacao,
                                on_conflict="oportunidade_item_id,fonte,fornecedor,url",
                            ).execute()
                        carregar_itens.clear()
                        carregar_cotacoes.clear()
                        if novas:
                            st.success(f"{len(novas)} cotação(ões) automática(s) gravada(s).")
                        else:
                            st.warning("Nenhum preço automático foi encontrado para esse termo.")
                        for aviso in avisos:
                            st.caption(aviso)
                    except Exception as exc:
                        st.error(f"Não foi possível gravar as cotações: {exc}")

            informatica = eh_informatica(oportunidade.get("categoria") or "", item.get("descricao") or "")
            links = links_de_pesquisa(consulta, incluir_informatica=informatica)
            with st.expander("Abrir pesquisa nas lojas confiáveis", expanded=False):
                st.caption(
                    "Estes botões abrem a busca na loja. Confira modelo, estoque, frete, prazo, "
                    "nota fiscal e reputação do vendedor antes de registrar a cotação."
                )
                st.caption(
                    "Na Shopee e no AliExpress, confirme também a reputação do vendedor, emissão de "
                    "nota fiscal brasileira, impostos de importação e prazo real de entrega."
                )
                colunas_lojas = st.columns(4)
                for indice, (nome, link) in enumerate(links.items()):
                    colunas_lojas[indice % 4].link_button(nome, link, use_container_width=True)

            try:
                cotacoes = carregar_cotacoes(url, key, item_id)
            except Exception as exc:
                st.error(f"Não foi possível carregar as cotações: {exc}")
                cotacoes = []

            st.subheader("Cotações encontradas")
            if cotacoes:
                df_cotacoes = pd.DataFrame(cotacoes)
                df_cotacoes["custo_total_unitario"] = pd.to_numeric(
                    df_cotacoes["custo_total_unitario"], errors="coerce"
                ).fillna(0)
                df_cotacoes = df_cotacoes.sort_values("custo_total_unitario")
                estimativa_item = numero(item.get("valor_unitario_estimado"))
                df_cotacoes["indicador_preco"] = df_cotacoes["custo_total_unitario"].map(
                    lambda custo: classificar_custo(custo, estimativa_item)
                )
                df_cotacoes["preco_br"] = df_cotacoes["preco_unitario"].map(brl)
                df_cotacoes["frete_br"] = df_cotacoes["frete"].map(brl)
                df_cotacoes["custo_br"] = df_cotacoes["custo_total_unitario"].map(brl)
                df_cotacoes["diferenca_br"] = (
                    estimativa_item - df_cotacoes["custo_total_unitario"]
                ).map(brl)
                opcoes_indicador = [
                    "🟢 R$ 5,00 ou mais abaixo",
                    "🟡 Até R$ 5,00 da estimativa",
                    "🔴 Acima da estimativa",
                    "⚪ Sem estimativa unitária",
                ]
                indicadores = st.multiselect(
                    "Filtrar cotações pela comparação com a estimativa unitária",
                    opcoes_indicador,
                    default=opcoes_indicador,
                    key=f"filtro_preco_{item_id}",
                )
                df_cotacoes_exibidas = df_cotacoes[
                    df_cotacoes["indicador_preco"].isin(indicadores)
                ].copy()
                st.caption(
                    "🟢 custo completo pelo menos R$ 5,00 abaixo · "
                    "🟡 entre a estimativa e R$ 5,00 abaixo · 🔴 acima da estimativa"
                )
                st.dataframe(
                    df_cotacoes_exibidas[
                        [
                            "indicador_preco", "fonte", "fornecedor", "produto", "preco_br", "frete_br",
                            "custo_br", "diferenca_br", "entrega", "retirada_local", "localidade",
                            "prazo", "url", "consultado_em",
                        ]
                    ],
                    column_config={
                        "indicador_preco": st.column_config.TextColumn("Comparação", width="medium"),
                        "preco_br": "Preço",
                        "frete_br": "Frete unitário",
                        "custo_br": "Custo unitário completo",
                        "diferenca_br": "Diferença para a estimativa",
                        "url": st.column_config.LinkColumn("Oferta", display_text="Abrir"),
                        "consultado_em": st.column_config.DatetimeColumn("Consultado em", format="DD/MM/YYYY HH:mm"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
                ids_exibidos = {int(codigo) for codigo in df_cotacoes_exibidas["id"].tolist()}
                cotacao_por_id = {
                    int(c["id"]): c for c in cotacoes if int(c["id"]) in ids_exibidos
                }
                if cotacao_por_id:
                    cotacao_id = st.selectbox(
                        "Cotação usada no cálculo",
                        list(cotacao_por_id),
                        format_func=lambda codigo: (
                            f"{classificar_custo(cotacao_por_id[codigo]['custo_total_unitario'], estimativa_item)} · "
                            f"{cotacao_por_id[codigo]['fornecedor']} · "
                            f"{brl(cotacao_por_id[codigo]['custo_total_unitario'])} por unidade"
                        ),
                    )
                    cotacao_escolhida = cotacao_por_id[cotacao_id]
                    custo_padrao = numero(cotacao_escolhida.get("custo_total_unitario"))
                    logistica = []
                    if cotacao_escolhida.get("entrega"):
                        logistica.append("entrega disponível")
                    if cotacao_escolhida.get("retirada_local"):
                        logistica.append("retirada no local")
                    st.success(
                        f"{classificar_custo(custo_padrao, estimativa_item)} · "
                        f"Referência selecionada: {cotacao_escolhida['fornecedor']} · "
                        f"{brl(custo_padrao)} por unidade"
                        + (f" · {', '.join(logistica)}" if logistica else "")
                    )
                else:
                    custo_padrao = 0.0
                    st.warning("Nenhuma cotação corresponde ao filtro de preço selecionado.")
            else:
                custo_padrao = 0.0
                st.info("Ainda não há cotações gravadas para este item.")

            with st.expander("Registrar uma cotação manual", expanded=not bool(cotacoes)):
                with st.form(f"cotacao_manual_{item_id}", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    fornecedor = c1.text_input("Fornecedor")
                    fonte = c2.text_input("Fonte/loja", value="Pesquisa manual")
                    produto = st.text_input("Produto/modelo cotado", value=consulta)
                    url_oferta = st.text_input("Link da oferta")
                    p1, p2, p3 = st.columns(3)
                    preco = p1.number_input("Preço unitário", min_value=0.0, step=1.0)
                    frete = p2.number_input("Frete por unidade", min_value=0.0, step=1.0)
                    prazo = p3.text_input("Prazo de entrega")
                    e1, e2 = st.columns(2)
                    entrega = e1.checkbox("Entrega disponível")
                    retirada = e2.checkbox("Retirada no local")
                    localidade = st.text_input("Endereço/local de retirada")
                    salvar_manual = st.form_submit_button("Salvar cotação manual")
                    if salvar_manual:
                        if not fornecedor or not produto or not url_oferta or preco <= 0:
                            st.error("Informe fornecedor, produto, link e um preço maior que zero.")
                        else:
                            try:
                                supabase.table("cotacoes").upsert(
                                    {
                                        "oportunidade_item_id": item_id,
                                        "fonte": fonte or "Pesquisa manual",
                                        "fornecedor": fornecedor,
                                        "produto": produto,
                                        "url": url_oferta,
                                        "preco_unitario": preco,
                                        "frete": frete,
                                        "entrega": entrega,
                                        "retirada_local": retirada,
                                        "localidade": localidade or None,
                                        "prazo": prazo or None,
                                        "automatica": False,
                                        "consultado_em": datetime.utcnow().isoformat(),
                                    },
                                    on_conflict="oportunidade_item_id,fonte,fornecedor,url",
                                ).execute()
                                carregar_cotacoes.clear()
                                st.success("Cotação manual salva.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Não foi possível salvar a cotação: {exc}")

            st.subheader("Calculadora de margem deste item")
            calc1, calc2, calc3 = st.columns(3)
            quantidade = calc1.number_input(
                "Quantidade", min_value=0.0, value=numero(item.get("quantidade")), step=1.0
            )
            venda_unitaria = calc2.number_input(
                "Valor unitário previsto no edital",
                min_value=0.0,
                value=numero(item.get("valor_unitario_estimado")),
                step=1.0,
            )
            custo_unitario = calc3.number_input(
                "Custo unitário completo", min_value=0.0, value=custo_padrao, step=1.0
            )
            tributos = numero(configuracao.get("custos_tributarios"))
            risco = numero(configuracao.get("reserva_risco"), 5)
            margem_alvo = numero(configuracao.get("margem_alvo"), 20)
            resultado = calcular_resultado(venda_unitaria, custo_unitario, quantidade, tributos, risco)
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Receita prevista", brl(resultado["receita"]))
            r2.metric("Custo dos produtos", brl(resultado["custo_produtos"]))
            r3.metric("Lucro após reservas", brl(resultado["lucro"]))
            r4.metric("Margem estimada", f"{resultado['margem']:.1f}%")
            divisor = 1 - (margem_alvo + tributos + risco) / 100
            if divisor > 0 and custo_unitario > 0:
                preco_alvo = custo_unitario / divisor
                st.write(
                    f"Para preservar margem líquida de **{margem_alvo:.1f}%**, com "
                    f"**{tributos:.1f}%** de tributos/custos e **{risco:.1f}%** de risco, "
                    f"o lance unitário de referência é **{brl(preco_alvo)}**."
                )
                if venda_unitaria and preco_alvo > venda_unitaria:
                    st.warning("Com essa cotação, o preço de referência supera a estimativa do órgão.")

if aba_ativa == rotulos_abas[2]:
    st.subheader("Perfil usado na triagem automática")
    st.caption(
        "Preencha exatamente como consta no CCMEI. O robô usa as categorias e o limite abaixo "
        "para priorizar oportunidades; ele não substitui a conferência jurídica do edital."
    )
    with st.form("perfil_mei"):
        p1, p2 = st.columns(2)
        cnpj_novo = p1.text_input("CNPJ", value=configuracao.get("cnpj") or "")
        razao_nova = p2.text_input("Razão social/nome", value=configuracao.get("razao_social") or "")
        e1, e2, e3 = st.columns(3)
        cep_novo = e1.text_input("CEP de origem", value=configuracao.get("cep") or "")
        municipio_novo = e2.text_input("Município", value=configuracao.get("municipio") or "")
        uf_nova = e3.text_input("UF", value=configuracao.get("uf") or "RJ", max_chars=2)
        atividades = st.text_area(
            "CNAEs/atividades do CCMEI (um por linha)",
            value="\n".join(configuracao.get("atividades_cnae") or []),
        )
        categorias_novas = st.multiselect(
            "Categorias que o MEI está apto a fornecer",
            CATEGORIAS_DISPONIVEIS,
            default=[
                categoria
                for categoria in (configuracao.get("categorias_habilitadas") or [])
                if categoria in CATEGORIAS_DISPONIVEIS
            ],
        )
        n1, n2, n3, n4 = st.columns(4)
        limite_novo = n1.number_input(
            "Limite para priorização", min_value=0.0,
            value=numero(configuracao.get("limite_oportunidade"), 80000), step=1000.0
        )
        margem_nova = n2.number_input(
            "Margem alvo (%)", min_value=0.0, max_value=100.0,
            value=numero(configuracao.get("margem_alvo"), 20)
        )
        risco_novo = n3.number_input(
            "Reserva de risco (%)", min_value=0.0, max_value=100.0,
            value=numero(configuracao.get("reserva_risco"), 5)
        )
        tributos_novos = n4.number_input(
            "Tributos/outros custos (%)", min_value=0.0, max_value=100.0,
            value=numero(configuracao.get("custos_tributarios"), 0)
        )
        salvar_perfil = st.form_submit_button("Salvar perfil", type="primary")
        if salvar_perfil:
            try:
                supabase.table("configuracao_empresa").upsert(
                    {
                        "id": 1,
                        "cnpj": cnpj_novo.strip() or None,
                        "razao_social": razao_nova.strip() or None,
                        "cep": cep_novo.strip() or None,
                        "municipio": municipio_novo.strip() or None,
                        "uf": uf_nova.strip().upper() or "RJ",
                        "atividades_cnae": [linha.strip() for linha in atividades.splitlines() if linha.strip()],
                        "categorias_habilitadas": categorias_novas,
                        "limite_oportunidade": limite_novo,
                        "margem_alvo": margem_nova,
                        "reserva_risco": risco_novo,
                        "custos_tributarios": tributos_novos,
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    on_conflict="id",
                ).execute()
                limpar_caches()
                st.success("Perfil salvo. A próxima execução do robô recalculará a aderência.")
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível salvar o perfil: {exc}")

    st.markdown(
        "**Antes de participar:** confira atividade compatível no CCMEI, certidões, SICAF, "
        "exigência de marca/modelo, prazo, local de entrega, quantidade, garantia e emissão de nota fiscal."
    )
    st.link_button(
        "Orientações oficiais para o MEI vender ao governo",
        "https://www.gov.br/empresas-e-negocios/pt-br/empreendedor/licitacoes-publicas/",
    )

