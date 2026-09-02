"""
Matching de comprobantes con operaciones (app/services/operation_match_service.py).

Puro, sin BD: corre en cualquier lado aunque no haya Postgres local.

Los casos de `pick_auto_match` son el puerto 1:1 de whatsapp-bot/src/test-cases/outgoing-match.json
(runner: src/test-outgoing-match.ts). Al mover el matcher al backend, esa suite deja de cubrir el
camino que corre en producción — estos tests la reemplazan. Si aparece una regresión del matcher,
el caso se agrega AQUÍ.
"""

import pytest

from datetime import datetime, timedelta, timezone

from app.services.operation_match_service import (
    ForwardedCriteria,
    IncomingCandidate,
    OperationCandidate,
    OutgoingCriteria,
    pick_auto_match,
    pick_forwarded_incoming,
    pick_suggestion,
    rank_candidates,
    score_candidate,
)

NOW = datetime(2026, 7, 26, 18, 35, tzinfo=timezone.utc)


def op(uuid, to_amount, *, minutes_ago=5, notes=None, has_outgoing=False, **kw):
    return OperationCandidate(
        uuid=uuid,
        to_amount=to_amount,
        to_currency=kw.pop("to_currency", "VES"),
        from_amount=kw.pop("from_amount", 100.0),
        from_currency=kw.pop("from_currency", "USDT"),
        created_at=NOW - timedelta(minutes=minutes_ago),
        notes=notes,
        has_outgoing_payment=has_outgoing,
        **kw,
    )


def criteria(amount, **kw):
    return OutgoingCriteria(amount=amount, currency=kw.pop("currency", "VES"), **kw)


# ---------------------------------------------------------------------------
# Política del bot (pick_auto_match) — puerto de outgoing-match.json
# ---------------------------------------------------------------------------


def test_receipt_repeated_when_only_op_already_has_outgoing_does_not_match():
    """dup-saliente: la única op del monto YA tiene saliente → no duplicar."""
    candidates = [op("op-tomada", 76260.0, has_outgoing=True)]
    assert pick_auto_match(candidates, criteria(76260.0), NOW) is None


def test_two_ops_same_amount_one_taken_picks_the_free_one_without_tokens():
    """dup-saliente: con una sola libre no hace falta desambiguar."""
    candidates = [
        op("op-tomada", 76260.0, has_outgoing=True),
        op("op-libre", 76260.0),
    ]
    assert pick_auto_match(candidates, criteria(76260.0), NOW) == "op-libre"


def test_free_op_within_window_and_tolerance_matches():
    """Regresión: el camino feliz sigue vinculando."""
    candidates = [op("op-normal", 14757.0, minutes_ago=30)]
    assert pick_auto_match(candidates, criteria(14757.0), NOW) == "op-normal"


def test_outside_24h_window_does_not_match():
    candidates = [op("op-vieja", 14757.0, minutes_ago=25 * 60)]
    assert pick_auto_match(candidates, criteria(14757.0), NOW) is None


def test_two_free_ops_same_amount_without_tokens_is_ambiguous():
    """Sin tokens que desambigüen el bot NO vincula: decide el operador."""
    candidates = [op("op-a", 14757.0, minutes_ago=3), op("op-b", 14757.0, minutes_ago=300)]
    assert pick_auto_match(candidates, criteria(14757.0), NOW) is None


def test_tokens_in_notes_disambiguate():
    candidates = [
        op("op-a", 14757.0, minutes_ago=3, notes="pago a 04141234567"),
        op("op-b", 14757.0, minutes_ago=300, notes="pago a 04249999999"),
    ]
    got = pick_auto_match(candidates, criteria(14757.0, phone_to="04249999999"), NOW)
    assert got == "op-b"


def test_token_shorter_than_four_chars_is_ignored():
    candidates = [op("op-a", 14757.0), op("op-b", 14757.0, notes="ref 123")]
    assert pick_auto_match(candidates, criteria(14757.0, identification="123"), NOW) is None


def test_amount_just_outside_one_percent_does_not_match():
    candidates = [op("op-a", 14757.0)]
    assert pick_auto_match(candidates, criteria(14757.0 * 1.02), NOW) is None


def test_amount_within_one_percent_matches():
    candidates = [op("op-a", 14757.0)]
    assert pick_auto_match(candidates, criteria(14757.0 * 1.005), NOW) == "op-a"


def test_different_currency_does_not_match():
    candidates = [op("op-a", 14757.0, to_currency="COP")]
    assert pick_auto_match(candidates, criteria(14757.0, currency="VES"), NOW) is None


def test_auto_match_ignores_partial_coverage_and_uses_raw_to_amount():
    """
    El bot compara contra `to_amount` crudo aunque la op esté a medio cubrir: prorratear es
    cosa del ranking, donde hay un operador mirando.
    """
    partial = op("op-parcial", 14757.0, value_amount=100.0, delivered_amount=60.0, pending_amount=40.0)
    assert pick_auto_match([partial], criteria(5902.8), NOW) is None
    assert pick_auto_match([partial], criteria(14757.0), NOW) == "op-parcial"


# ---------------------------------------------------------------------------
# Política del bot, lado ENTRANTE
#
# Antes no había ninguna: el bot colgaba el comprobante de la op abierta más reciente del
# cliente, sin comparar monto ni mirar si esa op ya tenía el suyo. Así acabó un recibo de
# 15 ZELLE dentro de una op de 120 que ya estaba servida (pago 163 → op 2593 en producción).
# ---------------------------------------------------------------------------


def op_in(uuid, from_amount, *, status="QUOTED", has_incoming=False, minutes_ago=5, **kw):
    return OperationCandidate(
        uuid=uuid,
        to_amount=kw.pop("to_amount", 0.0),
        to_currency=kw.pop("to_currency", "VES"),
        from_amount=from_amount,
        from_currency=kw.pop("from_currency", "ZELLE"),
        created_at=NOW - timedelta(minutes=minutes_ago),
        status=status,
        has_incoming_payment=has_incoming,
        **kw,
    )


def crit_in(amount, **kw):
    return OutgoingCriteria(amount=amount, currency=kw.pop("currency", "ZELLE"), **kw)


def test_incoming_matches_an_open_operation_of_the_same_amount():
    candidates = [op_in("op-abierta", 60.0)]
    assert pick_auto_match(candidates, crit_in(60.0), NOW, "incoming") == "op-abierta"


def test_incoming_does_not_match_an_operation_that_already_has_its_receipt():
    """El caso de Rennier: segundo comprobante de 60, 20 min después, a la misma op."""
    candidates = [op_in("op-servida", 60.0, has_incoming=True)]
    assert pick_auto_match(candidates, crit_in(60.0), NOW, "incoming") is None


def test_incoming_of_a_different_amount_does_not_match():
    """El caso reportado: 15 ZELLE contra una op de 120."""
    candidates = [op_in("op-120", 120.0)]
    assert pick_auto_match(candidates, crit_in(15.0), NOW, "incoming") is None


def test_incoming_ignores_closed_operations():
    for closed in ("COMPLETED", "CANCELLED"):
        candidates = [op_in("op-cerrada", 60.0, status=closed)]
        assert pick_auto_match(candidates, crit_in(60.0), NOW, "incoming") is None


def test_incoming_matches_a_pending_operation_too():
    """PENDING sigue abierta: la op cotizada que ya recibió notas pero no comprobante."""
    candidates = [op_in("op-pendiente", 60.0, status="PENDING")]
    assert pick_auto_match(candidates, crit_in(60.0), NOW, "incoming") == "op-pendiente"


def test_incoming_with_two_open_operations_of_the_same_amount_is_ambiguous():
    candidates = [op_in("a", 60.0, minutes_ago=3), op_in("b", 60.0, minutes_ago=40)]
    assert pick_auto_match(candidates, crit_in(60.0), NOW, "incoming") is None


def test_incoming_compares_against_from_amount_not_to_amount():
    """La op vale 60 ZELLE y entrega 48.024 VES: el comprobante del cliente es de 60."""
    candidates = [op_in("op", 60.0, to_amount=48024.0)]
    assert pick_auto_match(candidates, crit_in(60.0), NOW, "incoming") == "op"
    assert pick_auto_match(candidates, crit_in(48024.0), NOW, "incoming") is None


def test_incoming_respects_the_currency():
    candidates = [op_in("op", 60.0, from_currency="PAYPAL")]
    assert pick_auto_match(candidates, crit_in(60.0, currency="ZELLE"), NOW, "incoming") is None


def test_outgoing_side_is_untouched_by_the_incoming_rules():
    """Un saliente sí puede pagar una op COMPLETED: ahí no se filtra por estado."""
    candidates = [op("op-completada", 14757.0, status="COMPLETED")]
    assert pick_auto_match(candidates, criteria(14757.0), NOW) == "op-completada"


# ---------------------------------------------------------------------------
# Política del front (rank_candidates + pick_suggestion)
# ---------------------------------------------------------------------------


def test_ranking_puts_the_exact_amount_and_nearest_time_first():
    candidates = [
        op("ana", 14757.0, minutes_ago=3),
        op("luis", 14750.0, minutes_ago=29 * 60),
        op("pedro", 14800.0, minutes_ago=180),
        op("rosa", 9200.0, minutes_ago=60),
    ]
    ranked = rank_candidates(candidates, criteria(14757.0), "outgoing", NOW)
    assert [s.uuid for s in ranked][0] == "ana"
    sug = pick_suggestion(ranked)
    assert sug is not None and sug.uuid == "ana" and sug.confident


def test_time_breaks_the_tie_between_equal_amounts():
    candidates = [op("cercana", 14757.0, minutes_ago=3), op("lejana", 14757.0, minutes_ago=300)]
    ranked = rank_candidates(candidates, criteria(14757.0), "outgoing", NOW)
    sug = pick_suggestion(ranked)
    assert sug is not None and sug.uuid == "cercana" and sug.confident


def test_exact_tie_is_suggested_but_not_confident():
    """Empate en monto Y hora: se marca la mejor, pero el front no debe preseleccionarla."""
    candidates = [op("a", 14757.0, minutes_ago=3), op("b", 14757.0, minutes_ago=3)]
    ranked = rank_candidates(candidates, criteria(14757.0), "outgoing", NOW)
    sug = pick_suggestion(ranked)
    assert sug is not None and not sug.confident


def test_no_candidate_within_tolerance_yields_no_suggestion():
    candidates = [op("a", 14757.0), op("b", 9200.0)]
    ranked = rank_candidates(candidates, criteria(3300.0), "outgoing", NOW)
    assert pick_suggestion(ranked) is None


def test_ranking_prorates_a_partially_covered_operation():
    """Entregado 60 de 100 → el comprobante que falta vale el 40% de to_amount."""
    partial = op(
        "parcial", 14757.0, minutes_ago=30, value_amount=100.0, delivered_amount=60.0, pending_amount=40.0
    )
    other = op("entera", 14757.0, minutes_ago=3)
    ranked = rank_candidates([partial, other], criteria(5902.8), "outgoing", NOW)
    sug = pick_suggestion(ranked)
    assert sug is not None and sug.uuid == "parcial" and sug.confident


def test_incoming_side_compares_against_from_amount():
    candidates = [op("a", 14757.0, from_amount=100.0, from_currency="USDT")]
    ranked = rank_candidates(candidates, criteria(100.0, currency="USDT"), "incoming", NOW)
    assert ranked[0].within_tolerance
    assert pick_suggestion(ranked).uuid == "a"


def test_missing_currency_on_either_side_does_not_disqualify():
    """El OCR no siempre saca la moneda; no se castiga por eso."""
    candidates = [op("a", 14757.0, to_currency=None)]
    ranked = rank_candidates(candidates, criteria(14757.0), "outgoing", NOW)
    assert ranked[0].within_tolerance


def test_payment_without_amount_scores_nothing():
    candidates = [op("a", 14757.0)]
    ranked = rank_candidates(candidates, criteria(None), "outgoing", NOW)
    assert ranked[0].score == 0.0
    assert pick_suggestion(ranked) is None


def test_ranking_is_more_permissive_than_the_bot_never_the_reverse():
    """
    Invariante del diseño: donde el bot vincula solo, el ranking siempre sugiere con
    confianza. Lo contrario sí puede pasar (el ranking sugiere donde el bot se abstiene).
    """
    candidates = [op("a", 14757.0, minutes_ago=3), op("b", 20000.0, minutes_ago=10)]
    crit = criteria(14757.0)
    auto = pick_auto_match(candidates, crit, NOW)
    sug = pick_suggestion(rank_candidates(candidates, crit, "outgoing", NOW))
    assert auto == "a"
    assert sug is not None and sug.uuid == auto and sug.confident


def test_score_candidate_reports_signed_delta():
    s = score_candidate(op("a", 14800.0), criteria(14757.0), "outgoing", NOW)
    assert s.delta == 43.0
    s2 = score_candidate(op("b", 14750.0), criteria(14757.0), "outgoing", NOW)
    assert s2.delta == -7.0


# ---------------------------------------------------------------------------
# Comprobante reenviado al grupo (pick_forwarded_incoming)
# ---------------------------------------------------------------------------


def inc(pid, amount, *, minutes_ago=5, **kw):
    return IncomingCandidate(
        id=pid,
        amount=amount,
        currency=kw.pop("currency", "ZELLE"),
        created_at=NOW - timedelta(minutes=minutes_ago),
        **kw,
    )


def fwd(amount, **kw):
    return ForwardedCriteria(
        provider=kw.pop("provider", None),
        amount=amount,
        currency=kw.pop("currency", "ZELLE"),
        **kw,
    )


def test_forwarded_matches_the_same_zelle_receipt():
    assert pick_forwarded_incoming([inc(7, 200.0)], set(), fwd(200.0), NOW) == 7


def test_forwarded_requires_the_same_currency():
    assert pick_forwarded_incoming([inc(7, 200.0)], set(), fwd(200.0, currency="VES"), NOW) is None


def test_forwarded_matches_a_non_zelle_receipt_by_reference():
    """Caso BRL→VES (2026-08-06): antes solo se miraba ZELLE y el reenvío del entrante en
    reales se guardaba como un saliente fantasma, además del pago real en Bs."""
    candidates = [inc(7, 500.0, currency="BRL", reference="E2E-778")]
    criteria = fwd(500.0, currency="BRL", reference="E2E-778")
    assert pick_forwarded_incoming(candidates, set(), criteria, NOW) == 7


def test_forwarded_matches_a_non_zelle_receipt_by_ocr_fingerprint():
    """Sin referencia, la prueba es que es literalmente la misma imagen: el mismo texto."""
    text = "Comprovante PIX R$ 500,00 13/07 14:32"
    candidates = [inc(7, 500.0, currency="BRL", raw_text=text)]
    criteria = fwd(500.0, currency="BRL", raw_text="Comprovante PIX   R$ 500,00\n13/07 14:32")
    assert pick_forwarded_incoming(candidates, set(), criteria, NOW) == 7


def test_forwarded_non_zelle_rejects_a_different_receipt_of_the_same_amount():
    """Dos pagos del mismo monto en una hora son normales fuera de Zelle; tragarse el
    segundo como reenvío borraría un saliente REAL."""
    candidates = [inc(7, 500.0, currency="BRL", raw_text="Comprovante PIX R$ 500,00 14:32")]
    criteria = fwd(500.0, currency="BRL", raw_text="Comprovante PIX R$ 500,00 15:07")
    assert pick_forwarded_incoming(candidates, set(), criteria, NOW) is None


def test_forwarded_non_zelle_without_reference_or_text_does_not_match():
    candidates = [inc(7, 4908.0, currency="VES", raw_text="Pago movil 4.908,00 Bs")]
    assert pick_forwarded_incoming(candidates, set(), fwd(4908.0, currency="VES"), NOW) is None


def test_forwarded_tolerance_is_tighter_than_the_outgoing_one():
    """±0,1%: es el mismo comprobante, no uno parecido. Un 0,5% ya no cuenta."""
    assert pick_forwarded_incoming([inc(7, 200.0)], set(), fwd(201.0), NOW) is None
    assert pick_forwarded_incoming([inc(7, 200.0)], set(), fwd(200.1), NOW) == 7


def test_forwarded_outside_the_window_does_not_match():
    assert pick_forwarded_incoming([inc(7, 200.0, minutes_ago=8 * 60)], set(), fwd(200.0), NOW) is None


def test_forwarded_matches_hours_later_within_the_window():
    """El reenvío al grupo es un asiento contable que el operador hace cuando puede. Con la
    ventana de 60 min, el Zelle de $25 del 2026-08-24 23:39 reenviado a las 00:56 (77 min)
    no calzaba y quedaba un saliente fantasma (pago 4975)."""
    assert pick_forwarded_incoming([inc(7, 25.0, minutes_ago=77)], set(), fwd(25.0), NOW) == 7


def test_forwarded_zelle_matches_when_only_the_forward_has_a_confirmation():
    """Son dos capturas de la MISMA transferencia: el cliente manda la pantalla en español,
    sin código a la vista, y el operador reenvía la de su banco, que sí trae
    'Confirmation: JLXOWIF757BX'. La igualdad estricta rechazaba el par por un dato que sólo
    un lado tenía (pago 4975)."""
    candidates = [inc(7, 25.0, reference=None)]
    criteria = fwd(25.0, reference="JLXOWIF757BX")
    assert pick_forwarded_incoming(candidates, set(), criteria, NOW) == 7


def test_forwarded_rejects_two_different_confirmations():
    """Aflojar a «sólo cuando la traen los dos» no puede unir transferencias distintas."""
    candidates = [inc(7, 25.0, reference="OTRACONF99")]
    criteria = fwd(25.0, reference="JLXOWIF757BX")
    assert pick_forwarded_incoming(candidates, set(), criteria, NOW) is None


def test_forwarded_non_zelle_still_needs_proof_when_only_one_side_has_a_reference():
    """Fuera de Zelle el monto no basta: sin referencia en los dos lados, la prueba tiene que
    ser la huella del OCR."""
    candidates = [inc(7, 500.0, currency="BRL", reference=None, raw_text="Comprovante PIX 14:32")]
    criteria = fwd(500.0, currency="BRL", reference="E2E-778", raw_text="Comprovante PIX 15:07")
    assert pick_forwarded_incoming(candidates, set(), criteria, NOW) is None
    misma = fwd(500.0, currency="BRL", reference="E2E-778", raw_text="Comprovante PIX 14:32")
    assert pick_forwarded_incoming(candidates, set(), misma, NOW) == 7


def test_forwarded_skips_an_already_reused_incoming():
    """Un Zelle no puede reenviarse a dos grupos."""
    assert pick_forwarded_incoming([inc(7, 200.0)], {7}, fwd(200.0), NOW) is None


def test_forwarded_filters_by_exact_reference_when_present():
    candidates = [inc(7, 200.0, reference="ABC123"), inc(8, 200.0, reference="XYZ999")]
    assert pick_forwarded_incoming(candidates, set(), fwd(200.0, reference="XYZ999"), NOW) == 8


def test_forwarded_ambiguous_without_tokens_returns_none():
    candidates = [inc(7, 200.0, minutes_ago=3), inc(8, 200.0, minutes_ago=10)]
    assert pick_forwarded_incoming(candidates, set(), fwd(200.0), NOW) is None


def test_forwarded_disambiguates_by_identification():
    candidates = [
        inc(7, 200.0, minutes_ago=3, identification="V12345678"),
        inc(8, 200.0, minutes_ago=10, identification="V87654321"),
    ]
    got = pick_forwarded_incoming(candidates, set(), fwd(200.0, identification="V87654321"), NOW)
    assert got == 8


# ---------------------------------------------------------------------------
# Capa con BD (OperationMatchService) — necesita Postgres local; si no, se salta
# ---------------------------------------------------------------------------


def test_service_ranks_a_real_payment_against_real_operations(db, fund, client, operator):
    """Prueba el camino completo: cargar candidatas de la BD y puntuarlas contra un pago."""
    from app.services.operation_match_service import OperationMatchService
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    payment_service = WhatsAppPaymentService(db)
    inc = f.incoming(db, 220, "ZELLE")
    created = f.create_op_from_payment(
        payment_service, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )
    # Un saliente por el monto exacto de esa op, todavía sin vincular.
    out = f.outgoing(db, 1005.44, "BRL")
    db.flush()

    result = OperationMatchService(db).rank_for_payment(out.id, "outgoing")

    assert result.items, "el servicio debe devolver candidatas desde la BD"
    assert result.total == len(result.items)
    assert result.suggestion is not None
    assert str(result.suggestion.uuid) == str(created["uuid"])
    assert result.suggestion.confident
    # La sugerida encabeza la página en el orden por defecto ("suggested").
    top_op, _ = result.items[0]
    assert str(top_op.uuid) == str(created["uuid"])


def test_service_returns_nothing_for_an_unknown_payment(db):
    from app.services.operation_match_service import OperationMatchService

    result = OperationMatchService(db).rank_for_payment(999_999, "outgoing")
    assert result.items == [] and result.suggestion is None and result.total == 0


def test_service_auto_match_finds_the_operation_by_client_phone(db, fund, client, operator):
    """El camino del bot, de punta a punta contra la BD."""
    from app.services.operation_match_service import OperationMatchService, OutgoingCriteria
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    payment_service = WhatsAppPaymentService(db)
    inc = f.incoming(db, 220, "ZELLE", phone=client.phone)
    created = f.create_op_from_payment(
        payment_service, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )
    db.flush()

    matched = OperationMatchService(db).auto_match(
        OutgoingCriteria(amount=1005.44, currency="BRL"), phone=client.phone
    )
    assert matched is not None and str(matched.uuid) == str(created["uuid"])


def test_service_auto_match_ignores_an_operation_that_already_has_a_payout(
    db, fund, client, operator
):
    """La regla anti-duplicado, verificada contra la BD y no solo en memoria."""
    from app.services.operation_match_service import OperationMatchService, OutgoingCriteria
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    payment_service = WhatsAppPaymentService(db)
    inc = f.incoming(db, 220, "ZELLE", phone=client.phone)
    created = f.create_op_from_payment(
        payment_service, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )
    payment_service.set_operation(
        "outgoing", f.outgoing(db, 1005.44, "BRL").id, created["uuid"],
        completing_user=operator, complete_outgoing=True,
    )
    db.flush()

    matched = OperationMatchService(db).auto_match(
        OutgoingCriteria(amount=1005.44, currency="BRL"), phone=client.phone
    )
    assert matched is None, "una op con saliente no puede absorber otro comprobante"


def test_service_auto_match_reaches_the_anonymous_operations_of_a_partner(db, fund, pairs, partner):
    """
    El comprobante que se paga en el chat directo de un SOCIO tiene que alcanzar sus ops,
    aunque VIA_PARTNER las haya reasignado al cliente anónimo `anon:partner:{user_id}`.
    Caso real (Dionis, agosto 2026): 40 salientes quedaron sueltos porque el matcher solo
    miraba el cliente de ese teléfono, donde ya no queda ninguna op.
    """
    from datetime import timedelta

    from app.models.fund import FundGroupMember
    from app.models.whatsapp_client import WhatsAppClient
    from app.models.whatsapp_operation import (
        WhatsAppOperation,
        WhatsAppOperationScenario,
        WhatsAppOperationStatus,
    )
    from app.services.operation_match_service import OperationMatchService, OutgoingCriteria

    phone = "573123146340"
    db.add(WhatsAppClient(phone=phone, display_name="Dionis"))
    db.add(FundGroupMember(group_id=fund.id, user_id=partner.id, whatsapp_phone=phone))
    anon = WhatsAppClient(
        phone=f"anon:partner:{partner.id}", display_name="Anónimo (vía socio)"
    )
    db.add(anon)
    db.flush()

    now = datetime.now(timezone.utc)
    op_row = WhatsAppOperation(
        client_id=anon.id, currency_pair_id=pairs["COP-VES"].id,
        from_amount=29998.31, to_amount=7612.167, rate_used=0.2455,
        amount_side="RECEIVE", status=WhatsAppOperationStatus.PENDING,
        scenario=WhatsAppOperationScenario.VIA_PARTNER,
        created_at=now, quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op_row)
    db.flush()

    matched = OperationMatchService(db).auto_match(
        OutgoingCriteria(amount=7612.17, currency="VES"), phone=phone
    )
    assert matched is not None and matched.id == op_row.id


def test_an_operation_born_from_a_partial_payout_still_suggests_itself_to_its_siblings(
    db, fund, client, operator
):
    """
    El caso real (op 3898, 2026-08-27): 350 → 315.000 Bs pagados con TRES pagos móviles.
    El operador arma la operación desde el primero (65.723 Bs, ~73 USD) y luego quiere
    engancharle los otros dos, pero la operación no aparecía entre las sugeridas.

    Crear la op desde un comprobante de salida afirmaba que ese pago la cubría ENTERA: el
    pendiente quedaba en cero, el prorrateo de `expected_amount` no se activaba y los otros
    comprobantes se comparaban contra los 315.000 completos, fuera de toda tolerancia.
    """
    from app.services.operation_match_service import OperationMatchService
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    payment_service = WhatsAppPaymentService(db)
    primero = f.outgoing(db, 65_723, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        payment_service, "outgoing", primero, frm="ZELLE", to="VES",
        from_amount=350, to_amount=315_000, recorded_by=operator.id,
    )

    # Cubre lo suyo a la tasa de la op (65.723 / 900 ≈ 73,03), no los 350 enteros.
    assert op["delivered_amount"] == pytest.approx(73.03, abs=0.01)
    assert op["pending_amount"] == pytest.approx(276.97, abs=0.01)

    # El segundo pago móvil cubre justo el pendiente prorrateado (315.000 × 276,97/350).
    segundo = f.outgoing(db, 250_000, "VES", phone="584148861273")
    db.flush()
    result = OperationMatchService(db).rank_for_payment(segundo.id, "outgoing")

    assert result.suggestion is not None, "la op con saldo pendiente tiene que aparecer entre las sugeridas"
    assert str(result.suggestion.uuid) == str(op["uuid"])


def test_an_operation_paid_in_full_by_one_receipt_is_settled_whole(db, fund, client, operator):
    """
    Guardarraíl del corte: cuando el comprobante SÍ es la pata que sale, la operación nace
    saldada y con el valor que puso el operador —incluido el descuento, que es a propósito—.
    """
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    payment_service = WhatsAppPaymentService(db)
    pago = f.outgoing(db, 315_000, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        payment_service, "outgoing", pago, frm="ZELLE", to="VES",
        from_amount=350, to_amount=315_000, recorded_by=operator.id,
    )
    assert op["delivered_amount"] == pytest.approx(350, abs=0.01)
    assert op["pending_amount"] == pytest.approx(0, abs=0.01)


def test_the_bank_code_no_longer_drags_the_wrong_operation_back_in():
    """
    Caso real (pago 5079, 2026-08-27): dos tratos del mismo monto, mismo banco, personas
    distintas. La cédula y el teléfono señalan uno solo, pero el 0102 —que comparten— hacía
    que los dos «coincidieran» y el bot se abstenía. Banco de Venezuela es el 0102: emparejar
    por ahí no distingue nada.
    """
    candidates = [
        op("op-otra", 15826.496, notes="0102\nV30166167\n04147925265"),
        op("op-buena", 15826.496, notes="0102\nV14110025\n04221411002"),
    ]
    c = criteria(15826.5, identification="V14110025", phone_to="04221411002", bank_to="0102")
    assert pick_auto_match(candidates, c, NOW) == "op-buena"


def test_the_best_match_wins_over_a_partial_one():
    """Gana la que calza en MÁS datos, no «la única que calza en alguno»."""
    candidates = [
        op("op-a-medias", 500.0, notes="0134\nV14110025\n04140000000"),
        op("op-completa", 500.0, notes="0102\nV14110025\n04221411002"),
    ]
    c = criteria(500.0, identification="V14110025", phone_to="04221411002")
    assert pick_auto_match(candidates, c, NOW) == "op-completa"


def test_a_tie_at_the_top_is_still_ambiguous():
    """Dos que calzan igual de bien siguen siendo dudosas: el bot no adivina."""
    candidates = [
        op("op-1", 500.0, notes="V14110025"),
        op("op-2", 500.0, notes="V14110025"),
    ]
    c = criteria(500.0, identification="V14110025")
    assert pick_auto_match(candidates, c, NOW) is None
