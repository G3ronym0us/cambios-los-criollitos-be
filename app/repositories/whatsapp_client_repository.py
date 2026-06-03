"""
Acceso a datos de clientes del bot (`whatsapp_clients`) para el front de operador.
"""

from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient


class WhatsAppClientRepository:
    def __init__(self, db: Session):
        self.db = db

    def _filtered_query(
        self,
        search: Optional[str] = None,
        is_blocked: Optional[bool] = None,
        is_tracked: Optional[bool] = None,
    ):
        q = self.db.query(WhatsAppClient)
        if search:
            like = f"%{search.strip()}%"
            q = q.filter(
                or_(
                    WhatsAppClient.display_name.ilike(like),
                    WhatsAppClient.phone.ilike(like),
                )
            )
        if is_blocked is not None:
            q = q.filter(WhatsAppClient.is_blocked == is_blocked)
        if is_tracked is not None:
            q = q.filter(WhatsAppClient.is_tracked == is_tracked)
        return q

    def list(
        self,
        skip: int = 0,
        limit: int = 100,
        search: Optional[str] = None,
        is_blocked: Optional[bool] = None,
        is_tracked: Optional[bool] = None,
    ) -> Tuple[List[WhatsAppClient], int]:
        q = self._filtered_query(search, is_blocked, is_tracked)
        total = q.count()
        items = (
            q.order_by(WhatsAppClient.last_seen_at.desc().nullslast())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return items, total

    def get_by_uuid(self, client_uuid: UUID) -> Optional[WhatsAppClient]:
        return (
            self.db.query(WhatsAppClient)
            .filter(WhatsAppClient.uuid == client_uuid)
            .first()
        )
