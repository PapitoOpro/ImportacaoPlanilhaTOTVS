# -*- coding: utf-8 -*-
"""Gera a planilha ImportacaoRegraNCMDadosFiscais a partir da planilha de cadastro de produtos do cliente."""
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

from fiscal_codes import normalize_cest, normalize_ncm
from fiscal_service import FiscalService, NcmCestValidation, StatusFiscal
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
    analise: list[dict[str, Any]] = field(default_factory=list)
    correcoes: list[dict[str, Any]] = field(default_factory=list)
    base_fiscal_disponivel: bool = False


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
    if re.fullmatch(r"\d+\.0+", value):  # "5101.0" (Excel numérico) → "5101"
        value = value.split(".")[0]
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
    ncm = normalize_ncm(ncm_raw)
    if not ncm:
        err("NCM", ncm_raw, "NCM deve ter 8 dígitos")

    cest_raw = _get(row, _COL_CEST)
    cest_informado = bool(_digits(cest_raw).strip("0"))
    cest = normalize_cest(cest_raw) if cest_informado else ""
    if cest_informado and not cest:
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


_STATUS_REJEITA = {StatusFiscal.INVALID_NCM: "NCM", StatusFiscal.INVALID_CEST: "CEST"}


def _analise_item(linha: int, produto: str, v: NcmCestValidation) -> dict[str, Any]:
    return {"linha": linha, "produto": produto, **v.to_dict()}


def _aplicar_catalogo_totvs(
    service: FiscalService, linha: int, regra: RegraFiscal, reference_date: str | None
) -> tuple[RegraFiscal, list[ValidationError], dict[str, Any] | None]:
    """NCM/CEST válidos oficialmente, mas ausentes (ou inativos) no cadastro do TOTVS.

    NCM ausente: rejeita. CEST ausente: troca somente se houver exatamente 1 CEST oficialmente
    relacionado ao NCM e cadastrado no TOTVS; com 0 ou vários candidatos rejeita e lista as opções."""
    if not service.in_totvs_catalog("NCM", regra.ncm):
        return regra, [ValidationError(row=linha, field="NCM", value=regra.ncm,
                                       reason="NCM não cadastrado no TOTVS — cadastre-o antes de importar")], None
    if not regra.cest or service.in_totvs_catalog("CEST", regra.cest):
        return regra, [], None

    candidatos = service.totvs_cest_candidates(regra.ncm, reference_date)
    if len(candidatos) == 1:
        correcao = {"linha": linha, "campo": "CEST", "de": regra.cest, "para": candidatos[0],
                    "motivo": "CEST não cadastrado no TOTVS; substituído pelo único CEST oficial do NCM aceito pelo TOTVS"}
        return replace(regra, cest=candidatos[0]), [], correcao

    opcoes = f" Opções aceitas pelo TOTVS para o NCM: {', '.join(candidatos)}." if candidatos else         " Nenhum CEST oficial do NCM está cadastrado no TOTVS."
    return regra, [ValidationError(row=linha, field="CEST", value=regra.cest,
                                   reason=f"CEST não cadastrado no TOTVS.{opcoes}")], None


def build_regras(
    df: pd.DataFrame,
    regime: str,
    service: FiscalService | None = None,
    usuario: str = "",
    arquivo: str = "",
    reference_date: str | None = None,
) -> ResultadoRegras:
    """Uma regra por combinação única de NCM + CEST + dados fiscais.

    Com `service`, cada produto é validado contra a base oficial (NCM/CEST/NCM×CEST):
    NCM/CEST inexistentes rejeitam a linha; demais situações (divergência, múltiplos CESTs)
    são apenas sinalizadas — o CEST nunca é preenchido automaticamente."""
    if regime not in REGIMES:
        raise ValueError(f"Regime inválido: {regime}")

    if _COL_NCM not in df.columns:
        raise ValueError("Coluna NCM não encontrada na planilha do cliente")

    result = ResultadoRegras(base_fiscal_disponivel=service is not None)
    totvs_ativo = service is not None and service.has_totvs_catalog()
    vistos: dict[RegraFiscal, None] = {}

    for idx, row in df.iterrows():
        produto = _get(row, "NOME PRODUTO")
        if not _get(row, _COL_NCM) and not produto:
            continue
        result.total_produtos += 1
        linha = int(idx) + _FIRST_DATA_ROW
        regra, errors = _parse_row(row, linha, regime)

        campos_com_erro = {e.field for e in errors}
        if service is not None and not campos_com_erro & {"NCM", "CEST"}:
            validacao = service.validate_ncm_cest(_get(row, _COL_NCM), _get(row, _COL_CEST), reference_date)
            service.audit(validacao, usuario=usuario, arquivo=arquivo, produto=produto)
            result.analise.append(_analise_item(linha, produto, validacao))
            campo = _STATUS_REJEITA.get(validacao.status)
            if campo:
                errors.append(ValidationError(
                    row=linha, field=campo,
                    value=validacao.ncm if campo == "NCM" else validacao.cest,
                    reason=" ".join(m for m in validacao.messages if "Verifique" not in m),
                ))

        if regra and not errors and totvs_ativo:
            regra, errors_totvs, correcao = _aplicar_catalogo_totvs(service, linha, regra, reference_date)
            errors.extend(errors_totvs)
            if correcao:
                result.correcoes.append(correcao)

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


_STATUS_LABEL: dict[str, str] = {
    "VALID":             "NCM e CEST válidos e relacionados",
    "NO_CEST":           "NCM válido, sem CEST relacionado",
    "INVALID_NCM":       "NCM inválido",
    "INVALID_CEST":      "CEST inválido",
    "NCM_CEST_MISMATCH": "CEST sem relação com o NCM",
    "MULTIPLE_CEST":     "Múltiplos CESTs possíveis",
    "NEEDS_REVIEW":      "Necessita revisão",
}


def write_analise_report(analise: list[dict[str, Any]], output_path: Path) -> None:
    """Relatório por produto da validação NCM/CEST contra a base oficial."""
    rows = [
        {
            "Linha (cliente)":        a["linha"],
            "Produto":                a["produto"],
            "NCM":                    a["ncm_formatted"],
            "Descrição NCM (oficial)": a["ncm_description"] or "",
            "CEST informado":         a["cest_formatted"],
            "Descrição CEST (oficial)": a["cest_description"] or "",
            "Status":                 a["status"],
            "Situação":               _STATUS_LABEL.get(a["status"], a["status"]),
            "CESTs relacionados ao NCM": "\n".join(
                f"{c['code_formatted']} - {c['description']}" for c in a["related_cests"]
            ),
            "Observações":            " ".join(a["messages"]),
            "Base consultada":        " / ".join(a["versions"].values()),
        }
        for a in analise
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Analise Fiscal")
        ws = writer.sheets["Analise Fiscal"]
        for col, width in zip("ABCDEFGHIJK", (10, 35, 12, 50, 12, 40, 18, 30, 60, 60, 40)):
            ws.column_dimensions[col].width = width
        ws.freeze_panes = "A2"
