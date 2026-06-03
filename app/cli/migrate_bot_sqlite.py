"""
Migración one-shot de datos del bot WhatsApp (SQLite `bot.db`) al backend
(Postgres). Es el paso de datos del cutover G3R-16: se corre UNA vez, con las
tablas whatsapp_* recién creadas (vacías) por alembic y ANTES de encender
USE_BACKEND_FOR_QUOTES=true en el bot.

Qué hace (inserción directa, NO usa WhatsAppQuoteService):
  - clients (+ tracked/blocked/flags/preferences) -> whatsapp_clients
  - operations                                    -> whatsapp_operations
  - incoming_payments                             -> whatsapp_incoming_payments
  - outgoing_payments                             -> whatsapp_outgoing_payments

Decisiones de diseño (ver memoria migracion-bot-fase1/2):
  - Inserción directa preservando estado. Las ops COMPLETED se migran con
    transaction_id=NULL: NO se crean Transactions retroactivas (eso duplicaría
    profit splits / movimientos de fondos del backend). El historial contable
    del bot NO entra a la contabilidad del backend por diseño.
  - El `operations.id` (uuid TEXT de SQLite) se preserva en `legacy_sqlite_id`.
    El `uuid` nuevo del backend se autogenera.
  - Los pagos usan id entero NO compartido -> se reconstruyen mapeos old->new
    para `whatsapp_operation_id` y `source_payment_id`.
  - `amount_side` no existe en SQLite: se asume SEND (no afecta valores; los
    from_amount/to_amount se copian tal cual, ya venían calculados).
  - Pares sin CurrencyPair registrado (p.ej. VES-VES basura) -> se omiten y loguean.

Uso:
    # dry-run (valida y cuenta, hace rollback):
    python -m app.cli.migrate_bot_sqlite --sqlite-path /tmp/bot.db
    # aplicar:
    python -m app.cli.migrate_bot_sqlite --sqlite-path /tmp/bot.db --commit
    # re-correr desde cero (borra whatsapp_* antes; sólo pre-cutover):
    python -m app.cli.migrate_bot_sqlite --sqlite-path /tmp/bot.db --wipe --commit
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from app.database.connection import SessionLocal
from app.models.currency_pair import CurrencyPair
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment

QUOTE_TTL_MINUTES = 30


# ---------- helpers ----------

def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """SQLite guarda fechas como TEXT ISO. Devuelve datetime aware UTC o None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # admite "2026-05-20 14:30:00" y "2026-05-20T14:30:00.000+00:00"
    s = s.replace(" ", "T", 1) if "T" not in s and " " in s else s
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # último recurso: sólo fecha
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


STATUS_MAP = {
    "QUOTED": WhatsAppOperationStatus.QUOTED,
    "PENDING": WhatsAppOperationStatus.PENDING,
    "COMPLETED": WhatsAppOperationStatus.COMPLETED,
    "CANCELLED": WhatsAppOperationStatus.CANCELLED,
    "CANCELED": WhatsAppOperationStatus.CANCELLED,
}
DELIVERY_MAP = {
    "PENDING": WhatsAppDeliveryStatus.PENDING,
    "RECEIVED": WhatsAppDeliveryStatus.RECEIVED,
}


def run(sqlite_path: str, commit: bool, wipe: bool) -> int:
    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    db = SessionLocal()

    stats = {
        "clients": 0, "ops": 0, "ops_skipped_no_pair": 0,
        "incoming": 0, "outgoing": 0,
        "pay_op_unlinked": 0, "src_unlinked": 0,
    }
    unresolved_pairs: dict[str, int] = {}

    try:
        # Sanity: las tablas destino existen (alembic ya corrió)
        for t in ("whatsapp_clients", "whatsapp_operations",
                  "whatsapp_incoming_payments", "whatsapp_outgoing_payments"):
            exists = db.execute(
                text("SELECT to_regclass(:t)"), {"t": t}
            ).scalar()
            if exists is None:
                print(f"❌ Falta la tabla destino {t}. ¿Corriste 'alembic upgrade head'?")
                return 2

        if wipe:
            print("⚠️  --wipe: borrando whatsapp_* (orden FK)…")
            for t in ("whatsapp_outgoing_payments", "whatsapp_incoming_payments",
                      "whatsapp_operations", "whatsapp_clients"):
                db.execute(text(f"DELETE FROM {t}"))
            db.flush()

        # ---- caché de currency_pairs por símbolo ----
        pair_by_symbol: dict[str, CurrencyPair] = {}
        for cp in db.query(CurrencyPair).all():
            pair_by_symbol[cp.pair_symbol.upper()] = cp

        def resolve_pair(frm: str, to: str) -> Optional[CurrencyPair]:
            return (pair_by_symbol.get(f"{frm}-{to}".upper())
                    or pair_by_symbol.get(f"{to}-{frm}".upper()))

        # ---------- 1) clientes ----------
        # recolectar nombres y flags desde las tablas auxiliares
        names: dict[str, str] = {}
        if table_exists(con, "clients"):
            for r in con.execute("SELECT phone, name FROM clients"):
                if r["phone"]:
                    names[r["phone"]] = r["name"]

        tracked = {r["phone"] for r in con.execute("SELECT phone FROM tracked_clients")} \
            if table_exists(con, "tracked_clients") else set()
        blocked = {r["phone"] for r in con.execute("SELECT phone FROM blocked_clients")} \
            if table_exists(con, "blocked_clients") else set()
        usdt_auth = set()
        if table_exists(con, "client_flags"):
            for r in con.execute("SELECT phone, flag FROM client_flags"):
                if r["flag"] and "usdt" in str(r["flag"]).lower():
                    usdt_auth.add(r["phone"])
        prefs: dict[str, tuple] = {}
        if table_exists(con, "client_preferences"):
            for r in con.execute(
                "SELECT phone, default_from_currency, default_to_currency FROM client_preferences"
            ):
                prefs[r["phone"]] = (r["default_from_currency"], r["default_to_currency"])

        # universo de teléfonos: clients + flags + los de operations
        op_phones = {r["client_phone"] for r in con.execute(
            "SELECT DISTINCT client_phone FROM operations") if r["client_phone"]}
        all_phones = set(names) | tracked | blocked | usdt_auth | set(prefs) | op_phones

        client_id_by_phone: dict[str, int] = {}
        for phone in sorted(all_phones):
            if not phone:
                continue
            if len(phone) > 32:
                print(f"   · phone >32 chars, se omite como cliente (los pagos lo guardan igual): {phone}")
                continue
            wc = db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
            if wc is None:
                wc = WhatsAppClient(phone=phone, display_name=names.get(phone))
                db.add(wc)
            else:
                if names.get(phone) and not wc.display_name:
                    wc.display_name = names[phone]
            wc.is_tracked = phone in tracked
            wc.is_blocked = phone in blocked
            wc.is_usdt_authorized = phone in usdt_auth
            pref = prefs.get(phone)
            if pref and pref[0] and pref[1]:
                cp = resolve_pair(pref[0], pref[1])
                if cp:
                    wc.preferred_pair_id = cp.id
            db.flush()
            client_id_by_phone[phone] = wc.id
            stats["clients"] += 1

        # ---------- 2) operations ----------
        op_id_by_legacy: dict[str, int] = {}
        for r in con.execute("SELECT * FROM operations"):
            frm = (r["from_currency"] or "").upper()
            to = (r["to_currency"] or "").upper()
            cp = resolve_pair(frm, to)
            if cp is None:
                stats["ops_skipped_no_pair"] += 1
                unresolved_pairs[f"{frm}-{to}"] = unresolved_pairs.get(f"{frm}-{to}", 0) + 1
                continue

            phone = r["client_phone"]
            client_id = client_id_by_phone.get(phone)
            if client_id is None:
                # cliente apareció sólo en operations y superó el filtro; crear al vuelo
                wc = WhatsAppClient(phone=phone, display_name=r["client_name"])
                db.add(wc)
                db.flush()
                client_id = wc.id
                client_id_by_phone[phone] = client_id

            quoted = parse_dt(r["quoted_at"]) or parse_dt(r["created_at"]) or datetime.now(timezone.utc)
            status = STATUS_MAP.get((r["status"] or "").upper(), WhatsAppOperationStatus.QUOTED)
            dstatus = DELIVERY_MAP.get((r["delivery_status"] or "").upper()) if r["delivery_status"] else None

            op = WhatsAppOperation(
                client_id=client_id,
                currency_pair_id=cp.id,
                from_amount=r["from_amount"],
                to_amount=r["to_amount"],
                rate_used=r["rate_used"],
                inverse_percentage=False,
                applied_percentage=None,
                default_percentage=None,
                amount_side=WhatsAppAmountSide.SEND,
                bcv_usd=r["bcv_usd"],
                status=status,
                delivery_status=dstatus,
                delivered_at=parse_dt(r["delivered_at"]),
                notes=r["notes"],
                transaction_id=None,
                legacy_sqlite_id=r["id"],
                quoted_at=quoted,
                expires_at=quoted + timedelta(minutes=QUOTE_TTL_MINUTES),
                approved_at=None,
                completed_at=parse_dt(r["completed_at"]),
                cancelled_at=None,
                created_at=parse_dt(r["created_at"]) or quoted,
            )
            db.add(op)
            db.flush()
            op_id_by_legacy[r["id"]] = op.id
            stats["ops"] += 1

        # ---------- 3) incoming_payments ----------
        incoming_id_map: dict[int, int] = {}
        for r in con.execute("SELECT * FROM incoming_payments"):
            op_fk = op_id_by_legacy.get(r["operation_id"]) if r["operation_id"] else None
            if r["operation_id"] and op_fk is None:
                stats["pay_op_unlinked"] += 1
            pay = WhatsAppIncomingPayment(
                client_phone=r["client_phone"],
                provider=r["provider"], amount=r["amount"], currency=r["currency"],
                bank_from=r["bank_from"], bank_to=r["bank_to"],
                account_number=r["account_number"], identification=r["identification"],
                phone_to=r["phone_to"], reference=r["reference"], raw_text=r["raw_text"],
                whatsapp_operation_id=op_fk,
                corrected_at=parse_dt(r["corrected_at"]),
                correction_original=r["correction_original"],
                created_at=parse_dt(r["created_at"]),
            )
            db.add(pay)
            db.flush()
            incoming_id_map[r["id"]] = pay.id
            stats["incoming"] += 1

        # ---------- 4) outgoing_payments ----------
        for r in con.execute("SELECT * FROM outgoing_payments"):
            op_fk = op_id_by_legacy.get(r["operation_id"]) if r["operation_id"] else None
            if r["operation_id"] and op_fk is None:
                stats["pay_op_unlinked"] += 1
            src = incoming_id_map.get(r["source_payment_id"]) if r["source_payment_id"] else None
            if r["source_payment_id"] and src is None:
                stats["src_unlinked"] += 1
            pay = WhatsAppOutgoingPayment(
                client_phone=r["client_phone"],
                provider=r["provider"], amount=r["amount"], currency=r["currency"],
                bank_from=r["bank_from"], bank_to=r["bank_to"],
                account_number=r["account_number"], identification=r["identification"],
                phone_to=r["phone_to"], reference=r["reference"], raw_text=r["raw_text"],
                whatsapp_operation_id=op_fk,
                is_personal_expense=bool(r["is_personal_expense"]),
                personal_description=r["personal_description"],
                is_irrelevant=bool(r["is_irrelevant"]),
                source_payment_id=src,
                corrected_at=parse_dt(r["corrected_at"]),
                correction_original=r["correction_original"],
                created_at=parse_dt(r["created_at"]),
            )
            db.add(pay)
            stats["outgoing"] += 1

        db.flush()

        # ---------- resumen ----------
        print("\n──────── RESUMEN ────────")
        for k, v in stats.items():
            print(f"  {k:22} {v}")
        if unresolved_pairs:
            print("  pares no resueltos (omitidos):")
            for p, n in sorted(unresolved_pairs.items(), key=lambda x: -x[1]):
                print(f"     {p:14} {n}")

        if commit:
            db.commit()
            print("\n✅ COMMIT aplicado.")
        else:
            db.rollback()
            print("\n🟡 DRY-RUN: rollback (nada se escribió). Usa --commit para aplicar.")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"\n❌ Error, rollback: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Migra bot.db (SQLite) -> Postgres backend")
    ap.add_argument("--sqlite-path", required=True, help="Ruta al archivo bot.db")
    ap.add_argument("--commit", action="store_true", help="Aplica (default: dry-run)")
    ap.add_argument("--wipe", action="store_true", help="Borra whatsapp_* antes (sólo pre-cutover)")
    args = ap.parse_args()
    sys.exit(run(args.sqlite_path, commit=args.commit, wipe=args.wipe))
