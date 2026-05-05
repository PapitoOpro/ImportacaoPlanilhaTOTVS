# -*- coding: utf-8 -*-
from dataclasses import dataclass

import pandas as pd


@dataclass
class ValidationError:
    row: int
    field: str
    value: str
    reason: str


def _check_digits(val: str, length: int | None) -> str | None:
    if not val.isdigit():
        return "Deve conter apenas dígitos"
    if length and len(val) != length:
        return f"Deve ter {length} dígitos (encontrado: {len(val)})"
    return None


def _check_currency(val: str) -> str | None:
    cleaned = val.replace("R$", "").strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        if float(cleaned) < 0:
            return "Valor negativo não permitido"
    except ValueError:
        return "Formato de valor inválido"
    return None


def _check_decimal(val: str) -> str | None:
    try:
        float(val.replace(",", "."))
    except ValueError:
        return "Formato numérico inválido"
    return None


_CHECKERS = {
    "digits":   lambda v, r: _check_digits(v, r.get("length")),
    "currency": lambda v, r: _check_currency(v),
    "decimal":  lambda v, r: _check_decimal(v),
}


def _check_duplicates(df: pd.DataFrame, col: str) -> list[ValidationError]:
    errors: list[ValidationError] = []
    if col not in df.columns:
        return errors
    non_empty = df[df[col].str.strip() != ""]
    dupes = non_empty[non_empty[col].duplicated(keep=False)]
    for idx, row in dupes.iterrows():
        val = str(row[col])
        errors.append(ValidationError(int(idx) + 3, col, val, "Código duplicado"))
    return errors


def validate(
    df: pd.DataFrame,
    required_fields: list[str],
    field_rules: dict[str, dict],
) -> list[ValidationError]:
    errors: list[ValidationError] = []

    errors.extend(_check_duplicates(df, "Código Produto"))

    for idx, row in df.iterrows():
        row_num = int(idx) + 3

        for field in required_fields:
            val = str(row.get(field, "")).strip()
            if not val:
                errors.append(ValidationError(row_num, field, val, "Campo obrigatório vazio"))

        for field, rules in field_rules.items():
            val = str(row.get(field, "")).strip()
            if not val:
                continue
            checker = _CHECKERS.get(rules.get("type", ""))
            if checker:
                msg = checker(val, rules)
                if msg:
                    errors.append(ValidationError(row_num, field, val, msg))

    return errors
