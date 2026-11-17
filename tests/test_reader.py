# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reader import read_client_file  # noqa: E402


def _planilha_totvs(path: Path, ocultas: set[int]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "1. Produtos de Venda"
    ws.append(["INFORMAÇÕES PRINCIPAIS"])
    ws.append(["CÓDIGO PRODUTO VENDA", "NOME PRODUTO"])
    for i, nome in enumerate(["AGUA", "SUCO", "PICOLE", "CAFE"], start=3):
        ws.append([None, nome])
        ws.row_dimensions[i].hidden = i in ocultas
    wb.save(path)


def test_linhas_ocultas_ou_filtradas_sao_ignoradas(tmp_path: Path) -> None:
    path = tmp_path / "cliente.xlsx"
    _planilha_totvs(path, ocultas={5, 6})
    df = read_client_file(path)
    assert list(df["NOME PRODUTO"]) == ["AGUA", "SUCO"]
    assert list(df.index) == [0, 1]  # índice original: linha do relatório = índice + 3 = linha do Excel


def test_indice_preservado_quando_oculta_no_meio(tmp_path: Path) -> None:
    path = tmp_path / "cliente.xlsx"
    _planilha_totvs(path, ocultas={3})
    df = read_client_file(path)
    assert list(df["NOME PRODUTO"]) == ["SUCO", "PICOLE", "CAFE"]
    assert list(df.index) == [1, 2, 3]


def test_sem_linhas_ocultas_le_tudo(tmp_path: Path) -> None:
    path = tmp_path / "cliente.xlsx"
    _planilha_totvs(path, ocultas=set())
    assert len(read_client_file(path)) == 4
