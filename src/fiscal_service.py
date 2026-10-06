# -*- coding: utf-8 -*-
"""Serviço de consulta/validação fiscal (NCM, CEST, NCM×CEST) sobre a base versionada.

Princípio: o serviço informa CESTs RELACIONADOS ao NCM, nunca determina um CEST automaticamente.
"""
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from fiscal_codes import format_cest, format_ncm, normalize_cest, normalize_ncm

logger = logging.getLogger(__name__)


class StatusFiscal(str, Enum):
    VALID = "VALID"
    INVALID_NCM = "INVALID_NCM"
    INVALID_CEST = "INVALID_CEST"
    NCM_CEST_MISMATCH = "NCM_CEST_MISMATCH"
    MULTIPLE_CEST = "MULTIPLE_CEST"
    NO_CEST = "NO_CEST"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class NcmInfo:
    code: str
    description: str
    start: str | None
    end: str | None
    source: str
    version: str


@dataclass(frozen=True)
class CestInfo:
    code: str
    description: str
    segment: str
    start: str | None
    end: str | None
    source: str
    version: str


@dataclass
class NcmCestValidation:
    status: StatusFiscal
    ncm: str
    cest: str
    reference_date: str
    ncm_valid: bool = False
    ncm_description: str | None = None
    cest_valid: bool | None = None
    cest_description: str | None = None
    related_cests: list[CestInfo] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["ncm_formatted"] = format_ncm(self.ncm)
        data["cest_formatted"] = format_cest(self.cest)
        for c in data["related_cests"]:
            c["code_formatted"] = format_cest(c["code"])
        return data


def _ref(reference_date: date | str | None) -> str:
    if reference_date is None:
        return date.today().isoformat()
    if isinstance(reference_date, date):
        return reference_date.isoformat()
    return date.fromisoformat(reference_date).isoformat()


# Linha vigente na data: dentre as versões da mesma chave, vale a da importação mais recente.
_VIGENTE = """
    (inicio_vigencia IS NULL OR inicio_vigencia <= :d)
    AND (fim_vigencia IS NULL OR fim_vigencia >= :d)
"""


class FiscalService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ consultas

    def get_ncm(self, ncm: str, reference_date: date | str | None = None) -> NcmInfo | None:
        code = normalize_ncm(ncm)
        if not code:
            return None
        row = self._conn.execute(
            f"SELECT * FROM ncm WHERE codigo = :c AND {_VIGENTE} "
            "ORDER BY importacao_id DESC, inicio_vigencia IS NULL, inicio_vigencia DESC LIMIT 1",
            {"c": code, "d": _ref(reference_date)},
        ).fetchone()
        return NcmInfo(row["codigo"], row["descricao"], row["inicio_vigencia"], row["fim_vigencia"],
                       row["fonte"], row["versao"]) if row else None

    def get_cest(self, cest: str, reference_date: date | str | None = None) -> CestInfo | None:
        code = normalize_cest(cest)
        if not code:
            return None
        row = self._conn.execute(
            f"SELECT * FROM cest WHERE codigo = :c AND {_VIGENTE} "
            "ORDER BY importacao_id DESC, inicio_vigencia IS NULL, inicio_vigencia DESC LIMIT 1",
            {"c": code, "d": _ref(reference_date)},
        ).fetchone()
        return self._cest_info(row) if row else None

    def get_cests_by_ncm(self, ncm: str, reference_date: date | str | None = None) -> list[CestInfo]:
        """CESTs cujo item do Convênio abrange o NCM (NCM completo ou prefixo), vigentes na data."""
        code = normalize_ncm(ncm)
        if not code:
            return []
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT c.* FROM cest c
            JOIN ncm_cest nc ON nc.cest_id = c.id
            WHERE :n LIKE nc.ncm_prefixo || '%' AND {_VIGENTE.replace('inicio_', 'c.inicio_').replace('fim_', 'c.fim_')}
            ORDER BY c.codigo, c.importacao_id DESC, c.inicio_vigencia IS NULL, c.inicio_vigencia DESC
            """,
            {"n": code, "d": _ref(reference_date)},
        ).fetchall()
        por_codigo: dict[str, CestInfo] = {}
        for row in rows:
            por_codigo.setdefault(row["codigo"], self._cest_info(row))
        return list(por_codigo.values())

    def has_data(self) -> bool:
        tipos = {r["tipo"] for r in self._conn.execute("SELECT DISTINCT tipo FROM importacao")}
        return {"NCM", "CEST"} <= tipos

    # ------------------------------------------------------------------ validações

    def validate_ncm(self, ncm: str, reference_date: date | str | None = None) -> dict[str, Any]:
        info = self.get_ncm(ncm, reference_date)
        cests = self.get_cests_by_ncm(ncm, reference_date) if info else []
        return {
            "ncm": normalize_ncm(ncm) or str(ncm),
            "valid": info is not None,
            "description": info.description if info else None,
            "cests": [{"code": c.code, "description": c.description} for c in cests],
        }

    def validate_cest(self, cest: str, reference_date: date | str | None = None) -> dict[str, Any]:
        info = self.get_cest(cest, reference_date)
        return {
            "cest": normalize_cest(cest) or str(cest),
            "valid": info is not None,
            "description": info.description if info else None,
        }

    def validate_ncm_cest(
        self, ncm: str, cest: str | None = None, reference_date: date | str | None = None
    ) -> NcmCestValidation:
        ref = _ref(reference_date)
        ncm_code = normalize_ncm(ncm)
        cest_raw = str(cest or "").strip()
        cest_code = normalize_cest(cest_raw) if cest_raw else ""
        result = NcmCestValidation(StatusFiscal.NEEDS_REVIEW, ncm_code or str(ncm or ""), cest_code or cest_raw, ref)

        ncm_info = self.get_ncm(ncm_code, ref) if ncm_code else None
        if ncm_info is None:
            result.status = StatusFiscal.INVALID_NCM
            result.messages.append(
                "NCM inválido: formato incorreto (deve ter 8 dígitos)." if not ncm_code
                else f"NCM inválido: não encontrado na tabela oficial vigente em {ref}."
            )
            result.messages.append("Verifique o cadastro fiscal do produto.")
            return result

        result.ncm_valid = True
        result.ncm_description = ncm_info.description
        result.versions["ncm"] = f"{ncm_info.source} | {ncm_info.version}"
        result.related_cests = self.get_cests_by_ncm(ncm_code, ref)
        relacionados = {c.code for c in result.related_cests}
        result.messages.append("NCM válido.")

        if cest_raw:
            cest_info = self.get_cest(cest_code, ref) if cest_code else None
            if cest_info is None:
                result.status = StatusFiscal.INVALID_CEST
                result.cest_valid = False
                result.messages.append(
                    "CEST inválido: formato incorreto (deve ter 7 dígitos)." if not cest_code
                    else f"CEST inválido: não encontrado no Convênio ICMS 142/18 vigente em {ref}."
                )
                return result

            result.cest_valid = True
            result.cest_description = cest_info.description
            result.versions["cest"] = f"{cest_info.source} | {cest_info.version}"
            if cest_code in relacionados:
                result.status = StatusFiscal.VALID
                result.messages.append("CEST válido. Relação NCM × CEST encontrada.")
                if len(relacionados) > 1:
                    result.messages.append(
                        f"Atenção: existem {len(relacionados)} CESTs relacionados a este NCM; "
                        "confirme se o informado corresponde ao enquadramento da mercadoria."
                    )
            else:
                result.status = StatusFiscal.NCM_CEST_MISMATCH
                result.messages.append(
                    "CEST válido isoladamente, porém não foi encontrada relação entre este NCM e CEST."
                )
            return result

        if not relacionados:
            result.status = StatusFiscal.NO_CEST
            result.messages.append("Não foram encontrados CESTs relacionados.")
        elif len(relacionados) == 1:
            result.status = StatusFiscal.NEEDS_REVIEW
            result.messages.append(
                "CEST não informado. Foi encontrada relação entre o NCM e 1 CEST; a aplicação depende "
                "do enquadramento da mercadoria (substituição tributária). Não aplicado automaticamente."
            )
        else:
            result.status = StatusFiscal.MULTIPLE_CEST
            result.messages.append(
                f"CEST não informado. Foram encontrados {len(relacionados)} CESTs possíveis. "
                "Não determinado automaticamente; necessário analisar descrição/enquadramento da mercadoria."
            )
        return result

    # ------------------------------------------------------------------ auditoria

    def audit(self, validation: NcmCestValidation, usuario: str, arquivo: str, produto: str = "") -> None:
        logger.info(json.dumps({
            "event": "validacao_fiscal",
            "data": validation.reference_date,
            "usuario": usuario,
            "arquivo": arquivo,
            "produto": produto,
            "ncm": validation.ncm,
            "cest": validation.cest,
            "resultado": validation.status.value,
            "versoes": validation.versions,
        }, ensure_ascii=False))

    @staticmethod
    def _cest_info(row: sqlite3.Row) -> CestInfo:
        return CestInfo(row["codigo"], row["descricao"], row["segmento"], row["inicio_vigencia"],
                        row["fim_vigencia"], row["fonte"], row["versao"])
