# -*- coding: utf-8 -*-
"""
Mapeamento de colunas: planilha cliente → template TOTVS Food 5.0
Chaves do COLUMN_MAP são normalizadas (sem acento, maiúsculo, sem quebra de linha).
"""

# Coluna do cliente (normalizada) → coluna exata do template
COLUMN_MAP: dict[str, str] = {
    "CODIGO PRODUTO VENDA": "Código Produto",
    "NOME PRODUTO": "Nome Produto",
    "PRODUTO PESAVEL?": "Pesável",
    "VENDA FRACIONADA?": "Permitir Venda Fracionada",
    "UNIDADE DE MEDIDA": "Unidade",
    "GRUPO": "Grupo",
    "SUBGRUPO": "SubGrupo",
    "PRECO DE COMPRA": "Preço Compra",
    "PRECO DE VENDA": "Preço Venda",
    "LOCAL DE IMPRESSAO (COZINHA, BAR, ETC)": "Local Impressão",
    "CODIGO BENEFICIO FISCAL": "CodigoBeneficioRBC",
    "REDUCAO ICMS (%)": "PER_REDUCAO_BC_ICMS",
}

# Valores de fallback para campos do cliente quando vierem vazios
FIELD_FILL_DEFAULTS: dict[str, str] = {
    "Unidade":        "UN",
    "Preço Venda":    "0,00",
    "Preço Compra":   "0,00",
}

# Campos obrigatórios (nomes do template)
REQUIRED_FIELDS: list[str] = [
    "Código Produto",
    "Nome Produto",
    "Unidade",
    "Preço Venda",
]

# Regras de validação por coluna do template
FIELD_RULES: dict[str, dict] = {
    "COD_NCM":             {"type": "digits",   "length": 8},
    "Cod_CEST":            {"type": "digits",   "length": 7},
    "CFOP":                {"type": "digits",   "length": 4},
    "Preço Venda":         {"type": "currency"},
    "Preço Compra":        {"type": "currency"},
    "PIS":                 {"type": "decimal"},
    "COFINS":              {"type": "decimal"},
    "Imposto":             {"type": "currency"},
    "PER_REDUCAO_BC_ICMS": {"type": "currency"},
    "Quantidade Estoque":  {"type": "decimal"},
    "Quantidade Mínima":   {"type": "decimal"},
}

# Defaults para colunas do template não preenchidas pelo cliente
TEMPLATE_DEFAULTS: dict[str, str] = {
    "Quantidade Estoque":               "0",
    "Quantidade Mínima":                "0",
    "Produto Composto":                 "0",
    "Não Exibir no Cardápio":           "0",
    "Local Impressão":                  "NENHUM",
    "Dividido por":                     "1",
    "Pesável":                          "0",
    "Processado":                       "0",
    "Imposto":                          "0",
    "Tributo":                          "I",
    "COD_NCM":                          "21069090",
    "CFOP_Venda":                       "5102",
    "Cod_CEST":                         "",
    "Cobrar Serviço":                   "1",
    "Cobrar Consumação":                "1",
    "Pontos":                           "0",
    "Margem Lucro":                     "0",
    "Permitir Venda Fracionada":        "0",
    "Imprimir Boqueta":                 "1",
    "Imprimir Ticket 1 a 1":            "1",
    "Botao Touch Cor 1":                "67; 97; 161",
    "Botao Touch Cor 2":                "135; 206; 250",
    "Botao Touch Cor 3":                "0; 0; 128",
    "IPI":                              "0",
    "Redução ICMS":                     "0",
    "Exige Observação":                 "0",
    "Tempo Adicional Entrega":          "0",
    "Inventario no Fechamento":         "0",
    "Menor de Idade":                   "0",
    "IAT":                              "T",
    "IPPT":                             "T",
    "Ordem Touch":                      "0",
    "Mais Vendidos":                    "0",
    "Ordem Mais Vendidos":              "0",
    "Imprimir Monitor":                 "1",
    "Exibe Adicional":                  "0",
    "Exibe Adicional em Cascata":       "0",
    "Um Adicional":                     "0",
    "Nao Vender Adicional":             "0",
    "TIPO_ITEM":                        "0",
    "Quantidade Minima Adicionais":     "0",
    "Quantidade Maxima Adicionais":     "0",
    "PIS":                              "",
    "COFINS":                           "",
    "COFINS_Per_Entrada":               "0",
    "COFINS_valor":                     "0",
    "COFINS_Valor_Entrada":             "0",
    "CST_CSOSN_Venda":                  "102",
    "PIS_Per_Entrada":                  "0",
    "PIS_valor":                        "0",
    "PIS_Valor_Entrada":                "0",
    "PERMITE_NUMIDENTIFICACAO":         "0",
    "CSOSN_Entrada":                    "",
    "CSOSN":                            "1",
    "ExibirComoAdicional":              "0",
    "IndicadorExigibilidadeISS":        "0",
    "IndicadorIncentivoFiscal":         "0",
    "IndicadorNaturezaOperacaoISSQN":   "0",
    "TouchExibirComplemento":           "0",
    "NaoMultiplicarQuantidadeAdicional": "0",
}

# Ordem exata das colunas do template (aba Produtos)
TEMPLATE_COLUMNS: list[str] = [
    "Código Produto", "Nome Produto", "Quantidade Estoque", "Quantidade Mínima",
    "Unidade", "Grupo", "SubGrupo", "Produto Composto", "Não Exibir no Cardápio",
    "Preço Venda", "Preço Compra", "Local Impressão", "Dividido por", "Unidade Compra",
    "Pesável", "Processado", "Imposto", "Cobrar Serviço", "Cobrar Consumação",
    "Fator", "Pontos", "Tributo", "Setor", "Loja", "Descrição Produto",
    "Margem Lucro", "Texto Fiscal", "Texto Botão Touch", "Permitir Venda Fracionada",
    "Imprimir Boqueta", "Imprimir Ticket 1 a 1", "Botao Touch Cor 1",
    "Botao Touch Cor 2", "Botao Touch Cor 3", "CFOP", "CST", "IPI",
    "Redução ICMS", "Exige Observação", "Tributo Compra", "Tempo Adicional Entrega",
    "Inventario no Fechamento", "Menor de Idade", "Texto Botao Pocket", "IAT", "IPPT",
    "Ordem Touch", "Mais Vendidos", "Ordem Mais Vendidos", "Imprimir Monitor",
    "Exibe Adicional", "Exibe Adicional em Cascata", "Um Adicional",
    "Nao Vender Adicional", "TIPO_ITEM", "COD_NCM", "Quantidade Minima Adicionais",
    "Quantidade Maxima Adicionais", "linha_wisr", "PIS", "COFINS", "CFOP_Venda",
    "COFINS_Per_Entrada", "COFINS_Tipo_Calculo", "COFINS_Tipo_Calculo_Entrada",
    "COFINS_valor", "COFINS_Valor_Entrada", "CST_COFINS", "CST_COFINS_Entrada",
    "CST_CSOSN_Venda", "CST_PIS", "CST_PIS_Entrada", "PER_REDUCAO_BC_ICMS",
    "PIS_Per_Entrada", "PIS_Tipo_Calculo", "PIS_Tipo_Calculo_Entrada",
    "PIS_valor", "PIS_Valor_Entrada", "PERMITE_NUMIDENTIFICACAO",
    "CSOSN_Entrada", "CSOSN", "ExibirComoAdicional", "CodServicoAbrasf",
    "IndicadorExigibilidadeISS", "IndicadorIncentivoFiscal",
    "IndicadorNaturezaOperacaoISSQN", "Cod_CEST", "TouchExibirComplemento",
    "NaoMultiplicarQuantidadeAdicional", "Cod GTIN NF-e",
    "CodigoCreditoPresumido", "PercentualCreditoPresumido", "CodigoBeneficioRBC",
]
