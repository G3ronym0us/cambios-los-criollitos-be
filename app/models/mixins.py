"""
Mixins para modelos SQLAlchemy
"""
import uuid
from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID


class UUIDMixin:
    """
    Mixin para agregar campo UUID a los modelos

    El UUID se usa como identificador externo público mientras que el ID
    sigue siendo la primary key para mejor rendimiento en JOINs.
    """
    uuid = Column(
        UUID(as_uuid=True),
        unique=True,
        nullable=False,
        default=uuid.uuid4,
        index=True
    )
