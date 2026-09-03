"""
`set_settlements` (reparto en bloque) rechaza cubrir una operacion por encima de su valor
(ver `test_an_operation_cannot_be_covered_above_its_value`), pero el enlace de UN pago
(`set_operation`, el PATCH /{table}/{payment_id}/operation que usa el front para vincular)
llama a `_apply_settlement` sin pasar por ese guardarrail. Esto reproduce el hueco.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def test_linking_a_payment_cannot_overcover_the_operation(service, db, fund, client, operator):
    """
    Un trato de 80 ZELLE ya viene 100% cubierto (settled_amount=80). Vincular OTRO
    comprobante con settled_amount=999 no deberia poder "cubrir" 999 de un trato de 80:
    es exactamente el mismo error que `settlement_exceeds_operation` ataja en el reparto
    en bloque, pero por este camino no hay guardarrail.
    """
    inc80 = f.incoming(db, 80, "ZELLE")
    op80 = _op(db, f.create_op_from_payment(
        service, "incoming", inc80, frm="ZELLE", to="VES", from_amount=80, to_amount=62633.6,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )["uuid"])

    first = f.outgoing(db, 62633.6, "VES")
    service.set_operation("outgoing", first.id, op80.uuid, completing_user=operator,
                           complete_outgoing=True, settled_amount=80)
    assert service.delivered_amount(op80) == 80

    second = f.outgoing(db, 999999.0, "VES")
    with pytest.raises(QuoteServiceError) as exc:
        service.set_operation("outgoing", second.id, op80.uuid, completing_user=operator,
                               complete_outgoing=True, settled_amount=999)
    assert exc.value.code == "settlement_exceeds_operation"
