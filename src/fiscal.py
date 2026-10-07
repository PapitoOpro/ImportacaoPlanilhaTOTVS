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
    avisos: list[dict[str, Any]] = field(default_factory=list)
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


# NCM nunca bloqueia: o que o TOTVS não aceitar vira aviso (ver _aviso_ncm_totvs).
_STATUS_REJEITA = {StatusFiscal.INVALID_CEST: "CEST"}


def _analise_item(linha: int, produto: str, v: NcmCestValidation) -> dict[str, Any]:
    return {"linha": linha, "produto": produto, **v.to_dict()}


_STOPWORDS = {"DE", "DA", "DO", "DAS", "DOS", "EM", "COM", "SEM", "E", "OU", "PARA", "A", "O", "AS", "OS",
              "EXCETO", "INFERIOR", "IGUAL", "CONTEUDO", "EMBALAGEM", "EMBALAGENS", "OUTROS", "OUTRAS", "DEMAIS",
              # embalagem/volume: descrevem a apresentação, não a mercadoria
              "LATA", "VIDRO", "GARRAFA", "PET", "DESCARTAVEL", "RETORNAVEL", "CAIXA", "PACOTE", "UNIDADE",
              "LITRO", "LONG", "NECK", "PRONTA", "PRONTAS"}
_CST_ST = {"10", "30", "60", "70"}               # CST com ICMS-ST (90 = "outras", ambíguo: não exige)
_CSOSN_ST = {"201", "202", "203", "500"}         # CSOSN com ICMS-ST (900 = "outros", ambíguo: não exige)


def _tokens(texto: str) -> set[str]:
    """Palavras relevantes (sem acento/plural/números) para comparar produto × descrição do CEST."""
    palavras = re.findall(r"[A-Z]+", _norm_text(texto))
    return {p.rstrip("S") for p in palavras if len(p) > 2 and p not in _STOPWORDS}


def _score(service: FiscalService, alvo: set[str], codigo: str) -> int:
    info = service.get_cest(codigo)
    return len(alvo & _tokens(info.description)) if info else 0


def _escolher_cest(service: FiscalService, produto: str, candidatos: list[str]) -> tuple[str | None, list[str]]:
    """(escolhido, opções ordenadas por aderência ao nome do produto). Escolhe se há 1 candidato ou se a
    descrição do produto destaca um deles com folga; senão None e o chamador rejeita listando as opções."""
    if len(candidatos) == 1:
        return candidatos[0], candidatos
    alvo = _tokens(produto)
    ordenados = sorted(candidatos, key=lambda c: (-_score(service, alvo, c), c))
    if len(ordenados) > 1 and _score(service, alvo, ordenados[0]) > _score(service, alvo, ordenados[1]):
        return ordenados[0], ordenados
    return None, ordenados


def _opcoes(service: FiscalService, codigos: list[str]) -> str:
    def rotulo(c: str) -> str:
        info = service.get_cest(c)
        if not info:
            return c
        desc = info.description if len(info.description) <= 45 else info.description[:45] + "..."
        return f"{c} ({desc})"
    return "; ".join(rotulo(c) for c in codigos[:6])


def _exige_cest(regra: RegraFiscal, regime: str) -> bool:
    """Operação com ICMS-ST (tributo S ou CST/CSOSN de ST): o CEST é obrigatório (Rejeição 806)."""
    if regra.tributo == "S":
        return True
    if regime in _SIMPLES:
        return regra.cst_csosn[-3:] in _CSOSN_ST
    return regra.cst_csosn[-2:] in _CST_ST


def _candidatos_cest(service: FiscalService, ncm: str, ref: str | None, usar_totvs: bool) -> list[str]:
    if usar_totvs:
        return service.totvs_cest_candidates(ncm, ref)
    return [c.code for c in service.get_cests_by_ncm(ncm, ref)]


def _aviso_ncm_totvs(service: FiscalService, linha: int, produto: str, regra: RegraFiscal) -> dict[str, Any] | None:
    """NCM que o TOTVS não aceita (fora da tabela MDIC do sistema ou recusado numa importação real).
    Só avisa: a linha continua na planilha, mas a importação vai acusar erro até o NCM ser cadastrado."""
    if service.in_totvs_catalog("NCM", regra.ncm):
        return None
    return {"linha": linha, "produto": produto, "campo": "NCM", "valor": regra.ncm,
            "motivo": "NCM não aceito pelo TOTVS (fora da tabela MDIC do sistema ou recusado em importação anterior): "
                      "vai dar erro na importação. Cadastre o NCM no TOTVS antes de importar."}


def _aplicar_catalogo_totvs(
    service: FiscalService, linha: int, produto: str, regra: RegraFiscal, reference_date: str | None
) -> tuple[RegraFiscal, list[ValidationError], dict[str, Any] | None]:
    """NCM/CEST válidos oficialmente, mas ausentes (ou inativos) no cadastro do TOTVS.

    CEST ausente: troca se houver 1 CEST oficial do NCM aceito pelo TOTVS ou se a descrição do
    produto destaca um deles; senão rejeita e lista as opções. O NCM nunca é barrado aqui."""
    if not regra.cest or service.in_totvs_catalog("CEST", regra.cest):
        return regra, [], None

    escolhido, ordenados = _escolher_cest(service, produto, service.totvs_cest_candidates(regra.ncm, reference_date))
    if escolhido:
        criterio = "único" if len(ordenados) == 1 else "mais aderente ao nome do produto"
        correcao = {"linha": linha, "campo": "CEST", "de": regra.cest, "para": escolhido,
                    "motivo": f"CEST não cadastrado no TOTVS; trocado pelo CEST do NCM aceito pelo TOTVS ({criterio})"}
        return replace(regra, cest=escolhido), [], correcao

    detalhe = (f" Opções aceitas pelo TOTVS: {_opcoes(service, ordenados)}." if ordenados
               else " Nenhum CEST oficial do NCM está cadastrado no TOTVS.")
    return regra, [ValidationError(row=linha, field="CEST", value=regra.cest,
                                   reason=f"CEST não cadastrado no TOTVS.{detalhe}")], None


def _aplicar_obrigatoriedade(
    service: FiscalService, linha: int, produto: str, regra: RegraFiscal, regime: str,
    reference_date: str | None, usar_totvs: bool,
) -> tuple[RegraFiscal, list[ValidationError], dict[str, Any] | None]:
    """CEST vazio: só é exigido se o NCM consta no Convênio 142/18 E a operação é de ICMS-ST.
    Fora disso fica em branco (ex.: alimento preparado, venda sem ST)."""
    if regra.cest or not _exige_cest(regra, regime):
        return regra, [], None
    candidatos = _candidatos_cest(service, regra.ncm, reference_date, usar_totvs)
    if not candidatos:
        return regra, [], None  # NCM fora do Convênio (ou sem CEST aceito): não há o que preencher

    escolhido, ordenados = _escolher_cest(service, produto, candidatos)
    if escolhido:
        criterio = "único do NCM" if len(ordenados) == 1 else "mais aderente ao nome do produto"
        return replace(regra, cest=escolhido), [], {
            "linha": linha, "campo": "CEST", "de": "", "para": escolhido,
            "motivo": f"CEST obrigatório (operação com ICMS-ST) e não informado; preenchido com o CEST {criterio}",
        }
    return regra, [ValidationError(
        row=linha, field="CEST", value="",
        reason=f"CEST obrigatório (operação com ICMS-ST, Rejeição 806). Opções: {_opcoes(service, ordenados)}.",
    )], None


def build_regras(
    df: pd.DataFrame,
    regime: str,
    service: FiscalService | None = None,
    usuario: str = "",
    arquivo: str = "",
    reference_date: str | None = None,
    aplicar_sugestoes: bool = True,
    excluir_ncm_totvs: bool = False,
) -> ResultadoRegras:
    """Uma regra por combinação única de NCM + CEST + dados fiscais.

    Com `service`, cada produto é validado contra a base oficial (NCM/CEST/NCM×CEST):
    NCM/CEST inexistentes rejeitam a linha; demais situações (divergência, múltiplos CESTs)
    são apenas sinalizadas. CEST recusado pelo TOTVS ou obrigatório (ICMS-ST) é corrigido
    quando há escolha inequívoca; senão a linha é rejeitada com as opções."""
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
                    value=validacao.cest,
                    reason=" ".join(m for m in validacao.messages if "Verifique" not in m),
                ))

        if regra and not errors and service is not None:
            if totvs_ativo:
                aviso = _aviso_ncm_totvs(service, linha, produto, regra)
                if aviso and excluir_ncm_totvs:
                    errors.append(ValidationError(
                        row=linha, field="NCM", value=regra.ncm,
                        reason="NCM não aceito pelo TOTVS: produto separado da planilha para não derrubar a "
                               "importação. Cadastre o NCM no TOTVS e importe este produto à parte.",
                    ))
                elif aviso:
                    result.avisos.append(aviso)
            etapas = []
            if totvs_ativo and not errors:
                etapas.append(lambda r: _aplicar_catalogo_totvs(service, linha, produto, r, reference_date))
            if not errors:
                etapas.append(
                    lambda r: _aplicar_obrigatoriedade(service, linha, produto, r, regime, reference_date, totvs_ativo))
            for etapa in etapas:
                regra_etapa, errors_etapa, correcao = etapa(regra)
                errors.extend(errors_etapa)
                if correcao and not aplicar_sugestoes:
                    errors.append(ValidationError(
                        row=linha, field=correcao["campo"], value=correcao["de"],
                        reason=f"{correcao['motivo']}. Sugestão não aplicada: {correcao['para']}",
                    ))
                    break
                regra = regra_etapa
                if correcao:
                    result.correcoes.append(correcao)
                if errors_etapa:
                    break

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
