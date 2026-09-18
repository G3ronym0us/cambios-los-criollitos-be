"""add the default fund of each leg to currency_pairs

A new operation used to get its funds from the CURRENCY of each leg: the only active fund in
that currency. The currency says nothing about the business -- 664 USD-VES operations, which
are cash, landed in `Zelle/Paypal` just because it is the dollar fund -- so the pair becomes
the only source of the default fund, with the share of the margin each fund keeps.

So that nothing changes on deploy, every pair gets the fund the currency rule gives it today
(ZELLE and PAYPAL settle as USD; a currency with zero or several active funds gives none),
except:
  - USD-VES (cash) and USDT-USDT (a 1:1 parity that only carries percentages): no fund;
  - the pairs in OVERRIDES, where the currency says nothing -- USDT and VES have no fund of
    their own -- and the operator settled it: USDT-VES enters through Zelle, ZELLE-COP and
    PAYPAL-BRL pay out of Colombia and Brasil (never recorded before, though that is where
    the money comes from), and USD-BRL takes cash dollars in and pays out of Brasil;
  - ZELLE-BRL, which also gets its 7% Zelle / 3% Brasil split.

Without a fund the operation also loses its automatic scenario: `_resolve_scenario_for_new_op`
reads the partner off the fund, so an unconfigured pair stops classifying VIA_PARTNER /
ZELLE_DIRECT and falls back to NORMAL. That is why these seven are configured and not left
for later.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-15
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

_SETTLEMENT = {"ZELLE": "USD", "PAYPAL": "USD"}

#: Sin fondo a propósito: USD-VES se cambia en efectivo, y USDT-USDT es la paridad 1:1 que
#: sólo existe para colgar porcentajes — no mueve plata.
UNCONFIGURED = {"USD-VES", "USDT-USDT"}

#: Pares donde la moneda no acierta y manda el negocio (decidido con el operador, 18-09-2026):
#: USDT y VES no tienen fondo, así que la regla por moneda los dejaría en blanco, y en
#: ZELLE-COP y PAYPAL-BRL la pata de salida nunca se registró aunque el dinero salga de ahí.
#: Los dólares de USD-BRL entran en físico, como en USD-VES: sin fondo de entrada.
OVERRIDES = {
    "USDT-VES":   ("Zelle/Paypal", None),
    "ZELLE-COP":  ("Zelle/Paypal", "Cambios Colombia"),
    "PAYPAL-BRL": ("Zelle/Paypal", "Cambios Brasil"),
    "USDT-BRL":   (None, "Cambios Brasil"),
    "VES-BRL":    (None, "Cambios Brasil"),
    "USD-BRL":    (None, "Cambios Brasil"),
}

#: Puntos del margen por pata (entrada, salida).
SPLITS = {"ZELLE-BRL": (7.0, 3.0)}


def seed_default_funds(conn) -> None:
    funds_by_currency: dict[str, list[int]] = {}
    funds_by_name: dict[str, int] = {}
    for fund_id, currency, name in conn.execute(
        sa.text("SELECT id, upper(currency), name FROM fund_groups WHERE is_active")
    ):
        funds_by_currency.setdefault(currency, []).append(fund_id)
        funds_by_name[name] = fund_id

    def fund_for(symbol: str):
        candidates = funds_by_currency.get(_SETTLEMENT.get(symbol, symbol), [])
        return candidates[0] if len(candidates) == 1 else None

    pairs = conn.execute(sa.text(
        "SELECT cp.id, cp.pair_symbol, upper(f.symbol), upper(t.symbol)"
        " FROM currency_pairs cp"
        " JOIN currencies f ON f.id = cp.from_currency_id"
        " JOIN currencies t ON t.id = cp.to_currency_id"
    )).fetchall()

    for pair_id, symbol, from_symbol, to_symbol in pairs:
        if symbol in UNCONFIGURED:
            continue
        if symbol in OVERRIDES:
            name_in, name_out = OVERRIDES[symbol]
            fund_in = funds_by_name.get(name_in) if name_in else None
            fund_out = funds_by_name.get(name_out) if name_out else None
        else:
            fund_in = fund_for(from_symbol)
            fund_out = fund_for(to_symbol)
        pct_in, pct_out = SPLITS.get(symbol, (None, None))
        conn.execute(
            sa.text(
                "UPDATE currency_pairs SET"
                " default_fund_in_id = :fund_in, default_fund_out_id = :fund_out,"
                " default_fund_in_profit_pct = :pct_in, default_fund_out_profit_pct = :pct_out"
                " WHERE id = :id"
            ),
            {
                "id": pair_id,
                "fund_in": fund_in,
                "fund_out": fund_out,
                # Un porcentaje sin su fondo no significa nada.
                "pct_in": pct_in if fund_in is not None else None,
                "pct_out": pct_out if fund_out is not None else None,
            },
        )


def upgrade() -> None:
    op.add_column("currency_pairs", sa.Column("default_fund_in_id", sa.Integer(), nullable=True))
    op.add_column("currency_pairs", sa.Column("default_fund_in_profit_pct", sa.Float(), nullable=True))
    op.add_column("currency_pairs", sa.Column("default_fund_out_id", sa.Integer(), nullable=True))
    op.add_column("currency_pairs", sa.Column("default_fund_out_profit_pct", sa.Float(), nullable=True))
    op.create_foreign_key(
        "fk_currency_pairs_default_fund_in", "currency_pairs", "fund_groups",
        ["default_fund_in_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_currency_pairs_default_fund_out", "currency_pairs", "fund_groups",
        ["default_fund_out_id"], ["id"], ondelete="SET NULL",
    )
    seed_default_funds(op.get_bind())


def downgrade() -> None:
    op.drop_constraint("fk_currency_pairs_default_fund_out", "currency_pairs", type_="foreignkey")
    op.drop_constraint("fk_currency_pairs_default_fund_in", "currency_pairs", type_="foreignkey")
    op.drop_column("currency_pairs", "default_fund_out_profit_pct")
    op.drop_column("currency_pairs", "default_fund_out_id")
    op.drop_column("currency_pairs", "default_fund_in_profit_pct")
    op.drop_column("currency_pairs", "default_fund_in_id")
