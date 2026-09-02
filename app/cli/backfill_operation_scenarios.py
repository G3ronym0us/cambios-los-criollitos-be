"""
Reclasifica el escenario de operaciones viejas que quedaron sin clasificar: `NORMAL`, sin
receptor del entrante. Es la misma regla que ahora corre sola al nacer una operación desde
un comprobante (`WhatsAppPaymentService._resolve_scenario_for_new_op`), aplicada hacia
atrás sobre lo que ya existe.

El negocio (ver `backend/CLAUDE.md`, "Quién manda qué al grupo de WhatsApp"): todos los
comprobantes al grupo los sube Diohandres, el operador — quién los sube no distingue nada.
Lo que distingue es el CONTENIDO, y en una operación ya vieja eso se lee de qué comprobantes
quedaron vinculados:

  - Tiene un ENTRANTE vinculado -> el cliente se lo mandó directo a Diohandres.
    ZELLE_DIRECT, sin receptor (NULL = operador).
  - No tiene entrante y sí tiene un SALIENTE vinculado -> el socio cobró al cliente en su
    propio WhatsApp y el entrante nunca llegó al operador. VIA_PARTNER, con
    `received_by_user_id` = el socio del fondo — sólo si hay exactamente uno identificable
    (mismo criterio que `WhatsAppPaymentService._sole_fund_partner`: el miembro del fondo
    con `whatsapp_phone` propio; `is_fund_manager` no sirve porque también marca a
    Diohandres en su propio fondo).

Una operación sin ningún comprobante vinculado (nacida de una cotización que nunca se pagó)
no tiene de qué CONTENIDO leer el escenario: se deja como está.

Esto reasigna a quién se le atribuye la ganancia de la operación (`received_by_user_id`
alimenta el reparto del fondo), así que el dry-run es OBLIGATORIO antes de escribir. Este
script nunca corre solo — hay que invocarlo a mano con `--apply` para que escriba.

    python -m app.cli.backfill_operation_scenarios              # dry-run: imprime y no escribe
    python -m app.cli.backfill_operation_scenarios --apply      # escribe
"""

import argparse

from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationScenario
from app.services.whatsapp_payment_service import WhatsAppPaymentService


def plan_scenario_backfill(db: Session) -> dict:
    """Qué operación tocaría, y con qué escenario/receptor. No escribe nada."""
    svc = WhatsAppPaymentService(db)
    plan = {
        "zelle_direct": [],
        "via_partner": [],
        "skipped_no_receipt": [],
        "skipped_cash": [],
        "skipped_no_fund": [],
        "skipped_ambiguous_partner": [],
    }

    # Sólo lo que sigue en el default histórico: si el operador (o una corrida anterior de
    # este mismo CLI) ya fijó escenario o receptor, no se toca — es la misma regla de "no
    # pisar lo puesto a mano" que aplica la clasificación automática en vivo.
    ops = (
        db.query(WhatsAppOperation)
        .filter(
            WhatsAppOperation.scenario == WhatsAppOperationScenario.NORMAL,
            WhatsAppOperation.received_by_user_id.is_(None),
        )
        .order_by(WhatsAppOperation.id)
        .all()
    )

    for op in ops:
        has_incoming = bool(op.incoming_payments)
        has_outgoing = bool(op.outgoing_payments)
        if not has_incoming and not has_outgoing:
            plan["skipped_no_receipt"].append(op.id)
            continue

        cp = op.currency_pair
        if cp is None or cp.settles_in_cash:
            # Efectivo (USD-VES): se paga en mano, directo con Diohandres. Nunca hay socio,
            # aunque el par comparta fondo con Zelle/Paypal por moneda.
            plan["skipped_cash"].append(op.id)
            continue
        if op.fund_group_id is None:
            # Sin fondo detrás no hay grupo ni socio de qué hablar.
            plan["skipped_no_fund"].append(op.id)
            continue

        if has_incoming:
            plan["zelle_direct"].append(op.id)
        else:
            partner = svc._sole_fund_partner(op.fund_group_id)
            if partner is None:
                plan["skipped_ambiguous_partner"].append(op.id)
            else:
                plan["via_partner"].append((op.id, partner.id, partner.username))

    return plan


def apply_scenario_backfill(db: Session, plan: dict) -> None:
    """Escribe lo que dice el plan."""
    for op_id in plan["zelle_direct"]:
        db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op_id).update(
            {"scenario": WhatsAppOperationScenario.ZELLE_DIRECT}
        )

    for op_id, partner_id, _ in plan["via_partner"]:
        db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op_id).update(
            {
                "scenario": WhatsAppOperationScenario.VIA_PARTNER,
                "received_by_user_id": partner_id,
            }
        )

    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclasificación de escenario de operaciones viejas sin clasificar"
    )
    parser.add_argument("--apply", action="store_true", help="escribe (por defecto: dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = plan_scenario_backfill(db)

        print(f"\nZELLE_DIRECT (tiene entrante vinculado): {len(plan['zelle_direct'])}")
        for op_id in plan["zelle_direct"]:
            print(f"  op {op_id}")

        print(f"\nVIA_PARTNER (sólo saliente, socio identificado): {len(plan['via_partner'])}")
        for op_id, partner_id, partner_name in plan["via_partner"]:
            print(f"  op {op_id} -> socio {partner_name} (user {partner_id})")

        print(f"\nsin ningún comprobante vinculado (nada que leer): {len(plan['skipped_no_receipt'])}")
        print(f"par en efectivo, nunca hay socio: {len(plan['skipped_cash'])}")
        print(f"sin fondo detrás: {len(plan['skipped_no_fund'])}")
        print(f"socio ambiguo o inexistente en el fondo (no se adivina): {len(plan['skipped_ambiguous_partner'])}")
        for op_id in plan["skipped_ambiguous_partner"]:
            print(f"  op {op_id}")

        total = len(plan["zelle_direct"]) + len(plan["via_partner"])
        if args.apply:
            apply_scenario_backfill(db, plan)
            print(f"\nEscrito: {total} operaciones reclasificadas.")
        else:
            print(f"\nDry-run: {total} operaciones se tocarían. No se escribió nada. Con --apply se aplica.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
