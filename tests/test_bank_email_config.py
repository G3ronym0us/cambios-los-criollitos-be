"""Parseo de ZELLE_MAILBOXES (app/core/config.py)."""

import pytest

from app.core.config import MailboxConfig, Settings, parse_mailboxes


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


def test_acepta_lo_que_pydantic_ya_parseo_como_lista():
    """
    Regresión: pydantic-settings parsea solo los valores del .env que parecen JSON, así
    que `ZELLE_MAILBOXES` llegaba como lista y reventaba contra el tipo `str`. El
    validador la vuelve a serializar; sin él la feature no arranca con el .env cargado.
    """
    ya_parseado = [{"label": "Mariana", "email": "m@gmail.com", "password": "aaaa bbbb"}]

    devuelto = Settings.keep_mailboxes_as_json(ya_parseado)

    assert isinstance(devuelto, str)
    assert parse_mailboxes(devuelto) == [
        MailboxConfig(label="Mariana", email="m@gmail.com", password="aaaa bbbb")
    ]


def test_deja_pasar_el_texto_tal_cual():
    raw = '[{"label":"Jean","email":"j@gmail.com","password":"x"}]'
    assert Settings.keep_mailboxes_as_json(raw) == raw


def test_deja_pasar_el_none():
    assert Settings.keep_mailboxes_as_json(None) is None
