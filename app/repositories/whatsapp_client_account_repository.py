"""
Acceso a datos de la libreta de cuentas de un cliente (`whatsapp_client_accounts`).
"""

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.whatsapp_client_account import WhatsAppClientAccount
from app.services.beneficiary_accounts import alias_matches, normalize_alias


class WhatsAppClientAccountRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_for_client(self, client_id: int) -> List[WhatsAppClientAccount]:
        return (
            self.db.query(WhatsAppClientAccount)
            .filter(WhatsAppClientAccount.client_id == client_id)
            .order_by(
                WhatsAppClientAccount.is_default.desc(),
                WhatsAppClientAccount.last_used_at.desc().nullslast(),
                WhatsAppClientAccount.created_at.desc(),
            )
            .all()
        )

    def get_by_uuid(self, account_uuid: UUID) -> Optional[WhatsAppClientAccount]:
        return (
            self.db.query(WhatsAppClientAccount)
            .filter(WhatsAppClientAccount.uuid == str(account_uuid))
            .first()
        )

    def get_default(self, client_id: int, currency: Optional[str] = None) -> Optional[WhatsAppClientAccount]:
        q = self.db.query(WhatsAppClientAccount).filter(
            WhatsAppClientAccount.client_id == client_id,
            WhatsAppClientAccount.is_default.is_(True),
        )
        if currency:
            q = q.filter(WhatsAppClientAccount.currency == currency.upper())
        return q.first()

    def find_by_alias(
        self, client_id: int, alias_query: str, currency: str
    ) -> List[WhatsAppClientAccount]:
        """
        Candidatas cuyo alias empareja con la consulta, ya filtradas por moneda. El
        emparejamiento es por tokens (`alias_matches`), no por SQL: la tabla por cliente es
        chica y la regla tiene que ser exactamente la misma que se prueba en pytest.
        """
        query_norm = normalize_alias(alias_query)
        if query_norm is None:
            return []
        rows = (
            self.db.query(WhatsAppClientAccount)
            .filter(
                WhatsAppClientAccount.client_id == client_id,
                WhatsAppClientAccount.currency == currency.upper(),
                WhatsAppClientAccount.alias_normalized.isnot(None),
            )
            .all()
        )
        return [r for r in rows if alias_matches(query_norm, r.alias_normalized)]

    def find_same_alias(
        self, client_id: int, alias_norm: str, currency: str
    ) -> List[WhatsAppClientAccount]:
        """Cuentas con exactamente ese alias normalizado y moneda (para el upsert al aprender)."""
        return (
            self.db.query(WhatsAppClientAccount)
            .filter(
                WhatsAppClientAccount.client_id == client_id,
                WhatsAppClientAccount.currency == currency.upper(),
                WhatsAppClientAccount.alias_normalized == alias_norm,
            )
            .all()
        )

    def get_by_payment_info(self, client_id: int, payment_info: str) -> Optional[WhatsAppClientAccount]:
        return (
            self.db.query(WhatsAppClientAccount)
            .filter(
                WhatsAppClientAccount.client_id == client_id,
                WhatsAppClientAccount.payment_info == payment_info,
            )
            .first()
        )

    def create(
        self,
        client_id: int,
        alias: Optional[str],
        payment_info: str,
        currency: str,
        source: str = "MANUAL",
        is_confirmed: bool = False,
        is_default: bool = False,
    ) -> WhatsAppClientAccount:
        account = WhatsAppClientAccount(
            client_id=client_id,
            alias=alias.strip() if alias else None,
            alias_normalized=normalize_alias(alias),
            payment_info=payment_info,
            currency=currency.upper(),
            source=source,
            is_confirmed=is_confirmed,
            is_default=False,
        )
        self.db.add(account)
        self.db.flush()
        if is_default:
            self.set_default(client_id, account)
        self.db.commit()
        self.db.refresh(account)
        return account

    def set_default(self, client_id: int, account: WhatsAppClientAccount) -> None:
        """Desmarca la predeterminada anterior y marca esta, en la misma transacción."""
        self.db.query(WhatsAppClientAccount).filter(
            WhatsAppClientAccount.client_id == client_id,
            WhatsAppClientAccount.is_default.is_(True),
            WhatsAppClientAccount.id != account.id,
        ).update({"is_default": False}, synchronize_session=False)
        account.is_default = True
        self.db.flush()

    def touch(self, account: WhatsAppClientAccount) -> None:
        account.last_used_at = datetime.now(timezone.utc)
        self.db.commit()

    def delete(self, account: WhatsAppClientAccount) -> None:
        self.db.delete(account)
        self.db.commit()
