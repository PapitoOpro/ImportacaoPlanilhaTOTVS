# TOTVS Food — Importação de Produtos

Ferramenta web para converter planilhas de cadastro de clientes no formato oficial de importação do **TOTVS Food 5.0**.

---

## Como usar

1. Acesse a URL do sistema
2. Faça upload da planilha do cliente (`.xls`, `.xlsx`, `.xlsm` ou `.csv`)
3. Informe o número da loja (opcional)
4. Clique em **Processar**
5. Baixe o arquivo `PlanilhaImportaçãoLoja.xlsm` gerado
6. Se houver erros, baixe também o relatório `erros.xlsx`

> Após a conversão, ajuste os dados fiscais via **manutenção em massa** no TOTVS.

---

## O que o sistema faz automaticamente

- Mapeia as colunas da planilha do cliente para o template TOTVS
- Preenche `Preço Compra` e `Preço Venda` com `0,00` quando vazios
- Gera `Código Produto` sequencial para produtos sem código (ordenado por SubGrupo)
- Copia `Nome Produto` para `Texto Fiscal`, `Texto Botão Touch` e `Texto Botao Pocket`
- Converte SIM/NÃO → 1/0
- Formata preços como número decimal (ex: `4,00`)
- Valida campos obrigatórios e detecta duplicatas
- Exporta apenas linhas válidas; linhas com erro vão para relatório separado
- Descarta qualquer coluna da planilha do cliente que não esteja no mapeamento

---

## Dados fiscais fixos (Simples Nacional)

Os campos fiscais abaixo são **sempre gerados com valores padrão**, ignorando o que vier na planilha do cliente. Ajuste via manutenção em massa após a importação.

| Campo | Valor fixo |
| --- | --- |
| COD_NCM | 21069090 |
| CFOP_Venda | 5102 |
| Tributo | I |
| Imposto | 0 |
| CSOSN | 1 |
| CST_CSOSN_Venda | 102 |
| PIS | *(vazio)* |
| COFINS | *(vazio)* |
| CST_PIS | *(vazio)* |
| CST_COFINS | *(vazio)* |
| CSOSN_Entrada | *(vazio)* |
| Cod_CEST | *(vazio)* |

---

## Colunas lidas da planilha do cliente

| Coluna do cliente | Campo TOTVS |
| --- | --- |
| CODIGO PRODUTO VENDA | Código Produto |
| NOME PRODUTO | Nome Produto |
| PRODUTO PESAVEL? | Pesável |
| VENDA FRACIONADA? | Permitir Venda Fracionada |
| UNIDADE DE MEDIDA | Unidade |
| GRUPO | Grupo |
| SUBGRUPO | SubGrupo |
| PRECO DE COMPRA | Preço Compra |
| PRECO DE VENDA | Preço Venda |
| LOCAL DE IMPRESSAO (COZINHA, BAR, ETC) | Local Impressão |
| CODIGO BENEFICIO FISCAL | CodigoBeneficioRBC |
| REDUCAO ICMS (%) | PER_REDUCAO_BC_ICMS |

> Os nomes das colunas são normalizados automaticamente (sem acento, maiúsculo).  
> Colunas não listadas acima são ignoradas mesmo que existam na planilha.

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
