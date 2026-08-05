"""
Clientes-entidad: negocios a los que el operador les presta o cobra pero que no tienen
teléfono propio en el bot (sus comprobantes se mandan al grupo del negocio).

Se guardan en `whatsapp_clients` con una clave sintética `entity:{slug}`, igual que los
anónimos usan `anon:group:{id}` / `anon:partner:{user_id}`. La diferencia: un `anon:` es un
marcador de "todavía no sabemos quién es" y varios flujos lo excluyen; una entidad es un
cliente de verdad, con ficha, historial y saldo.
"""

import re
import unicodedata
from typing import Optional

from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient
from app.services.whatsapp_quote_service import QuoteServiceError

ENTITY_PHONE_PREFIX = "entity:"


def is_entity_client_phone(phone: Optional[str]) -> bool:
    return bool(phone) and phone.startswith(ENTITY_PHONE_PREFIX)


def slugify(name: str) -> str:
    """«Bodegón X, C.A.» → «bodegon-x-c-a»."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


class ClientEntityService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, display_name: str, linked_group_jid: Optional[str] = None) -> WhatsAppClient:
        name = (display_name or "").strip()
        if not name:
            raise QuoteServiceError(
                "invalid_entity_name", "El nombre de la entidad es obligatorio", 400
            )
        slug = slugify(name)
        if not slug:
            raise QuoteServiceError(
                "invalid_entity_name", "El nombre debe tener al menos una letra o un número", 400
            )

        jid = (linked_group_jid or "").strip() or None
        if jid is not None:
            taken = (
                self.db.query(WhatsAppClient)
                .filter(WhatsAppClient.linked_group_jid == jid)
                .first()
            )
            if taken is not None:
                raise QuoteServiceError(
                    "group_already_linked",
                    f"Ese grupo ya está vinculado a «{taken.display_name}»",
                    409,
                )

        entity = WhatsAppClient(
            phone=self._free_phone(slug), display_name=name, linked_group_jid=jid
        )
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return entity

    def _free_phone(self, slug: str) -> str:
        """Dos negocios pueden llamarse igual; el segundo lleva sufijo."""
        phone = f"{ENTITY_PHONE_PREFIX}{slug}"
        suffix = 2
        while (
            self.db.query(WhatsAppClient.id).filter(WhatsAppClient.phone == phone).first()
            is not None
        ):
            phone = f"{ENTITY_PHONE_PREFIX}{slug}-{suffix}"
            suffix += 1
        return phone
