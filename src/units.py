# -*- coding: utf-8 -*-
"""Tabela de unidades TOTVS Food: sigla → descrição + apelidos aceitos (sem acento, maiúsculo)."""

UNITS: dict[str, tuple[str, tuple[str, ...]]] = {
    "UN":  ("UNIDADE",    ("UNITARIO", "UNIDADE", "UNIDADES", "UNID", "UND", "UNIT", "U", "PC", "PCS", "PECA", "PECAS")),
    "KG":  ("QUILOGRAMA", ("QUILO", "QUILOS", "QUILOGRAMA", "QUILOGRAMAS", "KILO", "KILOS", "KGS")),
    "GR":  ("GRAMA",      ("G", "GRS", "GRAMA", "GRAMAS")),
    "LT":  ("LITRO",      ("L", "LITRO", "LITROS", "LTS")),
    "ML":  ("MILILITRO",  ("MILILITRO", "MILILITROS")),
    "DS":  ("DOSE",       ("DOSE", "DOSES", "DSE")),
    "PRC": ("PORCAO",     ("PORCAO", "PORCOES", "PORC", "POR")),
    "FT":  ("FATIA",      ("FATIA", "FATIAS")),
    "CP":  ("COPO",       ("COPO", "COPOS")),
    "TC":  ("TACA",       ("TACA", "TACAS")),
    "JR":  ("JARRA",      ("JARRA", "JARRAS")),
    "GF":  ("GARRAFA",    ("GARRAFA", "GARRAFAS", "GFA", "GRF")),
    "LAT": ("LATA",       ("LATA", "LATAS")),
    "BD":  ("BALDE",      ("BALDE", "BALDES")),
    "PT":  ("POTE",       ("POTE", "POTES")),
    "BDJ": ("BANDEJA",    ("BANDEJA", "BANDEJAS")),
    "CX":  ("CAIXA",      ("CAIXA", "CAIXAS", "CXS")),
    "PCT": ("PACOTE",     ("PACOTE", "PACOTES")),
    "FD":  ("FARDO",      ("FARDO", "FARDOS")),
    "SC":  ("SACO",       ("SACO", "SACOS")),
    "CB":  ("COMBO",      ("COMBO", "COMBOS")),
    "KIT": ("KIT",        ("KITS",)),
    "MT":  ("METRO",      ("M", "METRO", "METROS")),
    "CM":  ("CENTIMETRO", ("CENTIMETRO", "CENTIMETROS")),
    "DZ":  ("DUZIA",      ("DUZIA", "DUZIAS")),
    "PR":  ("PAR",        ("PAR", "PARES")),
}

UNIT_ALIASES: dict[str, str] = {
    alias: sigla
    for sigla, (_, aliases) in UNITS.items()
    for alias in (sigla, *aliases)
}

UNIT_DESCRIPTIONS: dict[str, str] = {sigla: desc for sigla, (desc, _) in UNITS.items()}
