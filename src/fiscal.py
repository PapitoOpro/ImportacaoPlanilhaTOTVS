# -*- coding: utf-8 -*-
"""Gera a planilha ImportacaoRegraNCMDadosFiscais a partir da planilha de cadastro de produtos do cliente."""
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

from validator import ValidationError

logger = logging.getLogger(__name__)

REGIMES: dict[str, str] = {
    "1": "Simples Nacional",
    "2": "Simples Nacional - Excesso",
    "3": "Regime Normal",
}
_SIMPLES = {"1", "2"}

_TRIBUTO_MAP: dict[str, str] = {
    "T": "T", "TRIBUTADO": "T", "TRIBUTADA": "T",
    "I": "I", "ISENTO": "I", "ISENTA": "I", "ISENCAO": "I",
    "S": "S", "ST": "S", "SUBSTITUICAO": "S", "SUBSTITUICAO TRIBUTARIA": "S",
    "N": "N", "NAO TRIBUTADO": "N", "NAO TRIBUTADA": "N", "NT": "N",
    "F": "F", "FORA DO ESTADO": "F",
}
_TRIBUTO_EXIGE_IMPOSTO = {"T", "S"}

_UF_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}

# Colunas da planilha do cliente (normalizadas pelo reader)
_COL_NCM = "NCM"
_COL_CEST = "CEST"
_COL_TRIBUTO = "TRIBUTO"
_COL_IMPOSTO = "IMPOSTO (% ICMS)"
_COL_CFOP = "CFOP"
_COL_CST = "CST OU CSOSN"
_COL_BENEFICIO = "CODIGO BENEFICIO FISCAL"
_COL_REDUCAO = "REDUCAO ICMS (%)"
_COL_PIS = ("CST PIS", "PIS CALCULO", "ALIQUOTA PIS")
_COL_COFINS = ("CST COFINS", "COFINS CALCULO", "ALIQUOTA COFINS")

_TIPO_ITEM_PADRAO = "00"  # Mercadoria para Revenda
_FIRST_DATA_ROW = 3       # linha 1 = seções, linha 2 = cabeçalho na planilha do cliente


@dataclass(frozen=True)
class RegraFiscal:
    ncm: str
    cest: str
    tributo: str
    imposto: float
    cfop: str
    cst_csosn: str
    beneficio: str
    reducao: float | None
    pis: tuple[str, float | None, float | None] | None
    cofins: tuple[str, float | None, float | None] | None


@dataclass
class ResultadoRegras:
    regras: list[RegraFiscal] = field(default_factory=list)
    total_produtos: int = 0
    produtos_validos: int = 0
    errors: list[ValidationError] = field(default_factory=list)


def _log(level: int, event: str, **kwargs: Any) -> None:
    logger.log(level, json.dumps({"event": event, **kwargs}, ensure_ascii=False, default=str))


def _clean(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _norm_text(value: str) -> str:
    value = unicodedata.normalize("NFD", value.upper())
    return "".join(c for c in value if unicodedata.category(c) != "Mn").strip()


def _digits(value: str) -> str:
    # "21069090.0" (Excel numérico) → "21069090"
    value = re.sub(r"\.0+$", "", value)
    return re.sub(r"\D", "", value)


def _to_float(value: str) -> float | None:
    cleaned = re.sub(r"[^\d,.\-]", "", value)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return round(float(cleaned), 4)
    except ValueError:
        return None


def _pad_leading_zero(digits: str, length: int) -> str:
    """Repõe o zero à esquerda que o Excel remove (NCM/CEST nunca têm mais de 1 zero inicial)."""
    return digits.zfill(length) if len(digits) == length - 1 else digits


def _get(row: pd.Series, col: str) -> str:
    return _clean(row[col]) if col in row.index else ""


def _parse_contribuicao(
    row: pd.Series, cols: tuple[str, str, str]
) -> tuple[str, float | None, float | None] | None:
    """Retorna (CST, alíquota %, alíquota R$) ou None se CST vazio."""
    cst = _digits(_get(row, cols[0]))
    if not cst:
        return None
    aliquota = _to_float(_get(row, cols[2]))
    por_valor = _norm_text(_get(row, cols[1])) in {"VALOR", "R$", "REAIS", "QUANTIDADE"}
    return (cst.zfill(2), None, aliquota) if por_valor else (cst.zfill(2), aliquota, None)


def _parse_row(
    row: pd.Series, linha: int, regime: str
) -> tuple[RegraFiscal | None, list[ValidationError]]:
    errors: list[ValidationError] = []

    def err(campo: str, valor: str, motivo: str) -> None:
        errors.append(ValidationError(row=linha, field=campo, value=valor, reason=motivo))

    ncm_raw = _get(row, _COL_NCM)
    ncm = _pad_leading_zero(_digits(ncm_raw), 8)
    if len(ncm) != 8:
        err("NCM", ncm_raw, "NCM deve ter 8 dígitos")

    cest_raw = _get(row, _COL_CEST)
    cest = _pad_leading_zero(_digits(cest_raw), 7) if _digits(cest_raw).strip("0") else ""
    if cest and len(cest) != 7:
        err("CEST", cest_raw, "CEST deve ter 7 dígitos")

    tributo_raw = _get(row, _COL_TRIBUTO)
    tributo = _TRIBUTO_MAP.get(_norm_text(tributo_raw), "")
    if not tributo:
        err("TRIBUTO", tributo_raw, "Tributo inválido (use T, I, S, N ou F)")

    imposto_raw = _get(row, _COL_IMPOSTO)
    imposto = _to_float(imposto_raw)
    if imposto is None:
        if tributo in _TRIBUTO_EXIGE_IMPOSTO:
            err("IMPOSTO (% ICMS)", imposto_raw, "Obrigatório quando Tributo = T ou S")
        imposto = 0.0

    cfop_raw = _get(row, _COL_CFOP)
    cfop = _digits(cfop_raw)
    if len(cfop) != 4:
        err("CFOP", cfop_raw, "CFOP deve ter 4 dígitos")

    cst_raw = _get(row, _COL_CST)
    cst_digits = _digits(cst_raw)
    if regime in _SIMPLES:
        cst_csosn = cst_digits.zfill(3) if cst_digits else ""
        if len(cst_csosn) != 3:
            err("CST ou CSOSN", cst_raw, "CSOSN deve ter 3 dígitos no Simples Nacional")
    else:
        cst_csosn = cst_digits.zfill(2) if cst_digits else ""
        if len(cst_csosn) not in (2, 3):
            err("CST ou CSOSN", cst_raw, "CST deve ter 2 ou 3 dígitos no Regime Normal")

    reducao = _to_float(_get(row, _COL_REDUCAO))
    regime_normal = regime not in _SIMPLES

    if errors:
        return None, errors

    return RegraFiscal(
        ncm=ncm,
        cest=cest,
        tributo=tributo,
        imposto=imposto,
        cfop=cfop,
        cst_csosn=cst_csosn,
        beneficio=_get(row, _COL_BENEFICIO),
        reducao=reducao if reducao else None,
        pis=_parse_contribuicao(row, _COL_PIS) if regime_normal else None,
        cofins=_parse_contribuicao(row, _COL_COFINS) if regime_normal else None,
    ), []


def build_regras(df: pd.DataFrame, regime: str) -> ResultadoRegras:
    """Uma regra por combinação única de NCM + CEST + dados fiscais."""
    if regime not in REGIMES:
        raise ValueError(f"Regime inválido: {regime}")

    if _COL_NCM not in df.columns:
        raise ValueError("Coluna NCM não encontrada na planilha do cliente")

    result = ResultadoRegras()
    vistos: dict[RegraFiscal, None] = {}

    for idx, row in df.iterrows():
        if not _get(row, _COL_NCM) and not _get(row, "NOME PRODUTO"):
            continue
        result.total_produtos += 1
        regra, errors = _parse_row(row, int(idx) + _FIRST_DATA_ROW, regime)
        if errors:
            result.errors.extend(errors)
            continue
        result.produtos_validos += 1
        vistos.setdefault(regra, None)

    result.regras = list(vistos)
    _log(logging.INFO, "regras_ncm_geradas", regime=regime, produtos=result.total_produtos,
         regras=len(result.regras), erros=len(result.errors))
    return result


def _clear_data_rows(ws: Any) -> None:
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)


def _write_text(ws: Any, row: int, col: int, value: str) -> None:
    if not value:
        return
    cell = ws.cell(row, col, value)
    cell.number_format = "@"


def write_regras(
    regras: list[RegraFiscal],
    output_path: Path,
    template_path: Path,
    regime: str,
    uf: str,
    numero_loja: str,
) -> None:
    if not template_path.exists():
        raise FileNotFoundError(f"Template não encontrado: {template_path}")

    wb = openpyxl.load_workbook(template_path)
    ws_ncm = wb["NCM X UF"]
    ws_dados = wb["Dados Básicos"]
    ws_pis = wb["PIS"]
    ws_cofins = wb["COFINS"]
    for ws in (ws_ncm, ws_dados, ws_pis, ws_cofins):
        _clear_data_rows(ws)

    simples = regime in _SIMPLES
    loja = int(numero_loja) if numero_loja.isdigit() else (numero_loja or None)
    pis_row = cofins_row = 2

    for codigo, regra in enumerate(regras, start=1):
        r = codigo + 1

        # NCM X UF: CODIGO REGRA | UF | NCM | CEST | REGIME | LOJA
        ws_ncm.cell(r, 1, codigo)
        ws_ncm.cell(r, 2, uf)
        _write_text(ws_ncm, r, 3, regra.ncm)
        _write_text(ws_ncm, r, 4, regra.cest)
        ws_ncm.cell(r, 5, int(regime))
        ws_ncm.cell(r, 6, loja)

        # Dados Básicos: CODIGO | TRIBUTO | IMPOSTO % | TIPO ITEM | BENEFICIO | CFOP | CST | CSOSN | REDUÇÃO ICMS %
        ws_dados.cell(r, 1, codigo)
        ws_dados.cell(r, 2, regra.tributo)
        ws_dados.cell(r, 3, regra.imposto)
        _write_text(ws_dados, r, 4, _TIPO_ITEM_PADRAO)
        _write_text(ws_dados, r, 5, regra.beneficio)
        _write_text(ws_dados, r, 6, regra.cfop)
        _write_text(ws_dados, r, 8 if simples else 7, regra.cst_csosn)
        ws_dados.cell(r, 9, regra.reducao)

        if regra.pis:
            ws_pis.cell(pis_row, 1, codigo)
            _write_text(ws_pis, pis_row, 2, regra.pis[0])
            ws_pis.cell(pis_row, 3, regra.pis[1])
            ws_pis.cell(pis_row, 4, regra.pis[2])
            pis_row += 1

        if regra.cofins:
            ws_cofins.cell(cofins_row, 1, codigo)
            _write_text(ws_cofins, cofins_row, 2, regra.cofins[0])
            ws_cofins.cell(cofins_row, 3, regra.cofins[1])
            ws_cofins.cell(cofins_row, 4, regra.cofins[2])
            cofins_row += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def validate_uf(uf: str) -> str:
    uf = uf.strip().upper()
    if uf not in _UF_VALIDAS:
        raise ValueError(f"UF inválida: {uf or '(vazia)'}")
    return uf
