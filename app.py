import pandas as pd
import streamlit as st
from supabase import Client, create_client


st.set_page_config(
    page_title="F.R.Y.D.A.Y. - Gestor de Licitações",
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


@st.cache_resource
def init_supabase(url: str, key: str) -> Client:
    return create_client(url, key)


@st.cache_data(ttl=300, show_spinner="Carregando oportunidades...")
def carregar_oportunidades(url: str, key: str) -> list[dict]:
    cliente = create_client(url, key)
    resposta = cliente.table("oportunidades").select("*").execute()
    return resposta.data or []


url = st.secrets.get("SUPABASE_URL", "").strip()
key = st.secrets.get("SUPABASE_KEY", "").strip()
if not url or not key:
    st.error("Configure SUPABASE_URL e SUPABASE_KEY nos Secrets do Streamlit.")
    st.stop()

supabase = init_supabase(url, key)

st.title("🛡️ F.R.Y.D.A.Y. | Painel de Compras Governamentais")
st.caption("Empresa: CELMA NOGUEIRA DE SOUZA SOARES - CNPJ: 58.573.360/0001-62")

try:
    registros = carregar_oportunidades(url, key)
except Exception as exc:
    st.error(f"Falha ao carregar oportunidades do Supabase: {exc}")
    st.stop()

df = pd.DataFrame(registros)
if df.empty:
    st.info("Nenhuma oportunidade encontrada. Aguarde a próxima execução do robô.")
else:
    df["status"] = df["status"].fillna("Em Análise")
    df["valor_estimado"] = pd.to_numeric(
        df["valor_estimado"], errors="coerce"
    ).fillna(0)
    df["data_publicacao"] = pd.to_datetime(
        df.get("data_publicacao"), errors="coerce"
    )
    df = df.sort_values("data_publicacao", ascending=False, na_position="last")

    st.sidebar.header("Filtros")
    busca = st.sidebar.text_input("Buscar no órgão ou objeto")
    categorias = st.sidebar.multiselect(
        "Categoria",
        options=sorted(df["categoria"].dropna().unique()),
    )
    status_selecionados = st.sidebar.multiselect(
        "Status",
        options=STATUS_OPTIONS,
        default=["Em Análise", "Cotando Fornecedor", "Proposta Cadastrada"],
    )

    filtrado = df.copy()
    if busca:
        mascara = (
            filtrado["orgao"].str.contains(busca, case=False, regex=False, na=False)
            | filtrado["objeto"].str.contains(
                busca, case=False, regex=False, na=False
            )
        )
        filtrado = filtrado[mascara]
    if categorias:
        filtrado = filtrado[filtrado["categoria"].isin(categorias)]
    if status_selecionados:
        filtrado = filtrado[filtrado["status"].isin(status_selecionados)]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Oportunidades filtradas", len(filtrado))
    c2.metric("Em análise", int((filtrado["status"] == "Em Análise").sum()))
    c3.metric(
        "Cotando",
        int((filtrado["status"] == "Cotando Fornecedor").sum()),
    )
    faturado = df.loc[df["status"] == "Vencida", "valor_estimado"].sum()
    c4.metric("Faturado (limite R$ 81 mil)", f"R$ {faturado:,.2f}")

    st.divider()
    st.subheader("Oportunidades em monitoramento")

    if filtrado.empty:
        st.warning("Nenhum registro corresponde aos filtros selecionados.")
    else:
        colunas = [
            "id",
            "orgao",
            "objeto",
            "categoria",
            "valor_estimado",
            "modalidade",
            "uf",
            "data_publicacao",
            "link",
            "status",
        ]
        colunas = [coluna for coluna in colunas if coluna in filtrado.columns]
        original = filtrado.set_index("id")["status"].to_dict()
        editado = st.data_editor(
            filtrado[colunas],
            column_config={
                "link": st.column_config.LinkColumn("Edital no PNCP"),
                "valor_estimado": st.column_config.NumberColumn(
                    "Valor estimado", format="R$ %.2f"
                ),
                "data_publicacao": st.column_config.DatetimeColumn(
                    "Publicação", format="DD/MM/YYYY HH:mm"
                ),
                "status": st.column_config.SelectboxColumn(
                    "Status do processo",
                    options=STATUS_OPTIONS,
                    required=True,
                ),
            },
            disabled=[
                coluna
                for coluna in colunas
                if coluna not in {"status"}
            ],
            hide_index=True,
            use_container_width=True,
        )

        if st.button("Salvar atualizações de status", type="primary"):
            alteracoes = [
                (str(row["id"]), row["status"])
                for _, row in editado.iterrows()
                if original.get(str(row["id"])) != row["status"]
            ]
            if not alteracoes:
                st.info("Nenhuma alteração de status para salvar.")
            else:
                try:
                    for identificador, status in alteracoes:
                        supabase.table("oportunidades").update(
                            {"status": status}
                        ).eq("id", identificador).execute()
                    carregar_oportunidades.clear()
                    st.success(f"{len(alteracoes)} status atualizado(s).")
                    st.rerun()
                except Exception as exc:
                    st.error(
                        "Não foi possível salvar os status. Verifique se o Secret "
                        f"do Streamlit usa uma chave de servidor: {exc}"
                    )

st.divider()
st.subheader("🧮 Calculadora rápida de margem")
col1, col2, col3 = st.columns(3)
custo = col1.number_input("Custo unitário (R$)", value=50.0, min_value=0.0)
frete = col2.number_input("Frete/logística (R$)", value=10.0, min_value=0.0)
margem = col3.number_input("Margem de lucro alvo (%)", value=25.0, min_value=0.0)

preco_venda = (custo + frete) * (1 + (margem / 100))
lucro = preco_venda - (custo + frete)
st.write(
    f"👉 **Preço de lance mínimo sugerido:** `R$ {preco_venda:.2f}` | "
    f"**Lucro por item:** `R$ {lucro:.2f}`"
)

