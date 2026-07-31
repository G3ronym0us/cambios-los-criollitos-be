import pytest

from app.services.beneficiary_accounts import (
    alias_matches,
    build_payment_block,
    normalize_alias,
)


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
