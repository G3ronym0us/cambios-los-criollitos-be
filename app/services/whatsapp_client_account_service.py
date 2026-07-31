"""
Libreta de cuentas de un cliente: resolver un nombre y aprender cuentas nuevas.

La resolución vive acá (no en el bot) por la dirección del rediseño bot→gateway: el bot
extrae el nombre del texto y el backend decide con qué cuenta se paga.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_client_account import WhatsAppClientAccount
from app.models.whatsapp_operation import WhatsAppOperation
from app.repositories.whatsapp_client_account_repository import WhatsAppClientAccountRepository
from app.schemas.client_account import AccountResolveResponse, ClientAccountResponse
from app.services.beneficiary_accounts import build_payment_block, normalize_alias


class WhatsAppClientAccountService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = WhatsAppClientAccountRepository(db)

    def _client_by_phone(self, phone: str) -> Optional[WhatsAppClient]:
        return self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()

    def resolve(self, phone: str, alias: str, currency: str) -> AccountResolveResponse:
        client = self._client_by_phone(phone)
        if client is None:
            return AccountResolveResponse(status="NONE")

        matches = self.repo.find_by_alias(client.id, alias, currency)
        if not matches:
            return AccountResolveResponse(status="NONE")
        if len(matches) > 1:
            return AccountResolveResponse(
                status="AMBIGUOUS",
                candidates=[ClientAccountResponse.model_validate(m) for m in matches],
            )

        account = matches[0]
        self.repo.touch(account)
        return AccountResolveResponse(
            status="MATCH",
            account=ClientAccountResponse.model_validate(account),
        )

    def _received_currency(self, op: WhatsAppOperation) -> Optional[str]:
        """
        Moneda que recibe el cliente. `op.currency` es lo que entrega; la otra punta del
        par es lo que recibe, sin importar en qué sentido quedó registrado el par.
        """
        cp = op.currency_pair
        if cp is None:
            return None
        from_sym = cp.from_currency.symbol.upper() if cp.from_currency else None
        to_sym = cp.to_currency.symbol.upper() if cp.to_currency else None
        given = op.currency.upper() if op.currency else None
        if given and given == from_sym:
            return to_sym
        if given and given == to_sym:
            return from_sym
        return to_sym

    def learn(
        self,
        op: WhatsAppOperation,
        payment_info: Optional[str],
        source: str,
    ) -> Optional[WhatsAppClientAccount]:
        """
        Guarda la cuenta del beneficiario que la operación nombró y todavía no tenía.

        No aprende (devuelve None) si: la op no nombró a nadie, ya tiene cuenta vinculada,
        el nombre resolvió ambiguo (habría una tercera cuenta con el mismo nombre), o el
        bloque de datos viene vacío.
        """
        if not op.beneficiary_alias or op.beneficiary_account_id is not None:
            return None
        if op.beneficiary_ambiguous:
            return None
        block = (payment_info or "").strip()
        if not block:
            return None

        currency = self._received_currency(op)
        if not currency:
            return None

        # La misma cuenta ya guardada: sólo se le pone el nombre si no lo tenía.
        existing = self.repo.get_by_payment_info(op.client_id, block)
        if existing is not None:
            if existing.alias is None:
                existing.alias = op.beneficiary_alias
                existing.alias_normalized = normalize_alias(op.beneficiary_alias)
            op.beneficiary_account_id = existing.id
            self.db.commit()
            return existing

        alias_norm = normalize_alias(op.beneficiary_alias)
        same_name = self.repo.find_same_alias(op.client_id, alias_norm, currency) if alias_norm else []
        confirmed = [a for a in same_name if a.is_confirmed]
        unconfirmed = [a for a in same_name if not a.is_confirmed]

        # Una cuenta confirmada nunca se pisa: si el bloque nuevo difiere, conviven las dos
        # y la próxima resolución de ese nombre devuelve AMBIGUOUS (el bot se detiene).
        if not confirmed and len(unconfirmed) == 1:
            account = unconfirmed[0]
            account.payment_info = block
            account.source = source
            op.beneficiary_account_id = account.id
            self.db.commit()
            self.db.refresh(account)
            return account

        account = self.repo.create(
            client_id=op.client_id,
            alias=op.beneficiary_alias,
            payment_info=block,
            currency=currency,
            source=source,
            is_confirmed=False,
        )
        op.beneficiary_account_id = account.id
        self.db.commit()
        return account

    def learn_from_outgoing(self, op: WhatsAppOperation, payment) -> Optional[WhatsAppClientAccount]:
        """
        Aprende del comprobante saliente ya pagado. Los campos vienen estructurados desde el
        bot (`extractOutgoingPaymentFields`), así que no hace falta reparsear el OCR. Si no
        alcanzan para armar un bloque con el que se pueda pagar, no se guarda nada.
        """
        block = build_payment_block(
            payment.account_number,
            payment.identification,
            payment.phone_to,
            payment.bank_to,
        )
        if block is None:
            return None
        return self.learn(op, block, source="RECEIPT")

    def default_payment_fields(self, client: WhatsAppClient) -> "tuple[Optional[str], Optional[str]]":
        """
        Compatibilidad: `default_payment_info`/`default_payment_currency` ya no se leen de
        `whatsapp_clients` sino de la cuenta predeterminada. Se siguen exponiendo con el
        mismo nombre para que un bot desplegado antes que este backend no se rompa.
        """
        account = self.repo.get_default(client.id)
        if account is None:
            return None, None
        return account.payment_info, account.currency

    def set_default_account(
        self, client: WhatsAppClient, payment_info: Optional[str], currency: Optional[str]
    ) -> None:
        """
        Crea, reemplaza o borra la cuenta predeterminada (la de `alias=NULL`). Es el camino
        que usa la UI vieja de "datos de pago del cliente".
        """
        current = self.repo.get_default(client.id)
        block = (payment_info or "").strip()
        if not block:
            if current is not None:
                self.repo.delete(current)
            return
        if not currency:
            return
        if current is not None:
            current.payment_info = block
            current.currency = currency.upper()
            current.is_confirmed = True
            self.db.commit()
            return
        self.repo.create(
            client_id=client.id,
            alias=None,
            payment_info=block,
            currency=currency,
            source="MANUAL",
            is_confirmed=True,
            is_default=True,
        )
