"""
Borra de la bitácora del analizador lo que ya no sirve, con dos plazos según la clase.

Lo que parece una operación dura más: es lo que el operador revisa cuando una cotización
sale mal. El chit-chat dura menos, pero no poco — son los ejemplos negativos del dataset, los
que enseñan al analizador cuándo callarse, y el backfill histórico no los tiene por
construcción. Las filas etiquetadas a mano no caducan nunca.

A 1,7 KB por fila y ~300 filas diarias la tabla crece ~15 MB al mes: el plazo corto no está
para ahorrar disco sino para no guardar conversación ajena más de lo necesario.

    python -m app.cli.purge_analysis_log --dry-run
    python -m app.cli.purge_analysis_log
    python -m app.cli.purge_analysis_log --transactional-days 7 --personal-days 1
"""

import argparse

from app.database.connection import SessionLocal
from app.services.analysis_log_service import AnalysisLogService


def run(
    transactional_days: int = 90,
    personal_days: int = 30,
    include_labeled: bool = False,
    dry_run: bool = False,
) -> None:
    db = SessionLocal()
    try:
        counts = AnalysisLogService(db).purge(
            transactional_days=transactional_days,
            personal_days=personal_days,
            include_labeled=include_labeled,
            dry_run=dry_run,
        )
        prefix = "(dry-run) " if dry_run else ""
        scope = "incluyendo etiquetadas" if include_labeled else "sin tocar las etiquetadas"
        print(f"{prefix}borradas ({scope}):")
        print(f"  transaccional (>{transactional_days}d): {counts['transaccional']}")
        print(f"  personal      (>{personal_days}d): {counts['personal']}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purga la bitácora del analizador")
    parser.add_argument(
        "--transactional-days", type=int, default=90, help="plazo de lo transaccional (90)"
    )
    parser.add_argument(
        "--personal-days", type=int, default=30, help="plazo del chit-chat (30)"
    )
    parser.add_argument(
        "--include-labeled", action="store_true", help="borra también las etiquetadas"
    )
    parser.add_argument("--dry-run", action="store_true", help="no borra, solo cuenta")
    args = parser.parse_args()
    run(
        transactional_days=args.transactional_days,
        personal_days=args.personal_days,
        include_labeled=args.include_labeled,
        dry_run=args.dry_run,
    )
