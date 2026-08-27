"""
Por dónde llega un fondo: su grupo de WhatsApp, o el chat directo con su gestor.

El jid del grupo era la única vía y daba por sentado que todo fondo se lleva en un grupo.
«Cambios Colombia» no: se maneja en el chat directo con Dionis, y por eso no tiene jid. Cada
camino que asumía lo contrario se rompió por su cuenta —el comprobante de reposición se
cotizaba como si fuera de un cliente (2026-08-25), y el reenvío contable del operador se
guardaba como un saliente fantasma (pago 4951, 2026-08-24)—, así que la regla vive en un solo
sitio y la usan todos.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.fund import FundGroup, FundGroupMember
from app.services.whatsapp_quote_service import QuoteServiceError


def resolve_fund_channel(
    db: Session,
    group_jid: Optional[str] = None,
    group_uuid: Optional[UUID] = None,
    manager_phone: Optional[str] = None,
) -> FundGroup:
    """
    El fondo detrás de un canal, sea el grupo o el gestor. Lanza `QuoteServiceError` si no
    hay ninguno, o si el teléfono no alcanza para decidir.
    """
    group = None
    if group_uuid is not None:
        group = db.query(FundGroup).filter(FundGroup.uuid == str(group_uuid)).first()
    elif group_jid:
        group = db.query(FundGroup).filter(FundGroup.whatsapp_group_jid == group_jid).first()
    elif manager_phone:
        grupos = (
            db.query(FundGroup)
            .join(FundGroupMember, FundGroupMember.group_id == FundGroup.id)
            .filter(
                FundGroupMember.whatsapp_phone == manager_phone,
                FundGroupMember.is_fund_manager.is_(True),
                FundGroup.is_active.is_(True),
            )
            .all()
        )
        # Gestionar más de un fondo es normal (el operador gestiona varios): ahí el teléfono
        # no alcanza para saber a cuál va, y adivinar sería mover capital al fondo
        # equivocado. Se rechaza y se pide el fondo explícito.
        if len(grupos) > 1:
            raise QuoteServiceError(
                "ambiguous_fund_group",
                f"{manager_phone} gestiona {len(grupos)} fondos: "
                f"{', '.join(g.name for g in grupos)}. Indicá cuál.",
                409,
            )
        group = grupos[0] if grupos else None
    if group is None:
        raise QuoteServiceError(
            "fund_group_not_found",
            f"Fondo para {group_uuid or group_jid or manager_phone} no encontrado",
            404,
        )
    return group
