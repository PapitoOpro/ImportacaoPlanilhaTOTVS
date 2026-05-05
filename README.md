# TOTVS Food — Importação de Produtos

Ferramenta web para converter planilhas de cadastro de clientes no formato oficial de importação do **TOTVS Food 5.0**.

---

## Como usar

1. Acesse a URL do sistema
2. Faça upload da planilha do cliente (`.xls`, `.xlsx`, `.xlsm` ou `.csv`)
3. Clique em **Processar**
4. Baixe o arquivo `PlanilhaImportaçãoLoja.xlsm` gerado
5. Se houver erros, baixe também o relatório `erros.xlsx`

---

## O que o sistema faz automaticamente

- Mapeia as colunas da planilha do cliente para o template TOTVS
- Preenche `Preço Compra` e `Preço Venda` com `0,00` quando vazios
- Gera `Código Produto` sequencial para produtos sem código
- Copia `Nome Produto` para `Texto Fiscal`, `Texto Botão Touch` e `Texto Botao Pocket`
- Formata NCM (8 dígitos), CEST (7), CFOP (4)
- Converte SIM/NÃO → 1/0
- Formata PIS e COFINS como número decimal (ex: `0,65`)
- Valida campos obrigatórios e detecta duplicatas
- Exporta apenas linhas válidas; linhas com erro vão para relatório separado

---

## Colunas esperadas na planilha do cliente

| Coluna do cliente | Campo TOTVS |
|---|---|
| CODIGO PRODUTO VENDA | Código Produto |
| NOME PRODUTO | Nome Produto |
| PRODUTO PESAVEL? | Pesável |
| VENDA FRACIONADA? | Permitir Venda Fracionada |
| UNIDADE DE MEDIDA | Unidade |
| GRUPO | Grupo |
| SUBGRUPO | SubGrupo |
| PRECO DE COMPRA | Preço Compra |
| PRECO DE VENDA | Preço Venda |
| LOCAL DE IMPRESSAO | Local Impressão |
| NCM | COD_NCM |
| CEST | Cod_CEST |
| TRIBUTO | Tributo |
| IMPOSTO (% ICMS) | Imposto |
| CFOP | CFOP |
| CST OU CSOSN | CST_CSOSN_Venda |
| CST PIS | CST_PIS |
| PIS CALCULO | PIS_Tipo_Calculo |
| ALIQUOTA PIS | PIS |
| CST COFINS | CST_COFINS |
| COFINS CALCULO | COFINS_Tipo_Calculo |
| ALIQUOTA COFINS | COFINS |
| CODIGO BENEFICIO FISCAL | CodigoBeneficioRBC |
| REDUCAO ICMS (%) | PER_REDUCAO_BC_ICMS |
| CODIGO DE BARRAS | Cod GTIN NF-e |

> Os nomes das colunas são normalizados automaticamente (sem acento, maiúsculo).

---

## Instalação local

```bash
pip install -r requirements.txt
uvicorn api:app --reload
```

Acesse: `http://localhost:8000`

## CLI (linha de comando)

```bash
python src/main.py planilha_cliente.xlsx
python src/main.py planilha_cliente.xlsx -o saida.xlsm -e erros.xlsx
```

---

## Estrutura do projeto

Ver [ESTRUTURA.md](ESTRUTURA.md) para documentação técnica completa.
