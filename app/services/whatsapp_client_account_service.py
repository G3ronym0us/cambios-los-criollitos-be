"""
Libreta de cuentas de un cliente: resolver un nombre y aprender cuentas nuevas.

La resolución vive acá (no en el bot) por la dirección del rediseño bot→gateway: el bot
extrae el nombre del texto y el backend decide con qué cuenta se paga.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient
from app.repositories.whatsapp_client_account_repository import WhatsAppClientAccountRepository
from app.schemas.client_account import AccountResolveResponse, ClientAccountResponse


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
