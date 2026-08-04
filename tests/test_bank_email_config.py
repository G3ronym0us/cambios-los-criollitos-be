"""Parseo de ZELLE_MAILBOXES (app/core/config.py)."""

import pytest

from app.core.config import MailboxConfig, parse_mailboxes


def test_parsea_dos_buzones():
    raw = (
        '[{"label":"Jean","email":"azocarjean98@gmail.com","password":"aaaa bbbb cccc dddd"},'
        ' {"label":"Mariana","email":"mmendozaperez53@gmail.com","password":"eeee ffff"}]'
    )
    boxes = parse_mailboxes(raw)
    assert boxes == [
        MailboxConfig(label="Jean", email="azocarjean98@gmail.com", password="aaaa bbbb cccc dddd"),
        MailboxConfig(label="Mariana", email="mmendozaperez53@gmail.com", password="eeee ffff"),
    ]


def test_sin_configuracion_devuelve_lista_vacia():
    # La feature apagada no debe romper el arranque del backend.
    assert parse_mailboxes(None) == []
    assert parse_mailboxes("") == []
    assert parse_mailboxes("   ") == []


def test_json_invalido_explota_con_mensaje_claro():
    with pytest.raises(ValueError, match="ZELLE_MAILBOXES"):
        parse_mailboxes("{no es json}")


def test_falta_un_campo_explota():
    with pytest.raises(ValueError, match="password"):
        parse_mailboxes('[{"label":"Jean","email":"a@b.com"}]')


def test_no_es_una_lista_explota():
    with pytest.raises(ValueError, match="lista"):
        parse_mailboxes('{"label":"Jean","email":"a@b.com","password":"x"}')
