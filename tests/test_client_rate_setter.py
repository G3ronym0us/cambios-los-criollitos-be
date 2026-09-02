"""
El permiso de fijar tasa: qué clientes pueden decir «a 935» y que eso sea una instrucción.

Hay intermediarios que fijan ellos la tasa a la que les compramos los dólares, y la dicen en
un mensaje suelto. Sin este permiso ese número se leía como un MONTO: en producción acuñó
cotizaciones de 935 y 940 dólares que nadie pidió (ops 4035, 4066, 4084, 4119, todas de
Lismar en USD-VES). No es ruido que filtrar, es una instrucción — y sólo la pueden dar
algunos, igual que `is_usdt_authorized` acota quién puede pedir USDT.
"""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.schemas.whatsapp import WhatsAppClientResponse, WhatsAppClientUpsert


@pytest.fixture
def cliente(db):
    row = WhatsAppClient(phone="584148740764", display_name="Lismar Jesus")
    db.add(row)
    db.flush()
    return row


def test_nadie_fija_tasa_por_defecto(db, cliente):
    """El valor por defecto importa: encenderlo sin querer deja al cliente mover la tasa."""
    assert cliente.is_rate_setter is False


def test_se_puede_conceder_y_retirar(db, cliente):
    cliente.is_rate_setter = True
    db.flush()
    db.refresh(cliente)
    assert cliente.is_rate_setter is True

    cliente.is_rate_setter = False
    db.flush()
    db.refresh(cliente)
    assert cliente.is_rate_setter is False


def test_viaja_en_la_serializacion(db, cliente):
    """Es lo que el bot necesita para saber si el «935» que acaba de llegar es una orden."""
    cliente.is_rate_setter = True
    db.flush()
    assert cliente.dict()["is_rate_setter"] is True
    assert WhatsAppClientResponse.model_validate(cliente.dict()).is_rate_setter is True


def test_el_update_lo_deja_pasar(db, cliente):
    """El permiso se concede desde el panel, así que tiene que existir en el esquema."""
    payload = WhatsAppClientUpsert(is_rate_setter=True)
    assert payload.is_rate_setter is True
    # Y no se toca solo: omitirlo deja el permiso como estaba.
    assert WhatsAppClientUpsert().is_rate_setter is None
