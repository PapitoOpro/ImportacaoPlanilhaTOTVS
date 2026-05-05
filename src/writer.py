# -*- coding: utf-8 -*-
from pathlib import Path

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

from validator import ValidationError


def write_output(df: pd.DataFrame, output_path: Path, template_path: Path | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if template_path and template_path.exists():
        wb = openpyxl.load_workbook(template_path, keep_vba=True)
        ws = wb["Produtos"]
        # Remove todas as linhas de dados, mantendo apenas o cabeçalho (linha 1)
        if ws.max_row > 1:
            ws.delete_rows(2, ws.max_row - 1)
        # Escreve os dados a partir da linha 2
        for row_data in dataframe_to_rows(df, index=False, header=False):
            ws.append(row_data)
        wb.save(output_path)
        return

    # Fallback: gera xlsx simples (sem VBA)
    output_path = output_path.with_suffix(".xlsx")
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Produtos")
        ws = writer.sheets["Produtos"]

        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = Font(bold=True, color="FFFFFF", size=10)
            cell.alignment = Alignment(horizontal="center")

        for col_idx, col in enumerate(ws.columns, 1):
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 40)

        ws.freeze_panes = "A2"


def write_error_report(errors: list[ValidationError], output_path: Path) -> None:
    if not errors:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    df_err = pd.DataFrame([
        {
            "Linha (cliente)": e.row,
            "Campo":           e.field,
            "Valor Informado": e.value,
            "Motivo do Erro":  e.reason,
        }
        for e in errors
    ])

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_err.to_excel(writer, index=False, sheet_name="Erros")
        ws = writer.sheets["Erros"]

        red = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
        yellow = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

        for cell in ws[1]:
            cell.fill = red
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center")

        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.fill = yellow

        for col_idx, col in enumerate(ws.columns, 1):
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 60)

        ws.freeze_panes = "A2"
