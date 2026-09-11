"""
Router "Admin" — agregados de nivel panel, de cara al operador.

Por ahora solo `/admin/overview`, el que alimenta la home nueva. Vive aparte de
`payments`/`whatsapp`/`transactions` porque no es la bandeja de nada en particular: es la
composición de varias, pensada para una sola pantalla.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_admin_user
from app.database.connection import get_db
from app.models.user import User
from app.services.admin_overview_service import AdminOverviewService

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/overview")
async def get_admin_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    """
    El agregado de la home de admin: pagos por atender, operaciones accionables, mi ganancia
    de hoy y —solo para ROOT— divergencias de tasa y deuda con clientes.

    Un bloque que falla no tumba la respuesta: llega en `null` con su nombre en `errors`, y
    el resto se sirve igual con 200. `alerts`/`clients` van AUSENTES (no null) para un
    MODERATOR — son decisiones que solo toma ROOT.
    """
    return AdminOverviewService(db).get_overview(current_user)
