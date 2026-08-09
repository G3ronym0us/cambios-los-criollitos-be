import pytest

from app.services.beneficiary_accounts import (
    alias_matches,
    build_payment_block,
    extract_masked_destination,
    masked_matches_account,
    normalize_alias,
)

# OCR de un "Transferencias a terceros" del BDV: sin cédula y con las dos cuentas tapadas.
BDV_RECEIPT = """Transferencias a terceros
7.337,81 Bs
Fecha: 06/08/2026
Operación: 059134978386
Nombre: Amelida Josefina Bastardo
Origen: 0102****6476
Destino: 0102****3817
Concepto: pago"""


class TestNormalizeAlias:
    def test_lowercases_and_strips_accents(self):
        assert normalize_alias("Yelitza Bolívar") == "yelitza bolivar"

    def test_collapses_whitespace(self):
        assert normalize_alias("  Yelitza   Bolivar \n") == "yelitza bolivar"

    def test_none_and_blank_are_none(self):
        assert normalize_alias(None) is None
        assert normalize_alias("   ") is None


class TestAliasMatches:
    def test_query_subset_of_alias(self):
        assert alias_matches("yelitza", "yelitza bolivar") is True

    def test_alias_subset_of_query(self):
        assert alias_matches("yelitza bolivar", "yelitza") is True

    def test_exact_match(self):
        assert alias_matches("yelitza bolivar", "yelitza bolivar") is True

    def test_partial_overlap_does_not_match(self):
        # Comparten "yelitza" pero ninguno contiene al otro.
        assert alias_matches("yelitza perez", "yelitza bolivar") is False

    def test_different_names_do_not_match(self):
        assert alias_matches("maria", "yelitza bolivar") is False

    def test_alias_none_does_not_match(self):
        assert alias_matches("yelitza", None) is False

    def test_prefix_of_a_word_does_not_match(self):
        # "yeli" no es "yelitza": el emparejamiento es por palabra completa.
        assert alias_matches("yeli", "yelitza bolivar") is False


class TestBuildPaymentBlock:
    def test_transfer_uses_account_and_id(self):
        assert build_payment_block("01020113121301031941", "V12345678", None, "0102") == (
            "01020113121301031941\nV12345678"
        )

    def test_mobile_payment_uses_bank_id_phone(self):
        assert build_payment_block(None, "12345678", "04147612526", "0102") == (
            "0102\nV12345678\n04147612526"
        )

    def test_id_keeps_explicit_prefix(self):
        assert build_payment_block(None, "E12345678", "04147612526", "0102") == (
            "0102\nE12345678\n04147612526"
        )

    def test_incomplete_data_returns_none(self):
        assert build_payment_block(None, None, "04147612526", "0102") is None
        assert build_payment_block(None, "V12345678", None, "0102") is None
        assert build_payment_block(None, "V12345678", "04147612526", None) is None
        assert build_payment_block(None, None, None, None) is None

    def test_account_without_id_returns_none(self):
        assert build_payment_block("01020113121301031941", None, None, "0102") is None


class TestExtractMaskedDestination:
    def test_reads_destination_and_ignores_origin(self):
        assert extract_masked_destination(BDV_RECEIPT) == ("0102", "3817")

    def test_accepts_other_labels_and_mask_characters(self):
        assert extract_masked_destination("Cuenta receptor 0134 xxxx 0098") == ("0134", "0098")
        assert extract_masked_destination("Beneficiaria: 0105••••7788") == ("0105", "7788")

    def test_column_split_ocr_returns_none(self):
        # La etiqueta y su valor quedaron en líneas distintas: mirar la siguiente daría la
        # cuenta del origen, así que no se devuelve nada.
        split = "Origen:\nDestino:\n0102****6476\n0102****3817"
        assert extract_masked_destination(split) is None

    def test_origin_only_is_not_a_destination(self):
        assert extract_masked_destination("Cuenta origen: 0102****6476") is None

    def test_amounts_and_dates_are_not_accounts(self):
        assert extract_masked_destination("Destino: pago 7.337,81 Bs el 06/08/2026") is None

    def test_none_and_blank(self):
        assert extract_masked_destination(None) is None
        assert extract_masked_destination("") is None


class TestMaskedMatchesAccount:
    def test_matches_bank_and_last_four(self):
        assert masked_matches_account(("0102", "1941"), "01020113121301031941\nV12345678") is True

    def test_finds_account_anywhere_in_the_block(self):
        block = "Banesco\n01340866100001310098\n26640340\nKelly zitman\n04126882238"
        assert masked_matches_account(("0134", "0098"), block) is True

    def test_same_last_four_at_another_bank_does_not_match(self):
        assert masked_matches_account(("0134", "1941"), "01020113121301031941\nV12345678") is False

    def test_different_last_four_does_not_match(self):
        assert masked_matches_account(("0102", "3817"), "01020113121301031941\nV12345678") is False

    def test_mobile_payment_block_has_no_account_to_match(self):
        assert masked_matches_account(("0102", "2526"), "0102\nV12345678\n04147612526") is False

    def test_empty_block(self):
        assert masked_matches_account(("0102", "3817"), None) is False
