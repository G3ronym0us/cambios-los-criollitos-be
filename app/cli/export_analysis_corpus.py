"""
Convierte la bitácora del analizador en un JSONL etiquetado.

La bitácora guarda lo que el analizador LEYÓ; la etiqueta —si leyó bien— aparece después, en
lo que pasó con la operación. La derivación vive en `AnalysisCorpusService`; este CLI solo la
recorre y la escribe.

Veredictos: ver `AnalysisCorpusService.VERDICTS`.

    python -m app.cli.export_analysis_corpus --days 30 --out corpus.jsonl
    python -m app.cli.export_analysis_corpus --stats            # solo el recuento
    python -m app.cli.export_analysis_corpus --verdict ghost_quote,superseded
    python -m app.cli.export_analysis_corpus --analyzer heuristic-v1-backfill
"""

import argparse
import json
import sys
from collections import Counter
from typing import Optional

from app.database.connection import SessionLocal
from app.services.analysis_corpus_service import VERDICTS, AnalysisCorpusService


def run(
    days: int = 30,
    out: Optional[str] = None,
    verdicts: Optional[set[str]] = None,
    analyzer: Optional[str] = None,
    stats_only: bool = False,
) -> None:
    db = SessionLocal()
    try:
        service = AnalysisCorpusService(db)
        rows = service.rows_since(days)
        if analyzer:
            rows = [r for r in rows if r.analyzer == analyzer]
        ops = service.operations_for(rows)

        counts: Counter = Counter()
        by_analyzer: Counter = Counter()
        sink = open(out, "w", encoding="utf-8") if out and not stats_only else None
        try:
            for row in rows:
                op = ops.get(row.wa_message_id) if row.wa_message_id else None
                record = service.enrich(row, op)
                counts[record["verdict"]] += 1
                by_analyzer[row.analyzer] += 1
                if verdicts and record["verdict"] not in verdicts:
                    continue
                line = json.dumps(record, ensure_ascii=False)
                if sink:
                    sink.write(line + "\n")
                elif not stats_only:
                    print(line)
        finally:
            if sink:
                sink.close()

        total = sum(counts.values())
        print(f"\n{total} análisis en los últimos {days} días", file=sys.stderr)
        for verdict, count in counts.most_common():
            share = (count / total * 100) if total else 0
            print(f"  {verdict:<14} {count:>6}  ({share:.1f}%)  {VERDICTS.get(verdict, '')}", file=sys.stderr)
        if len(by_analyzer) > 1:
            print("\npor analizador:", file=sys.stderr)
            for name, count in by_analyzer.most_common():
                print(f"  {name:<26} {count:>6}", file=sys.stderr)
        if out and not stats_only:
            print(f"\n→ {out}", file=sys.stderr)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exporta el corpus del analizador a JSONL")
    parser.add_argument("--days", type=int, default=30, help="ventana hacia atrás (default 30)")
    parser.add_argument("--out", default=None, help="archivo destino; sin él, va a stdout")
    parser.add_argument(
        "--verdict", default=None, help="exporta solo estos veredictos (separados por coma)"
    )
    parser.add_argument(
        "--analyzer", default=None, help="filtra por analizador (ej: heuristic-v1-backfill)"
    )
    parser.add_argument("--stats", action="store_true", help="solo el recuento, sin exportar")
    args = parser.parse_args()
    run(
        days=args.days,
        out=args.out,
        verdicts={v.strip() for v in args.verdict.split(",")} if args.verdict else None,
        analyzer=args.analyzer,
        stats_only=args.stats,
    )
