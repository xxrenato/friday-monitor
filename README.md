# F.R.Y.D.A.Y. - monitor de oportunidades p�blicas para MEI

Aplica��o Streamlit que consulta o PNCP, classifica oportunidades para triagem de um MEI,
detalha os itens e apoia a cota��o de fornecedores e o c�lculo de margem.

## O que o rob� monitora

- concorr�ncia eletr�nica (c�digo 4);
- preg�o eletr�nico (6);
- preg�o presencial (7);
- dispensa de licita��o (8);
- credenciamento (12).

Por padr�o, a busca cobre o Rio de Janeiro e os �ltimos 15 dias. Esses valores podem ser
alterados pelas vari�veis `PNCP_UF`, `PNCP_MODALIDADES` e `PNCP_LOOKBACK_DAYS` no workflow.

## Classifica��o para MEI

A pontua��o usa categoria habilitada no perfil, benef�cio informado pelo PNCP para ME/EPP,
valor estimado, modalidade e forma de disputa. � somente uma triagem: a aptid�o final depende
do objeto e das regras do edital, das atividades/CNAEs do CCMEI, da regularidade fiscal, do
SICAF e das condi��es log�sticas.

## Cota��es

- Para inform�tica, o painel consulta automaticamente as ofertas publicadas no BoaDica.
- Para outras lojas, o painel fornece buscas diretas em grandes varejistas e permite registrar
  pre�o, frete, entrega ou retirada, prazo e localidade.
- Uma pesquisa ampla autom�tica opcional pode ser habilitada adicionando `SERPER_API_KEY`
  aos Secrets do Streamlit. Os resultados continuam restritos a dom�nios de lojas conhecidas.
- Todo pre�o deve ser reconfirmado no carrinho e com emiss�o de nota fiscal antes da proposta.

## Secrets necess�rios

No GitHub Actions e no Streamlit Community Cloud:

```toml
SUPABASE_URL = "https://seu-projeto.supabase.co"
SUPABASE_KEY = "chave-de-servidor"
```

Nunca coloque a chave de servidor no c�digo, em aplicativo Android/Windows distribu�do ou em
reposit�rio p�blico. O painel atual usa essa chave apenas no servidor do Streamlit.

## Execu��o

O GitHub Actions executa a ingest�o �s 06h e 12h, de segunda a sexta, e tamb�m quando os
arquivos principais mudam. O Streamlit l� os dados persistidos no Supabase.

