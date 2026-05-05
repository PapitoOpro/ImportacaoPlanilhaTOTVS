# -*- coding: utf-8 -*-
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import COLUMN_MAP, FIELD_FILL_DEFAULTS, FIELD_RULES, REQUIRED_FIELDS, TEMPLATE_COLUMNS, TEMPLATE_DEFAULTS
from reader import read_client_file
from transformer import transform
from validator import validate
from writer import write_error_report, write_output

_ROOT = Path(__file__).parent.parent
_TEMPLATE = _ROOT / "PlanilhaImportaçãoLojaComValidação.xlsm"


def run(input_path: Path, output_path: Path, error_path: Path) -> int:
    print(f"[1/4] Lendo: {input_path.name}")
    df = read_client_file(input_path)
    print(f"      {len(df)} produto(s) encontrado(s)")

    print("[2/4] Transformando...")
    df = transform(df, COLUMN_MAP, TEMPLATE_DEFAULTS, TEMPLATE_COLUMNS, FIELD_FILL_DEFAULTS)

    print("[3/4] Validando...")
    errors = validate(df, REQUIRED_FIELDS, FIELD_RULES)

    invalid_idx: set[int] = set()
    if errors:
        print(f"      {len(errors)} erro(s) encontrado(s)")
        write_error_report(errors, error_path)
        print(f"      Relatório: {error_path}")
        invalid_idx = {e.row - 3 for e in errors}
    else:
        print("      Sem erros")

    valid_df = df[~df.index.isin(invalid_idx)].reset_index(drop=True)
    print(f"      {len(valid_df)} linha(s) válida(s) para exportação")

    print(f"[4/4] Exportando: {output_path.name}")
    write_output(valid_df, output_path, template_path=_TEMPLATE)

    print(f"\n[OK] Concluido -> {output_path}")
    if errors:
        print(f"  Rejeitadas: {len(invalid_idx)} | Exportadas: {len(valid_df)}")

    return len(errors)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automação de importação de produtos — TOTVS Food 5.0"
    )
    parser.add_argument("input", help="Planilha do cliente (.xls/.xlsx/.csv)")
    parser.add_argument(
        "--output", "-o",
        default=str(_ROOT / "output" / "PlanilhaImportaçãoLoja.xlsm"),
        help="Arquivo de saída",
    )
    parser.add_argument(
        "--errors", "-e",
        default=str(_ROOT / "output" / "erros.xlsx"),
        help="Relatório de erros",
    )
    args = parser.parse_args()

    try:
        error_count = run(
            input_path=Path(args.input),
            output_path=Path(args.output),
            error_path=Path(args.errors),
        )
        sys.exit(1 if error_count else 0)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"ERRO inesperado: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
