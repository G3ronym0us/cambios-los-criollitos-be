"""Aviso al operador vía el bot (app/services/bot_notifier.py)."""

import pytest

from app.services import bot_notifier


@pytest.mark.asyncio
async def test_sin_configuracion_no_intenta_y_devuelve_false(monkeypatch):
    # Sin BOT_NOTIFY_URL la feature está apagada; no debe explotar ni intentar red.
    monkeypatch.setattr(bot_notifier.settings, "BOT_NOTIFY_URL", None)
    monkeypatch.setattr(bot_notifier.settings, "BOT_API_KEY", "token")
    assert await bot_notifier.notify_operator("hola") is False


@pytest.mark.asyncio
async def test_sin_token_no_intenta(monkeypatch):
    monkeypatch.setattr(bot_notifier.settings, "BOT_NOTIFY_URL", "http://localhost:3457")
    monkeypatch.setattr(bot_notifier.settings, "BOT_API_KEY", None)
    assert await bot_notifier.notify_operator("hola") is False


@pytest.mark.asyncio
async def test_error_de_red_devuelve_false_sin_propagar(monkeypatch):
    # Un aviso que no sale nunca debe tumbar el poller: se reintenta en la vuelta siguiente.
    monkeypatch.setattr(bot_notifier.settings, "BOT_NOTIFY_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(bot_notifier.settings, "BOT_API_KEY", "token")
    assert await bot_notifier.notify_operator("hola") is False
