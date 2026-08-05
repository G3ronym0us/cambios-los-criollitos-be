"""
Qué correo confirma qué pago, y cuándo se le insiste al operador
(app/services/bank_email_matching.py).

Puro, sin BD ni red. El reloj entra por parámetro: nada de datetime.now() adentro.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.bank_email_matching import (
    build_mailbox_down_message,
    ESCALATION_MINUTES,
    NotificationCandidate,
    build_confirmed_message,
    build_escalation_message,
    is_final_step,
    pick_email_confirmation,
    schedule_next,
    should_alert_mailbox_down,
)

NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)
PAID_AT = NOW - timedelta(minutes=20)


def cand(id_, amount, *, minutes_ago=25, label="Jean", name="Carlos R Barrientos"):
    return NotificationCandidate(
        id=id_,
        amount=Decimal(amount),
        received_at=NOW - timedelta(minutes=minutes_ago),
        mailbox_label=label,
        sender_name=name,
        bank="BANK_OF_AMERICA",
    )


def pick(candidates, amount="30.00", paid_at=PAID_AT, now=NOW):
    return pick_email_confirmation(
        candidates, amount=Decimal(amount), payment_created_at=paid_at, now=now
    )


# ---------- pick_email_confirmation ----------

def test_confirma_con_el_correo_del_mismo_monto():
    chosen, count = pick([cand(1, "30.00")])
    assert chosen is not None and chosen.id == 1
    assert count == 1


def test_no_confirma_con_monto_distinto():
    chosen, count = pick([cand(1, "31.00")])
    assert chosen is None and count == 0


def test_no_confirma_con_diferencia_de_un_centavo():
    # Sin tolerancia: los dos lados son dólares con centavos, no hay conversión de por medio.
    chosen, _ = pick([cand(1, "30.01")])
    assert chosen is None


def test_sin_candidatos_no_confirma():
    chosen, count = pick([])
    assert chosen is None and count == 0


def test_ignora_correo_anterior_a_la_ventana():
    # 13 h antes del pago: fuera de las 12 h hacia atrás.
    viejo = cand(1, "30.00", minutes_ago=13 * 60 + 20)
    chosen, count = pick([viejo])
    assert chosen is None and count == 0


def test_acepta_correo_dentro_de_la_ventana_hacia_atras():
    # 11 h antes del pago: adentro.
    chosen, _ = pick([cand(1, "30.00", minutes_ago=11 * 60 + 20)])
    assert chosen is not None


def test_ignora_correo_del_futuro():
    futuro = NotificationCandidate(
        id=1, amount=Decimal("30.00"), received_at=NOW + timedelta(minutes=5),
        mailbox_label="Jean", sender_name="Carlos", bank="BANK_OF_AMERICA",
    )
    chosen, count = pick([futuro])
    assert chosen is None and count == 0


def test_con_dos_candidatos_toma_el_mas_antiguo_y_los_cuenta():
    # El aviso tiene que poder decir "había 2 correos de $30,00 sin asignar".
    chosen, count = pick([cand(2, "30.00", minutes_ago=10), cand(1, "30.00", minutes_ago=25)])
    assert chosen is not None and chosen.id == 1
    assert count == 2


def test_elige_solo_entre_los_del_monto_correcto():
    chosen, count = pick([cand(1, "50.00"), cand(2, "30.00")])
    assert chosen is not None and chosen.id == 2
    assert count == 1


# ---------- escalera ----------

def test_primer_aviso_a_los_cinco_minutos():
    assert schedule_next(0, NOW) == NOW + timedelta(minutes=5)


def test_segundo_aviso_a_los_quince():
    assert schedule_next(1, NOW) == NOW + timedelta(minutes=15)


def test_los_escalones_son_los_del_spec():
    assert ESCALATION_MINUTES == [5, 15, 30, 60]


def test_despues_del_ultimo_escalon_no_hay_mas():
    assert schedule_next(len(ESCALATION_MINUTES), NOW) is None


def test_el_ultimo_escalon_es_final():
    assert is_final_step(len(ESCALATION_MINUTES) - 1) is True


def test_los_escalones_intermedios_no_son_finales():
    assert is_final_step(0) is False
    assert is_final_step(2) is False


# ---------- mensajes ----------

def test_mensaje_de_confirmacion_dice_nombre_cuenta_y_banco():
    text = build_confirmed_message(
        cand(1, "30.00"), amount=Decimal("30.00"), minutes_elapsed=22, ambiguity_count=1
    )
    assert "Carlos R Barrientos" in text
    assert "Jean" in text
    assert "30,00" in text
    assert "22 min" in text
    assert "⚠️" not in text


def test_mensaje_de_confirmacion_avisa_si_habia_varios_candidatos():
    text = build_confirmed_message(
        cand(1, "30.00"), amount=Decimal("30.00"), minutes_elapsed=1, ambiguity_count=2
    )
    assert "2 correos" in text


def test_mensaje_de_escalon_incluye_los_minutos():
    text = build_escalation_message(0, amount=Decimal("30.00"), client_phone="584121234567")
    assert "5 min" in text
    assert "30,00" in text


def test_ultimo_escalon_avisa_que_cierra():
    text = build_escalation_message(3, amount=Decimal("30.00"), client_phone="584121234567")
    assert "1 h" in text
    assert "cierro" in text.lower()


# ---------- buzón caído ----------

def test_un_fallo_suelto_no_avisa():
    # Un timeout contra Gmail se cura solo en la vuelta siguiente; avisar de eso es
    # entrenar al operador para ignorar las alertas.
    assert should_alert_mailbox_down(1, is_auth_failure=False) is False


def test_dos_fallos_seguidos_tampoco():
    assert should_alert_mailbox_down(2, is_auth_failure=False) is False


def test_al_tercer_fallo_avisa():
    assert should_alert_mailbox_down(3, is_auth_failure=False) is True


def test_credenciales_rechazadas_avisan_de_una():
    # No se arregla sola: cada minuto que pasa es un pago sin verificar.
    assert should_alert_mailbox_down(1, is_auth_failure=True) is True


def test_mensaje_de_credenciales_pide_regenerar():
    text = build_mailbox_down_message("Mariana", "auth", is_auth_failure=True)
    assert "Mariana" in text
    assert "contraseña de aplicación" in text


def test_mensaje_transitorio_no_culpa_a_las_credenciales():
    text = build_mailbox_down_message("Mariana", "The read operation timed out", is_auth_failure=False)
    assert "credenciales" not in text.lower()
    assert "timed out" in text
    assert "reintentando" in text
