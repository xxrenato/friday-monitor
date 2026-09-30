# F.R.Y.D.A.Y. — monitor de oportunidades públicas para MEI

Aplicação Streamlit que consulta o PNCP, classifica oportunidades para triagem de um MEI,
detalha os itens e apoia a cotação de fornecedores e o cálculo de margem.

## O que o robô monitora

- concorrência eletrônica (código 4);
- pregão eletrônico (6);
- pregão presencial (7);
- dispensa de licitação (8);
- credenciamento (12).

Por padrão, a busca cobre o Brasil inteiro usando o endpoint oficial de contratações com
recebimento de propostas em aberto. O painel só exibe processos com encerramento futuro e
situação válida: recebendo propostas/lances ou com abertura próxima. O escopo pode ser alterado
pelas variáveis `PNCP_UF` e `PNCP_MODALIDADES` no workflow.

## Classificação para MEI

A pontuação usa categoria habilitada no perfil, benefício informado pelo PNCP para ME/EPP,
valor estimado, modalidade e forma de disputa. É somente uma triagem: a aptidão final depende
do objeto e das regras do edital, das atividades/CNAEs do CCMEI, da regularidade fiscal, do
SICAF e das condições logísticas.

## Cotações

- Para informática, o painel consulta automaticamente as ofertas publicadas no BoaDica.
- Para outras lojas, o painel fornece buscas diretas em grandes varejistas e permite registrar
  preço, frete, entrega ou retirada, prazo e localidade.
- Uma pesquisa ampla automática opcional pode ser habilitada adicionando `SERPER_API_KEY`
  aos Secrets do Streamlit. Os resultados continuam restritos a domínios de lojas conhecidas.
- Todo preço deve ser reconfirmado no carrinho e com emissão de nota fiscal antes da proposta.

## Secrets necessários

No GitHub Actions e no Streamlit Community Cloud:

```toml
SUPABASE_URL = "https://seu-projeto.supabase.co"
SUPABASE_KEY = "chave-de-servidor"
```

Nunca coloque a chave de servidor no código, em aplicativo Android/Windows distribuído ou em
repositório público. O painel atual usa essa chave apenas no servidor do Streamlit.

## Execução

O GitHub Actions executa a ingestão às 06h e 12h, de segunda a sexta, e também quando os
arquivos principais mudam. O Streamlit lê os dados persistidos no Supabase.
