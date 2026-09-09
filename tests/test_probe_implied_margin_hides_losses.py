"""
Probe (agente 1, campaña de ruptura): `WhatsAppRateResolver.implied_margin` con un margen
NEGATIVO (el operador cotizó peor que la tasa base del par -- una pérdida real en esa
operación puntual) devuelve `None`, igual que devolvería para una tasa disparatada que "no
salió de este par". Documentado a propósito en el docstring ("el resultado se sale del
rango de un margen comercial"), pero el efecto es que una pérdida real se colapsa al mismo
`None` que "no hay margen que reportar".

Se usa en `whatsapp_payment_service.py:3020` para fijar `applied_percentage` al crear una
operación desde un comprobante. Ese campo alimenta `_profit_from_allocations`
(whatsapp_quote_service.py), que cuando no hay reparto de fondos hace
`float(op.applied_percentage or 0.0)` -- un `None` se convierte en profit=0%, no en "-X%
de pérdida". La operación queda contabilizada como "sin margen" en vez de "perdimos
dinero en este cambio".

No se toca product code: no está claro cuál es el rango de negativo que el negocio
considera "todavía de este par" vs. "la tasa no tiene nada que ver" (decisión de negocio,
no de ingeniería). Se documenta con esta llamada+respuesta.
"""

from app.services.whatsapp_rate_resolver import RateEntry, WhatsAppRateResolver


def test_implied_margin_collapses_real_loss_to_none():
    # Par base (sin margen): USDT->VES @ 960.
    entry = RateEntry(rate=960.0, inverse_percentage=False, base_percentage=None, base_rate=960.0)

    # El operador efectivamente cotizó a 1000 (le dio MÁS Bs por USDT que la base): una
    # pérdida real de ese cambio puntual, no un margen que "no salió de este par".
    margin_on_a_loss = WhatsAppRateResolver.implied_margin(entry, 1000.0)
    assert margin_on_a_loss is None, (
        "si esto deja de ser None, el comportamiento documentado cambió -- revisar "
        "whatsapp_payment_service.py:3020 y _profit_from_allocations antes de asumir "
        "que ahora sí reporta la pérdida"
    )

    # Para contraste: un margen normal sí se reporta.
    margin_normal = WhatsAppRateResolver.implied_margin(entry, 900.0)
    assert margin_normal == 6.25
