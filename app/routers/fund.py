from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from uuid import UUID

from app.database.connection import get_db
from app.schemas.fund import (
    FundGroupCreate, FundGroupUpdate, FundGroupResponse,
    FundGroupMemberCreate, FundGroupMemberUpdate, FundGroupMemberResponse,
    FundMovementCreate, FundMovementResponse, FundMovementList,
    UserPositionResponse, FundGroupBalanceResponse,
)
from app.repositories.fund_repository import FundRepository
from app.repositories.user_repository import UserRepository
from app.models.fund import FundMovementType
from app.models.user import User
from app.core.dependencies import get_current_user, get_moderator_user, get_root_user

router = APIRouter(prefix="/funds", tags=["Funds"])


# ===== Groups =====

@router.post("/groups", response_model=FundGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_fund_group(
    group_data: FundGroupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Crear un nuevo grupo de fondo (requiere moderador)"""
    fund_repo = FundRepository(db)

    if fund_repo.get_group_by_name(group_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A fund group named '{group_data.name}' already exists",
        )

    group = fund_repo.create_fund_group(
        name=group_data.name,
        currency=group_data.currency,
        description=group_data.description,
        whatsapp_group_jid=group_data.whatsapp_group_jid,
    )
    return group


@router.get("/groups", response_model=List[FundGroupResponse])
async def list_fund_groups(
    active_only: bool = Query(True, description="Solo grupos activos"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Listar grupos de fondo"""
    fund_repo = FundRepository(db)
    return fund_repo.get_groups(active_only=active_only)


@router.patch("/groups/{group_uuid}", response_model=FundGroupResponse)
async def update_fund_group(
    group_uuid: UUID,
    payload: FundGroupUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Actualizar un grupo (JID de WhatsApp). Requiere moderador."""
    fund_repo = FundRepository(db)
    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")
    if payload.clear_whatsapp_group_jid:
        fund_repo.update_group_whatsapp_jid(group, None)
    elif payload.whatsapp_group_jid is not None:
        fund_repo.update_group_whatsapp_jid(group, payload.whatsapp_group_jid)
    return fund_repo.get_group_by_uuid(group_uuid)


@router.post("/groups/{group_uuid}/members", response_model=FundGroupMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_group_member(
    group_uuid: UUID,
    member_data: FundGroupMemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Agregar miembro a un grupo de fondo (requiere moderador)"""
    fund_repo = FundRepository(db)
    user_repo = UserRepository(db)

    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")

    user = user_repo.get_by_uuid(member_data.user_uuid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if fund_repo.get_member(group.id, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User '{user.username}' is already a member of this group",
        )

    member = fund_repo.add_member(
        group_id=group.id,
        user_id=user.id,
        is_fund_manager=member_data.is_fund_manager,
        whatsapp_phone=member_data.whatsapp_phone,
    )
    return member


@router.patch("/groups/{group_uuid}/members/{user_uuid}", response_model=FundGroupMemberResponse)
async def update_group_member(
    group_uuid: UUID,
    user_uuid: UUID,
    payload: FundGroupMemberUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Actualizar un miembro (número de WhatsApp del socio / rol). Requiere moderador."""
    fund_repo = FundRepository(db)
    user_repo = UserRepository(db)

    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")
    user = user_repo.get_by_uuid(user_uuid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    member = fund_repo.get_member(group.id, user.id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this group")

    return fund_repo.update_member(
        member,
        is_fund_manager=payload.is_fund_manager,
        whatsapp_phone=payload.whatsapp_phone,
        clear_whatsapp_phone=payload.clear_whatsapp_phone,
    )


@router.delete("/groups/{group_uuid}/members/{user_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_group_member(
    group_uuid: UUID,
    user_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Eliminar miembro de un grupo de fondo (requiere moderador)"""
    fund_repo = FundRepository(db)
    user_repo = UserRepository(db)

    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")

    user = user_repo.get_by_uuid(user_uuid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    removed = fund_repo.remove_member(group.id, user.id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this group")


@router.get("/groups/{group_uuid}/balance", response_model=FundGroupBalanceResponse)
async def get_group_balance(
    group_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Balance consolidado del grupo (Total / Acumulada / Fondos)"""
    fund_repo = FundRepository(db)

    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")

    balance = fund_repo.get_group_balance(group.id)
    if not balance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Could not calculate group balance")

    return balance


@router.get("/groups/{group_uuid}/movements", response_model=FundMovementList)
async def list_group_movements(
    group_uuid: UUID,
    movement_type: Optional[str] = Query(None, description="Filtrar por tipo: DEPOSIT, EXCHANGE, PERSONAL, ADJUSTMENT"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Historial de movimientos de un grupo (requiere moderador)"""
    fund_repo = FundRepository(db)

    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")

    type_enum = None
    if movement_type:
        try:
            type_enum = FundMovementType(movement_type.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid movement_type '{movement_type}'. Valid: DEPOSIT, EXCHANGE, PERSONAL, ADJUSTMENT",
            )

    skip = (page - 1) * per_page
    movements, total = fund_repo.get_movements(
        group_id=group.id,
        movement_type=type_enum,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=per_page,
    )
    total_pages = (total + per_page - 1) // per_page
    return FundMovementList(
        movements=[_serialize_movement(m) for m in movements],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


# ===== Movements =====

@router.post("/movements", response_model=FundMovementResponse, status_code=status.HTTP_201_CREATED)
async def create_movement(
    movement_data: FundMovementCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Registrar un movimiento de fondo manualmente (requiere moderador)"""
    fund_repo = FundRepository(db)
    user_repo = UserRepository(db)

    group = fund_repo.get_group_by_uuid(movement_data.group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")

    user = user_repo.get_by_uuid(movement_data.user_uuid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Resolver transaction_id si se envía transaction_uuid
    transaction_id = None
    if movement_data.transaction_uuid:
        from app.repositories.transaction_repository import TransactionRepository
        tx_repo = TransactionRepository(db)
        tx = tx_repo.get_by_uuid(movement_data.transaction_uuid)
        if not tx:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
        transaction_id = tx.id

    movement_type = FundMovementType(movement_data.movement_type)

    movement = fund_repo.create_movement(
        group_id=group.id,
        user_id=user.id,
        movement_type=movement_type,
        amount=movement_data.amount,
        currency=movement_data.currency,
        movement_date=movement_data.movement_date,
        amount_usdt=movement_data.amount_usdt,
        usdt_rate=movement_data.usdt_rate,
        transaction_id=transaction_id,
        reference=movement_data.reference,
        notes=movement_data.notes,
        recorded_by_user_id=current_user.id,
    )
    return _serialize_movement(fund_repo.get_movement_by_uuid(movement.uuid))


@router.get("/movements/{movement_uuid}", response_model=FundMovementResponse)
async def get_movement(
    movement_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Detalle de un movimiento"""
    fund_repo = FundRepository(db)
    movement = fund_repo.get_movement_by_uuid(movement_uuid)
    if not movement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movement not found")
    return _serialize_movement(movement)


@router.delete("/movements/{movement_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_movement(
    movement_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user),
):
    """Eliminar un movimiento (requiere ROOT)"""
    fund_repo = FundRepository(db)
    movement = fund_repo.get_movement_by_uuid(movement_uuid)
    if not movement:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movement not found")

    fund_repo.delete_movement(movement.id)


# ===== User position =====

@router.get("/users/{user_uuid}/position", response_model=UserPositionResponse)
async def get_user_position(
    user_uuid: UUID,
    group_uuid: UUID = Query(..., description="UUID del grupo de fondo"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Posicion individual de un gestor dentro de un grupo.
    Usuarios solo pueden ver su propia posicion; moderadores pueden ver cualquiera.
    """
    fund_repo = FundRepository(db)
    user_repo = UserRepository(db)

    user = user_repo.get_by_uuid(user_uuid)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Solo el propio usuario o un moderador/admin puede ver la posicion
    if current_user.uuid != user_uuid and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own position",
        )

    group = fund_repo.get_group_by_uuid(group_uuid)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund group not found")

    position = fund_repo.get_user_position(user.id, group.id)
    if not position:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")

    return position


# ===== Serialization helper =====

def _serialize_movement(movement) -> dict:
    return {
        "uuid": movement.uuid,
        "group_uuid": movement.group.uuid if movement.group else None,
        "group_name": movement.group.name if movement.group else None,
        "user_uuid": movement.user.uuid if movement.user else None,
        "username": movement.user.username if movement.user else None,
        "movement_type": movement.movement_type.value,
        "amount": movement.amount,
        "currency": movement.currency,
        "amount_usdt": movement.amount_usdt,
        "usdt_rate": movement.usdt_rate,
        "transaction_uuid": movement.transaction.uuid if movement.transaction else None,
        "reference": movement.reference,
        "notes": movement.notes,
        "recorded_by_uuid": movement.recorded_by.uuid if movement.recorded_by else None,
        "recorded_by_username": movement.recorded_by.username if movement.recorded_by else None,
        "movement_date": movement.movement_date,
        "created_at": movement.created_at,
    }
