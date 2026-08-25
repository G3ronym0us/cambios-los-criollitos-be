"""
Borra de la bitácora del analizador lo que ya no sirve.

La tabla guarda texto crudo de clientes, así que no puede crecer para siempre. Las filas
etiquetadas se respetan por defecto —son el dataset ya revisado y su valor no caduca—;
una fila cruda y vieja, en cambio, es solo un mensaje ajeno guardado sin haber servido.

    python -m app.cli.purge_analysis_log --days 90 --dry-run
    python -m app.cli.purge_analysis_log --days 90
    python -m app.cli.purge_analysis_log --days 365 --include-labeled
"""

import argparse

from app.database.connection import SessionLocal
from app.services.analysis_log_service import AnalysisLogService


def run(days: int = 90, include_labeled: bool = False, dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        count = AnalysisLogService(db).purge(
            days=days, include_labeled=include_labeled, dry_run=dry_run
        )
        prefix = "(dry-run) " if dry_run else ""
        scope = "todas" if include_labeled else "solo las no etiquetadas"
        print(f"{prefix}{count} filas de más de {days} días borradas ({scope})")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Purga la bitácora del analizador")
    parser.add_argument("--days", type=int, default=90, help="antigüedad mínima (default 90)")
    parser.add_argument(
        "--include-labeled", action="store_true", help="borra también las etiquetadas"
    )
    parser.add_argument("--dry-run", action="store_true", help="no borra, solo cuenta")
    args = parser.parse_args()
    run(days=args.days, include_labeled=args.include_labeled, dry_run=args.dry_run)
