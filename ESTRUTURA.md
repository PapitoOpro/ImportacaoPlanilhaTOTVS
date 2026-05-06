# Estrutura do Projeto — TOTVS Food Importação de Produtos

## Árvore de Arquivos

```text
ImportacaoPlanilhaTOTVS/
├── api.py                          # API FastAPI — endpoints HTTP
├── requirements.txt                # Dependências Python
├── Procfile                        # Configuração de deploy (Render)
├── PlanilhaImportaçãoLojaComValidação.xlsm  # Template oficial TOTVS Food 5.0
├── templates/
│   └── index.html                  # Interface web (upload + download + aviso fiscal)
├── static/                         # Arquivos estáticos (CSS, JS)
└── src/
    ├── config.py                   # Mapeamentos, regras e defaults
    ├── reader.py                   # Leitura de planilhas do cliente
    ├── transformer.py              # Transformação e normalização dos dados
    ├── validator.py                # Validação de campos obrigatórios e formatos
    ├── writer.py                   # Geração do Excel de saída e relatório de erros
    └── main.py                     # CLI (linha de comando)
```

---

## Fluxo de Processamento

```text
Planilha cliente (.xls/.xlsx/.xlsm/.csv)
        │
        ▼
   reader.py          → lê e normaliza cabeçalhos (sem acento, maiúsculo)
        │
        ▼
   transformer.py     → renomeia colunas via COLUMN_MAP, descarta não mapeadas,
                         aplica formatadores, preenche defaults fiscais fixos,
                         gera códigos sequenciais, copia Nome Produto → textos
        │
        ▼
   validator.py       → valida obrigatórios, formatos (dígitos, decimal),
                         detecta duplicatas de código
        │
        ├── linhas válidas ──▶ writer.py → PlanilhaImportaçãoLoja.xlsm
        └── linhas com erro ─▶ writer.py → erros.xlsx
```

---

## src/config.py

Central de configuração. Não contém lógica — só dados.

### `COLUMN_MAP`

Mapeamento `coluna do cliente (normalizada)` → `coluna do template TOTVS`.
Apenas as colunas listadas aqui são lidas da planilha do cliente. Todas as demais são descartadas.

| Chave (cliente) | Valor (template) |
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

### `FIELD_FILL_DEFAULTS`

Valores aplicados quando o campo vier vazio na planilha do cliente.

| Campo | Default |
| --- | --- |
| Unidade | UN |
| Preço Venda | 0,00 |
| Preço Compra | 0,00 |

### `REQUIRED_FIELDS`

Campos obrigatórios: `Código Produto`, `Nome Produto`, `Unidade`, `Preço Venda`.

### `FIELD_RULES`

Regras de validação por coluna:

- `digits` — somente dígitos, comprimento fixo (NCM=8, CEST=7, CFOP=4)
- `currency` — valor monetário (Imposto, PER_REDUCAO_BC_ICMS)
- `decimal` — número decimal com vírgula (Quantidade Estoque, Quantidade Mínima)

### `TEMPLATE_DEFAULTS`

Defaults para colunas do template. Campos fiscais fixos (Simples Nacional):

| Campo | Valor |
| --- | --- |
| COD_NCM | 21069090 |
| CFOP_Venda | 5102 |
| Tributo | I |
| Imposto | 0 |
| CSOSN | 1 |
| CST_CSOSN_Venda | 102 |
| PIS | *(vazio)* |
| COFINS | *(vazio)* |
| CSOSN_Entrada | *(vazio)* |
| Cod_CEST | *(vazio)* |

### `TEMPLATE_COLUMNS`

Lista ordenada com as 94 colunas do template TOTVS Food 5.0 (aba `Produtos`).
Define a ordem exata de saída, espelhando `PlanilhaImportaçãoLojaComValidação.xlsm`.

---

## src/reader.py

```python
read_client_file(path: Path) -> pd.DataFrame
```

- Aceita `.xls` — lê aba `1. Produtos de Venda` com `header=1` (pula linha 0 de títulos de seção)
- Aceita `.xlsx`, `.xlsm` — auto-detecção de cabeçalho:
  - Tenta `header=0`; se nenhuma coluna bater com `COLUMN_MAP`, recarrega com `header=1` (aba `1. Produtos de Venda`)
  - Suporta tanto planilhas simples (1 linha de cabeçalho) quanto o template TOTVS Food 5.0 (2 linhas: títulos de seção + nomes de campo)
- Aceita `.csv` (detecção automática de encoding e separador)
- Normaliza nomes de colunas: remove acentos, uppercase, colapsa espaços e quebras de linha
- Retorna DataFrame com todas as colunas como `str`, sem linhas totalmente vazias

---

## src/transformer.py

```python
transform(df, column_map, template_defaults, template_columns, field_fill_defaults) -> pd.DataFrame
```

**Etapas internas:**

1. Renomeia colunas via `COLUMN_MAP`
2. **Descarta** todas as colunas que não vieram via `COLUMN_MAP` (evita passthrough de dados do cliente)
3. Limpa texto (strip, colapso de espaços)
4. Converte automaticamente colunas SIM/NÃO → 1/0
5. Gera `Código Produto` sequencial para linhas sem código (ordenado por SubGrupo)
6. Aplica `FIELD_FILL_DEFAULTS` (Preço Venda e Preço Compra → `0,00` se vazios)
7. Aplica formatadores:
   - `_to_number` → `4,00` (Preço Venda, Preço Compra — número decimal sem R$)
   - `_to_bool` → SIM/S/X → 1, NÃO/N → 0
8. Copia `Nome Produto` → `Texto Fiscal`, `Texto Botão Touch`, `Texto Botao Pocket` (quando vazios)
9. Preenche todas as colunas do template com `TEMPLATE_DEFAULTS` (inclui valores fiscais fixos)
10. Retorna DataFrame na ordem exata de `TEMPLATE_COLUMNS`

---

## src/validator.py

```python
validate(df, required_fields, field_rules) -> list[ValidationError]
```

**`ValidationError`** (dataclass): `row`, `field`, `value`, `reason`

**Validações:**

- Campos obrigatórios vazios
- Duplicatas de `Código Produto`
- Formato decimal com vírgula (Quantidade Estoque, Quantidade Mínima)

Linhas com erros são **excluídas** do arquivo de saída e listadas em `erros.xlsx`.

---

## src/writer.py

```python
write_output(df, output_path, template_path, numero_loja)  # Gera PlanilhaImportaçãoLoja.xlsm
write_error_report(errors, path)                           # Gera erros.xlsx
```

**Arquivo de saída:**

- Copia o template `.xlsm` (preserva macros e validações)
- Escreve os dados na aba `Produtos` a partir da linha 3
- 94 colunas na ordem exata do template

**Relatório de erros:**

- Cabeçalho vermelho, linhas amarelas
- Colunas: Linha, Campo, Valor Informado, Motivo do Erro

---

## api.py — Endpoints HTTP

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/` | Interface web (index.html) |
| POST | `/processar` | Recebe planilha, processa, retorna JSON com stats, erros e arquivos em base64 |

**Resposta de `/processar`:**

```json
{
  "stats": { "total": 100, "exportados": 98, "rejeitados": 2 },
  "erros": [{ "linha": 5, "campo": "Nome Produto", "valor": "", "motivo": "Campo obrigatório vazio" }],
  "arquivo": "<base64 do xlsm>",
  "arquivo_erros": "<base64 do erros.xlsx ou null>"
}
```

Após conversão bem-sucedida, a interface exibe:
> **LEMBRE-SE** de ajustar os dados fiscais na manutenção em massa, arquivo gerado com regra do simples nacional com NCM 21069090!

---

## src/main.py — CLI

```bash
python src/main.py planilha_cliente.xlsx
python src/main.py planilha_cliente.xlsx -o saida.xlsm -e erros.xlsx
```

Executa o mesmo pipeline da API em modo terminal. Retorna exit code `0` (sem erros) ou `1` (com erros).

---

## Arquivo de Saída — PlanilhaImportaçãoLoja.xlsm

Formato compatível com importação direta no TOTVS Food 5.0.
Aba: `Produtos` | 94 colunas na ordem exata do template oficial.

| Coluna | Formato | Observação |
| --- | --- | --- |
| Código Produto | inteiro (string) | Auto-gerado se vazio |
| Nome Produto | texto | Fonte para Texto Fiscal/Touch/Pocket |
| Preço Venda | `4,00` | Default `0,00` |
| Preço Compra | `4,00` | Default `0,00` |
| COD_NCM | `21069090` | Fixo — Simples Nacional |
| CFOP_Venda | `5102` | Fixo |
| CSOSN | `1` | Fixo |
| CST_CSOSN_Venda | `102` | Fixo |
| Pesável | `0` ou `1` | SIM/NÃO convertido |
| Texto Fiscal | texto | Cópia de Nome Produto |
| AlterarCod_CEST | — | Nova coluna — preencher pós-importação |

---

## Deploy

- **Plataforma:** Render.com
- **Runtime:** Python 3.11 / uvicorn
- **Start command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`
- **Procfile:** `web: uvicorn api:app --host 0.0.0.0 --port $PORT`
