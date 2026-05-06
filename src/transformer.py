# -*- coding: utf-8 -*-
import re
import unicodedata
from typing import Any

import pandas as pd


def _clean(value: Any) -> str:
    if pd.isna(value) or value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


_SQL_UNSAFE = re.compile(r"""['";\\\|<>&%@#\^*`!?~+=\$\{\}\[\]°ªº]""")

_UNIT_MAP: dict[str, str] = {
    "UN": "UN", "UNITARIO": "UN", "UNIDADE": "UN", "UNID": "UN", "UND": "UN",
    "UNIT": "UN", "PC": "UN", "PECA": "UN", "PÇ": "UN",
    "KG": "KG", "QUILO": "KG", "QUILOGRAMA": "KG", "KILO": "KG",
    "GR": "GR", "G": "GR", "GRAMA": "GR", "GRAMAS": "GR",
    "LT": "LT", "L": "LT", "LITRO": "LT", "LITROS": "LT", "LTS": "LT",
    "ML": "ML", "MILILITRO": "ML", "MILILITROS": "ML",
    "CX": "CX", "CAIXA": "CX", "PCT": "PCT", "PACOTE": "PCT",
    "FD": "FD", "FARDO": "FD", "SC": "SC", "SACO": "SC",
    "MT": "MT", "METRO": "MT", "M": "MT",
    "DZ": "DZ", "DUZIA": "DZ", "DÚZIA": "DZ",
    "PR": "PR", "PAR": "PR",
}


def _normalize_unit(value: str) -> str:
    key = unicodedata.normalize("NFD", value.upper().strip())
    key = "".join(c for c in key if unicodedata.category(c) != "Mn")
    return _UNIT_MAP.get(key, value.strip())

def _sanitize_product_name(value: str) -> str:
    """Remove caracteres que causam falha na importação SQL do TOTVS Food.
    Mantém letras (incluindo acentuadas), números, espaços e pontuação básica (-.,:()/)."""
    value = _SQL_UNSAFE.sub("", value)
    return re.sub(r"\s{2,}", " ", value).strip()


def _digits_only(value: str) -> str:
    """Remove tudo que não é dígito. Validação de comprimento fica no validator."""
    return re.sub(r"\D", "", value)


def _to_currency(value: str) -> str:
    cleaned = re.sub(r"[^\d,.]", "", value)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        n = float(cleaned)
        return f"R${n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except ValueError:
        return ""


def _to_bool(value: str) -> str:
    """Normaliza SIM/NÃO/S/N/X → 1/0."""
    v = unicodedata.normalize("NFD", value.upper())
    v = "".join(c for c in v if unicodedata.category(c) != "Mn")
    return "1" if v in {"SIM", "S", "X", "1", "TRUE", "VERDADEIRO"} else "0"


_FORMATTERS: dict[str, Any] = {
    "COD_NCM":                {"fn": lambda v: _digits_only(v).zfill(8)},
    "Cod_CEST":               {"fn": lambda v: _digits_only(v).zfill(7)},
    "CFOP":                   {"fn": lambda v: _digits_only(v)[:4]},
    "Preço Venda":            {"fn": _to_currency},
    "Preço Compra":           {"fn": _to_currency},
    "PIS":                    {"fn": _to_currency},
    "COFINS":                 {"fn": _to_currency},
    "Imposto":                {"fn": _to_currency},
    "PER_REDUCAO_BC_ICMS":    {"fn": _to_currency},
    "Pesável":                {"fn": _to_bool},
    "Permitir Venda Fracionada": {"fn": _to_bool},
}


_BOOL_VALS = {"SIM", "NAO", "S", "N", "X"}


def _normalize_key(name: str) -> str:
    name = unicodedata.normalize("NFD", str(name))
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", name).strip().upper()


def _is_bool_col(series: pd.Series) -> bool:
    """True se todos os valores não-vazios da coluna são SIM/NÃO/S/N/X."""
    non_empty = series.dropna().apply(lambda v: _normalize_key(str(v)))
    non_empty = non_empty[non_empty != ""]
    return len(non_empty) > 0 and non_empty.isin(_BOOL_VALS).all()


def _assign_sequential_codes(df: pd.DataFrame) -> pd.DataFrame:
    """Preenche Código Produto vazio com sequencial crescente ordenado por SubGrupo.
    Pula números já existentes para evitar duplicatas."""
    cod_col = "Código Produto"
    sub_col = "SubGrupo"

    if cod_col not in df.columns:
        return df

    mask = df[cod_col].str.strip() == ""
    if not mask.any():
        return df

    existing = {str(v).strip() for v in df.loc[~mask, cod_col] if str(v).strip()}

    sort_key = (
        df.loc[mask, sub_col].fillna("").str.upper()
        if sub_col in df.columns
        else pd.Series("", index=df[mask].index)
    )
    sorted_idx = df[mask].assign(_sort=sort_key).sort_values("_sort").index

    counter = 1
    for idx in sorted_idx:
        while str(counter) in existing:
            counter += 1
        df.at[idx, cod_col] = str(counter)
        existing.add(str(counter))
        counter += 1

    return df


def transform(
    df: pd.DataFrame,
    column_map: dict[str, str],
    template_defaults: dict[str, str],
    template_columns: list[str],
    field_fill_defaults: dict[str, str] | None = None,
) -> pd.DataFrame:
    norm_map = {_normalize_key(k): v for k, v in column_map.items()}

    rename = {col: norm_map[col] for col in df.columns if col in norm_map}
    df = df.rename(columns=rename)

    # Limpa texto e converte colunas SIM/NÃO → 1/0 automaticamente
    text_cols = [c for c in df.columns if c not in _FORMATTERS]
    for col in text_cols:
        if _is_bool_col(df[col]):
            df[col] = df[col].apply(lambda v: _to_bool(_clean(v)) if _clean(v) else "")
        else:
            df[col] = df[col].apply(_clean)

    # Normaliza Unidade → sigla TOTVS (ex: "unitario" → "UN")
    if "Unidade" in df.columns:
        df["Unidade"] = df["Unidade"].apply(
            lambda v: _normalize_unit(_clean(v)) if _clean(v) else ""
        )

    # Sanitiza Nome Produto (remove chars inválidos para SQL)
    if "Nome Produto" in df.columns:
        df["Nome Produto"] = df["Nome Produto"].apply(
            lambda v: _sanitize_product_name(_clean(v))
        )

    # Código sequencial para linhas sem código
    df = _assign_sequential_codes(df)

    # Preenche campos vazios com defaults configurados
    if field_fill_defaults:
        for col, default in field_fill_defaults.items():
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda v: default if (pd.isna(v) or str(v).strip() == "") else v
                )

    # Aplica formatadores numéricos/fiscais
    for col, rule in _FORMATTERS.items():
        if col in df.columns:
            df[col] = df[col].apply(lambda v: rule["fn"](_clean(v)) if _clean(v) else "")

    # Garante todas as colunas do template, com defaults
    for col in template_columns:
        if col not in df.columns:
            df[col] = template_defaults.get(col, "")

    return df[template_columns]
