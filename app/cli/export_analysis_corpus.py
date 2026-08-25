"""
Convierte la bitácora del analizador en un JSONL etiquetado.

La bitácora (`whatsapp_message_analyses`) guarda lo que el analizador LEYÓ; la etiqueta —si
leyó bien— aparece después, en lo que pasó con la operación. Este CLI hace ese join y le
pone un veredicto a cada fila. No escribe nada: la derivación es pura y se puede rehacer
cuando cambie el criterio.

El puente es `wa_message_id` → `whatsapp_operation_messages` → operación, el mismo índice que
el bot ya usa para el revoke y las correcciones.

Veredictos:
  correct        op COMPLETED — la lectura sostuvo una operación que se cerró
  superseded     op cancelada por `superseded_by_correction` — leyó mal; `replacement` trae
                 lo que el operador puso en su lugar, que es la etiqueta buena
  cancelled      op cancelada por cualquier otro motivo — ambiguo, mirar a mano
  open           op todavía QUOTED/PENDING — aún no dice nada, reexportar más tarde
  ghost_quote    dedujo QUOTE con monto y NO nació ninguna operación — el caso caro
  no_action      no dedujo cotización y no nació operación — el caso aburrido y correcto

    python -m app.cli.export_analysis_corpus --days 30 --out corpus.jsonl
    python -m app.cli.export_analysis_corpus --stats            # solo el recuento
    python -m app.cli.export_analysis_corpus --verdict ghost_quote,superseded
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database.connection import SessionLocal
from app.models.whatsapp_message_analysis import WhatsAppMessageAnalysis
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_operation_message import WhatsAppOperationMessage

SUPERSEDED_MARK = "[cancel] superseded_by_correction"
# Una corrección cancela la cotización vieja justo después de crear la nueva. La ventana es
# amplia a propósito: de más agarra la op equivocada del mismo cliente, de menos se pierde el
# reemplazo y la fila queda sin su etiqueta buena.
REPLACEMENT_WINDOW = timedelta(minutes=5)


def _op_summary(op: WhatsAppOperation) -> dict[str, Any]:
    pair = op.currency_pair
    return {
        "operation_uuid": str(op.uuid),
        "status": op.status.value if op.status else None,
        "pair_symbol": pair.pair_symbol if pair else None,
        "from_amount": op.from_amount,
        "to_amount": op.to_amount,
        "amount_side": op.amount_side.value if op.amount_side else None,
        "bcv_usd": op.bcv_usd,
        "quoted_at": op.quoted_at.isoformat() if op.quoted_at else None,
    }


def _find_replacement(db, op: WhatsAppOperation) -> Optional[WhatsAppOperation]:
    """
    La cotización que reemplazó a `op`. No hay un vínculo explícito en la base: se busca la
    siguiente operación del mismo cliente dentro de la ventana. Es una inferencia, y por eso
    el JSONL la marca aparte en vez de mezclarla con lo que sí está registrado.
    """
    if op.cancelled_at is None:
        return None
    return (
        db.query(WhatsAppOperation)
        .filter(
            WhatsAppOperation.client_id == op.client_id,
            WhatsAppOperation.id != op.id,
            WhatsAppOperation.quoted_at >= op.cancelled_at - REPLACEMENT_WINDOW,
            WhatsAppOperation.quoted_at <= op.cancelled_at + REPLACEMENT_WINDOW,
        )
        .order_by(WhatsAppOperation.quoted_at.asc())
        .first()
    )


def _verdict(row: WhatsAppMessageAnalysis, op: Optional[WhatsAppOperation]) -> str:
    if op is None:
        output = row.output or {}
        deduced_quote = output.get("intent") == "QUOTE" and output.get("amount") is not None
        return "ghost_quote" if deduced_quote else "no_action"
    if op.status == WhatsAppOperationStatus.COMPLETED:
        return "correct"
    if op.status == WhatsAppOperationStatus.CANCELLED:
        return "superseded" if SUPERSEDED_MARK in (op.notes or "") else "cancelled"
    return "open"


def run(
    days: int = 30,
    out: Optional[str] = None,
    verdicts: Optional[set[str]] = None,
    stats_only: bool = False,
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = (
            db.query(WhatsAppMessageAnalysis)
            .filter(WhatsAppMessageAnalysis.created_at >= cutoff)
            .order_by(WhatsAppMessageAnalysis.created_at.asc())
            .all()
        )

        # Un solo golpe para todos los vínculos mensaje→operación de la ventana.
        message_ids = [r.wa_message_id for r in rows if r.wa_message_id]
        links = {}
        if message_ids:
            links = {
                link.wa_message_id: link.whatsapp_operation_id
                for link in db.query(WhatsAppOperationMessage)
                .filter(WhatsAppOperationMessage.wa_message_id.in_(message_ids))
                .all()
            }

        counts: Counter = Counter()
        sink = open(out, "w", encoding="utf-8") if out and not stats_only else None
        try:
            for row in rows:
                op_id = links.get(row.wa_message_id) if row.wa_message_id else None
                op = (
                    db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op_id).first()
                    if op_id
                    else None
                )
                verdict = _verdict(row, op)
                counts[verdict] += 1
                if verdicts and verdict not in verdicts:
                    continue

                record = {
                    "uuid": str(row.uuid),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "client_phone": row.client_phone,
                    "messages": row.messages,
                    "context": row.context,
                    "default_pair_symbol": row.default_pair_symbol,
                    "analyzer": row.analyzer,
                    "output": row.output,
                    "verdict": verdict,
                    "operation": _op_summary(op) if op else None,
                    "replacement": None,
                    # La corrección a mano manda sobre lo derivado: si alguien revisó la
                    # fila, su veredicto es el que vale al armar el dataset.
                    "label": row.label,
                    "label_source": row.label_source,
                }
                if verdict == "superseded" and op is not None:
                    replacement = _find_replacement(db, op)
                    if replacement is not None:
                        record["replacement"] = _op_summary(replacement)

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
            print(f"  {verdict:<14} {count:>6}  ({share:.1f}%)", file=sys.stderr)
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
    parser.add_argument("--stats", action="store_true", help="solo el recuento, sin exportar")
    args = parser.parse_args()
    run(
        days=args.days,
        out=args.out,
        verdicts={v.strip() for v in args.verdict.split(",")} if args.verdict else None,
        stats_only=args.stats,
    )
