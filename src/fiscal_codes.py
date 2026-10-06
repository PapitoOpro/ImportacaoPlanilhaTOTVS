# -*- coding: utf-8 -*-
"""Normalização de códigos fiscais (NCM / CEST). Sem I/O."""
import re

NCM_LENGTH = 8
CEST_LENGTH = 7


def _digits(value: object) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", text):  # "21069090.0" (número vindo do Excel)
        text = text.split(".")[0]
    return re.sub(r"\D", "", text)


def normalize_ncm(value: object) -> str:
    """'3305.10.00' / '33051000' / 3305100 (Excel sem zero) → '33051000'. Retorna '' se inválido."""
    digits = _digits(value)
    if len(digits) == NCM_LENGTH - 1:  # Excel remove o zero à esquerda (capítulos 01–09)
        digits = "0" + digits
    return digits if len(digits) == NCM_LENGTH else ""


def normalize_cest(value: object) -> str:
    """'01.001.00' / '0100100' / 100100 (Excel sem zero) → '0100100'. Retorna '' se inválido."""
    digits = _digits(value)
    if len(digits) == CEST_LENGTH - 1:  # segmentos 01–09
        digits = "0" + digits
    return digits if len(digits) == CEST_LENGTH else ""


def format_ncm(code: str) -> str:
    return f"{code[:4]}.{code[4:6]}.{code[6:]}" if len(code) == NCM_LENGTH else code


def format_cest(code: str) -> str:
    return f"{code[:2]}.{code[2:5]}.{code[5:]}" if len(code) == CEST_LENGTH else code
