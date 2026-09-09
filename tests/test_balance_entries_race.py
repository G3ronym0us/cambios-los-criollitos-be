"""
`WhatsAppBalanceService.credit_from_incoming`/`debit_for_operation` son "idempotentes por
pago/operacion" a base de comprobar-y-luego-insertar, sin ningun candado en la fila: dos
requests concurrentes (un reintento del bot, un doble click en el front) pueden pasar la
comprobacion los dos antes de que cualquiera comitee, y dejar el mismo pago acreditado -o
la misma operacion debitada- dos veces. Este archivo prueba el candado de base de datos
que cierra ese hueco (dos indices unicos parciales), simulando la carrera con dos inserts
directos que se saltan el chequeo de la capa de servicio.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.whatsapp_balance import WhatsAppBalanceEntry, WhatsAppBalanceEntryType
from tests import factories as f


def test_db_rejects_a_second_credit_for_the_same_incoming_payment(db, client):
    payment = f.incoming(db, 200, "ZELLE")
    db.add(WhatsAppBalanceEntry(
        client_id=client.id, entry_type=WhatsAppBalanceEntryType.CREDIT,
        amount=200, currency="USD", incoming_payment_id=payment.id,
    ))
    db.flush()
    db.add(WhatsAppBalanceEntry(
        client_id=client.id, entry_type=WhatsAppBalanceEntryType.CREDIT,
        amount=200, currency="USD", incoming_payment_id=payment.id,
    ))
    with pytest.raises(IntegrityError):
        db.flush()


def test_db_rejects_a_second_debit_for_the_same_operation(db, client, fund, pairs, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    service = WhatsAppPaymentService(db)
    payment = f.incoming(db, 80, "ZELLE")
    op_uuid = f.create_op_from_payment(
        service, "incoming", payment, frm="ZELLE", to="VES", from_amount=80, to_amount=62633.6,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )["uuid"]
    from app.models.whatsapp_operation import WhatsAppOperation
    op = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(op_uuid)).first()

    db.add(WhatsAppBalanceEntry(
        client_id=client.id, entry_type=WhatsAppBalanceEntryType.DEBIT,
        amount=30, currency="USD", whatsapp_operation_id=op.id,
    ))
    db.flush()
    db.add(WhatsAppBalanceEntry(
        client_id=client.id, entry_type=WhatsAppBalanceEntryType.DEBIT,
        amount=30, currency="USD", whatsapp_operation_id=op.id,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
