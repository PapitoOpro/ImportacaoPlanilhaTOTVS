# Estrutura do Projeto — TOTVS Food Importação de Produtos

## Árvore de Arquivos

```
ImportacaoPlanilhaTOTVS/
├── api.py                          # API FastAPI — endpoints HTTP
├── requirements.txt                # Dependências Python
├── Procfile                        # Configuração de deploy (Render)
├── templates/
│   └── index.html                  # Interface web (upload + download)
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

```
Planilha cliente (.xls/.xlsx/.xlsm/.csv)
        │
        ▼
   reader.py          → lê e normaliza cabeçalhos (sem acento, maiúsculo)
        │
        ▼
   transformer.py     → renomeia colunas, aplica formatadores, preenche defaults,
                         gera códigos sequenciais, copia Nome Produto → textos
        │
        ▼
   validator.py       → valida obrigatórios, formatos (dígitos, moeda, decimal),
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

| Chave (cliente) | Valor (template) |
|---|---|
| CODIGO PRODUTO VENDA | Código Produto |
| NOME PRODUTO | Nome Produto |
| PRECO DE COMPRA | Preço Compra |
| PRECO DE VENDA | Preço Venda |
| ALIQUOTA PIS | PIS |
| ALIQUOTA COFINS | COFINS |
| NCM | COD_NCM |
| CEST | Cod_CEST |
| ... | ... |

### `FIELD_FILL_DEFAULTS`
Valores aplicados quando o campo vier vazio na planilha do cliente.

| Campo | Default |
|---|---|
| Unidade | UN |
| Preço Venda | 0,00 |
| Preço Compra | 0,00 |

### `REQUIRED_FIELDS`
Campos obrigatórios: `Código Produto`, `Nome Produto`, `Unidade`, `Preço Venda`, `COD_NCM`.

### `FIELD_RULES`
Regras de validação por coluna:
- `digits` — somente dígitos, comprimento fixo (NCM=8, CEST=7, CFOP=4)
- `currency` — valor monetário formato `R$ 1.234,56`
- `decimal` — número decimal com vírgula (PIS, COFINS: ex. `0,65`)

### `TEMPLATE_DEFAULTS`
Defaults para colunas do template que não vêm da planilha do cliente (ex. flags de comportamento TOTVS).

### `TEMPLATE_COLUMNS`
Lista ordenada com todas as ~90 colunas do template TOTVS Food 5.0. Define a ordem exata de saída.

---

## Planilha de Entrada — PLANILHA DE CADASTROS TOTVS FOOD 5.0.xls

Arquivo fornecido ao cliente para preenchimento.

- **Formato:** `.xls` (Excel 97-2003)
- **Aba:** `1. Produtos de Venda`
- **Estrutura:** linha 0 = seções mescladas (ignorada), linha 1 = cabeçalhos reais, linha 2+ = dados
- **88 produtos** no exemplo de referência

| Coluna do cliente | Mapeado para |
|---|---|
| CÓDIGO PRODUTO VENDA | Código Produto |
| CÓDIGO DE BARRAS | Cod GTIN NF-e |
| NOME PRODUTO | Nome Produto |
| PRODUTO PESÁVEL? | Pesável |
| VENDA FRACIONADA? | Permitir Venda Fracionada |
| UNIDADE DE MEDIDA | Unidade |
| GRUPO | Grupo |
| SUBGRUPO | SubGrupo |
| PREÇO DE COMPRA | Preço Compra |
| PREÇO DE VENDA | Preço Venda |
| LOCAL DE IMPRESSÃO (COZINHA, BAR, ETC) | Local Impressão |
| NCM | COD_NCM |
| CEST | Cod_CEST |
| TRIBUTO | Tributo |
| IMPOSTO (% ICMS) | Imposto |
| CFOP | CFOP |
| CST OU CSOSN | CST_CSOSN_Venda |
| CST PIS | CST_PIS |
| PIS CALCULO | PIS_Tipo_Calculo |
| ALÍQUOTA PIS | PIS |
| CST COFINS | CST_COFINS |
| COFINS CALCULO | COFINS_Tipo_Calculo |
| ALÍQUOTA COFINS | COFINS |
| CÓDIGO BENEFÍCIO FISCAL | CodigoBeneficioRBC |
| REDUÇÃO ICMS (%) | PER_REDUCAO_BC_ICMS |

---

## src/reader.py

```python
read_client_file(path: Path) -> pd.DataFrame
```
- Aceita `.xls` (lê aba `1. Produtos de Venda`, header na linha 1 — pula seção mesclada da linha 0)
- Aceita `.xlsx`, `.xlsm` (header na linha 0)
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
2. Limpa texto (strip, colapso de espaços)
3. Converte automaticamente colunas SIM/NÃO → 1/0
4. Gera `Código Produto` sequencial para linhas sem código (ordenado por SubGrupo)
5. Aplica `FIELD_FILL_DEFAULTS` (Preço Venda e Preço Compra → `0,00` se vazios)
6. Aplica formatadores:
   - `_to_currency` → `R$ 1.234,56` (Preço Venda, Preço Compra, Imposto...)
   - `_to_number` → `0,65` (PIS, COFINS — número decimal sem R$)
   - `_digits_only` + zfill → NCM (8 dígitos), CEST (7), CFOP (4)
   - `_to_bool` → SIM/S/X → 1, NÃO/N → 0
7. Copia `Nome Produto` → `Texto Fiscal`, `Texto Botão Touch`, `Texto Botao Pocket` (quando vazios)
8. Garante todas as colunas do template na ordem correta

---

## src/validator.py

```python
validate(df, required_fields, field_rules) -> list[ValidationError]
```

**`ValidationError`** (dataclass): `row`, `field`, `value`, `reason`

**Validações:**
- Campos obrigatórios vazios
- Duplicatas de `Código Produto`
- Formato de dígitos (NCM, CEST, CFOP)
- Formato de moeda (Preço Venda, Preço Compra, Imposto...)
- Formato decimal com vírgula (PIS, COFINS)

Linhas com erros são **excluídas** do arquivo de saída e listadas em `erros.xlsx`.

---

## src/writer.py

```python
write_output(df, output_path)       # Gera PlanilhaImportaçãoLoja.xlsm
write_error_report(errors, path)    # Gera erros.xlsx
```

**Formatação do Excel de saída:**
- Cabeçalho azul escuro (`#1F4E79`), texto branco, negrito
- Colunas auto-dimensionadas (máx. 40 chars)
- Linha 1 travada (freeze panes)

**Relatório de erros:**
- Cabeçalho vermelho, linhas amarelas
- Colunas: Linha, Campo, Valor Informado, Motivo do Erro

---

## api.py — Endpoints HTTP

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Interface web (index.html) |
| POST | `/processar` | Recebe planilha, processa, retorna JSON com stats e erros |
| GET | `/download/{session_id}/resultado` | Download `PlanilhaImportaçãoLoja.xlsm` |
| GET | `/download/{session_id}/erros` | Download `erros.xlsx` |

**Resposta de `/processar`:**
```json
{
  "session_id": "uuid",
  "stats": { "total": 100, "exportados": 98, "rejeitados": 2 },
  "erros": [{ "linha": 5, "campo": "COD_NCM", "valor": "123", "motivo": "Deve ter 8 dígitos" }]
}
```

Sessions ficam em memória (`_sessions` dict) — expiram ao reiniciar o servidor.

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
Aba: `Produtos` | ~90 colunas na ordem exata do template oficial.

### Colunas importantes

| Coluna | Formato | Observação |
|---|---|---|
| Código Produto | inteiro (string) | Auto-gerado se vazio |
| Nome Produto | texto | Fonte para Texto Fiscal/Touch/Pocket |
| Preço Venda | `R$ 1.234,56` | Default `0,00` |
| Preço Compra | `R$ 1.234,56` | Default `0,00` |
| PIS | `0,65` | Número decimal (sem R$) |
| COFINS | `3,00` | Número decimal (sem R$) |
| COD_NCM | 8 dígitos | Obrigatório |
| Pesável | `0` ou `1` | SIM/NÃO convertido |
| Texto Fiscal | texto | Cópia de Nome Produto |
| Texto Botão Touch | texto | Cópia de Nome Produto |
| Texto Botao Pocket | texto | Cópia de Nome Produto |

---

## Deploy

- **Plataforma:** Render.com
- **Runtime:** Python 3.14 / uvicorn
- **Start command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`
- **Procfile:** `web: uvicorn api:app --host 0.0.0.0 --port $PORT`
