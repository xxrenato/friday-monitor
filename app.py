import os
import streamlit as st
import pandas as pd
from supabase import create_client, Client

st.set_page_config(page_title="F.R.Y.D.A.Y. - Gestor de Licitações", layout="wide")

# Conexão com o Supabase
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

st.title("🛡️ F.R.Y.D.A.Y. | Painel de Compras Governamentais")
st.caption("Empresa: CELMA NOGUEIRA DE SOUZA SOARES - CNPJ: 58.573.360/0001-62")

# Carrega Dados
#res = supabase.table("oportunidades").select("*").execute()
# Carrega Dados com proteção contra erros
# Carrega Dados de forma segura
df = pd.DataFrame()

try:
    res = supabase.table("oportunidades").select("*").execute()
    if res and hasattr(res, 'data') and res.data:
        df = pd.DataFrame(res.data)
except Exception as e:
    st.error(f"⚠️ Falha de conexão/permissão com o Supabase: {e}")

if df.empty:
    st.info("Nenhuma oportunidade cadastrada até o momento. Aguarde a execução do robô de captura.")
else:
    # Indicadores Topo
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Identificado", len(df))
    c2.metric("Em Análise", len(df[df['status'] == 'Em Análise']))
    c3.metric("Cotando", len(df[df['status'] == 'Cotando Fornecedor']))
    
    # Soma de disputas ganhas para controle de teto MEI
    vencidas = df[df['status'] == 'Vencida']['valor_estimado'].sum()
    c4.metric("Faturado (Limite R$ 81k)", f"R$ {vencidas:,.2f}")

    st.divider()

    # Tabela de Edição de Status
    st.subheader("Oportunidades em Monitoramento")
    
    df_editado = st.data_editor(
        df,
        column_config={
            "link": st.column_config.LinkColumn("Link do Edital"),
            "valor_estimado": st.column_config.NumberColumn("Valor Est. (R$)", format="R$ %.2f"),
            "status": st.column_config.SelectboxColumn(
                "Status do Processo",
                options=["Em Análise", "Cotando Fornecedor", "Proposta Cadastrada", "Vencida", "Descartada"],
                required=True
            )
        },
        disabled=["id", "orgao", "objeto", "categoria", "valor_estimado", "modalidade", "uf", "link", "data_publicacao"],
        hide_index=True,
        use_container_width=True
    )

    # Botão para Salvar Alterações de Status no Banco
    if st.button("Salvar Atualizações de Status"):
        for _, row in df_editado.iterrows():
            supabase.table("oportunidades").update({"status": row["status"]}).eq("id", row["id"]).execute()
        st.success("Status atualizados com sucesso!")
        st.rerun()

st.divider()

# Calculadora Integrada
st.subheader("🧮 Calculadora Rápidas de Margem")
col1, col2, col3 = st.columns(3)
custo = col1.number_input("Custo Unitário (Fornecedor R$)", value=50.0)
frete = col2.number_input("Frete/Logística (R$)", value=10.0)
margem = col3.number_input("Margem de Lucro Alvo (%)", value=25.0)

preco_venda = (custo + frete) * (1 + (margem / 100))
lucro = preco_venda - (custo + frete)

st.write(f"👉 **Preço de Lance Mínimo Sugerido:** `R$ {preco_venda:.2f}` | **Lucro por Item:** `R$ {lucro:.2f}`")
