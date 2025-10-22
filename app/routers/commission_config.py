from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID

from app.database.connection import get_db
from app.schemas.commission_config import (
    CommissionConfigCreate, CommissionConfigUpdate, CommissionConfigResponse,
    CommissionConfigList, PairConfigsResponse, ConfigSplitResponse
)
from app.repositories.commission_config_repository import CommissionConfigRepository
from app.repositories.user_repository import UserRepository
from app.models.user import User
from app.core.dependencies import get_current_user, get_moderator_user

router = APIRouter(prefix="/commission-configs", tags=["Commission Configurations"])


@router.post("", response_model=CommissionConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_commission_config(
    config_data: CommissionConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Crear nueva configuración de comisiones para un par de divisas

    **Requiere**: Permisos de moderador o superior

    **Ejemplo**:
    ```json
    {
      "currency_pair_id": 5,
      "name": "Configuración Estándar",
      "description": "División equitativa entre 2 operadores",
      "total_percentage": 10.0,
      "splits": [
        {"user_id": 5, "percentage": 5.0},
        {"user_id": 8, "percentage": 5.0}
      ]
    }
    ```
    """
    config_repo = CommissionConfigRepository(db)
    user_repo = UserRepository(db)

    # Validar que todos los usuarios existan y puedan recibir comisiones
    for split in config_data.splits:
        user = user_repo.get_by_uuid(split.user_uuid)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with UUID {split.user_uuid} not found"
            )
        if not user.can_receive_commission:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User '{user.username}' cannot receive commissions"
            )

    # Crear configuración
    config = config_repo.create_configuration(config_data, current_user.id)

    # Enriquecer respuesta con información de usuarios
    response = CommissionConfigResponse(**config.dict())
    response.splits = [
        ConfigSplitResponse(
            **split.dict(),
            username=split.user.username if split.user else None,
            user_full_name=split.user.full_name if split.user else None
        )
        for split in config.splits
    ]

    return response


@router.get("/pairs/{currency_pair_uuid}", response_model=PairConfigsResponse)
async def get_configs_by_pair(
    currency_pair_uuid: UUID,
    only_active: bool = Query(True, description="Solo configuraciones activas"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener todas las configuraciones disponibles para un par de divisas

    **Uso**: Listar opciones al crear transacción para un par específico

    **Ejemplo**: GET `/commission-configs/pairs/{uuid}?only_active=true`
    """
    config_repo = CommissionConfigRepository(db)

    # Get currency pair by UUID to get the ID
    from app.repositories.currency_pair_repository import CurrencyPairRepository
    pair_repo = CurrencyPairRepository(db)
    currency_pair = pair_repo.get_by_uuid(currency_pair_uuid)

    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair with UUID {currency_pair_uuid} not found"
        )

    configs = config_repo.get_by_pair(currency_pair.id, only_active)

    # Enriquecer respuestas con información de usuarios
    enriched_configs = []
    for config in configs:
        response = CommissionConfigResponse(**config.dict())
        response.splits = [
            ConfigSplitResponse(
                **split.dict(),
                username=split.user.username if split.user else None,
                user_full_name=split.user.full_name if split.user else None
            )
            for split in config.splits
        ]
        enriched_configs.append(response)

    return PairConfigsResponse(
        currency_pair_uuid=currency_pair_uuid,
        pair_symbol=configs[0].currency_pair.pair_symbol if configs and configs[0].currency_pair else None,
        configurations=enriched_configs,
        total=len(enriched_configs)
    )


@router.get("/pairs", response_model=List[int])
async def get_available_pairs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener lista de IDs de pares de divisas con configuraciones activas

    **Uso**: Dropdown para seleccionar par al crear transacción

    **Retorna**: [1, 5, 8, ...] (IDs de currency_pairs)
    """
    config_repo = CommissionConfigRepository(db)
    return config_repo.get_available_pairs()


@router.get("", response_model=CommissionConfigList)
async def get_all_commission_configs(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    currency_pair_id: Optional[int] = Query(None, description="Filtrar por ID de par de divisas"),
    only_active: bool = Query(False, description="Solo configuraciones activas"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Listar todas las configuraciones de comisiones con filtros

    **Requiere**: Permisos de moderador o superior
    """
    config_repo = CommissionConfigRepository(db)

    skip = (page - 1) * per_page
    configs, total = config_repo.get_all_configurations(
        skip=skip,
        limit=per_page,
        currency_pair_id=currency_pair_id,
        only_active=only_active
    )

    total_pages = (total + per_page - 1) // per_page

    # Enriquecer respuestas
    enriched_configs = []
    for config in configs:
        response = CommissionConfigResponse(**config.dict())
        response.splits = [
            ConfigSplitResponse(
                **split.dict(),
                username=split.user.username if split.user else None,
                user_full_name=split.user.full_name if split.user else None
            )
            for split in config.splits
        ]
        enriched_configs.append(response)

    return CommissionConfigList(
        configurations=enriched_configs,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )


@router.get("/{config_uuid}", response_model=CommissionConfigResponse)
async def get_commission_config(
    config_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obtener configuración por UUID con detalles completos"""
    config_repo = CommissionConfigRepository(db)
    config = config_repo.get_by_uuid(config_uuid)

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commission configuration with UUID {config_uuid} not found"
        )

    # Enriquecer respuesta
    response = CommissionConfigResponse(**config.dict())
    response.splits = [
        ConfigSplitResponse(
            **split.dict(),
            username=split.user.username if split.user else None,
            user_full_name=split.user.full_name if split.user else None
        )
        for split in config.splits
    ]

    return response


@router.put("/{config_uuid}", response_model=CommissionConfigResponse)
async def update_commission_config(
    config_uuid: UUID,
    config_data: CommissionConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Actualizar configuración de comisiones

    **Requiere**: Permisos de moderador o superior
    """
    config_repo = CommissionConfigRepository(db)
    user_repo = UserRepository(db)

    # Verificar que la configuración existe
    config = config_repo.get_by_uuid(config_uuid)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commission configuration with UUID {config_uuid} not found"
        )

    # Si se actualizan splits, validar usuarios
    if config_data.splits:
        for split in config_data.splits:
            user = user_repo.get_by_uuid(split.user_uuid)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with UUID {split.user_uuid} not found"
                )
            if not user.can_receive_commission:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"User '{user.username}' cannot receive commissions"
                )

    # Actualizar
    updated_config = config_repo.update_configuration(config.id, config_data)

    if not updated_config:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update configuration"
        )

    # Enriquecer respuesta
    response = CommissionConfigResponse(**updated_config.dict())
    response.splits = [
        ConfigSplitResponse(
            **split.dict(),
            username=split.user.username if split.user else None,
            user_full_name=split.user.full_name if split.user else None
        )
        for split in updated_config.splits
    ]

    return response


@router.delete("/{config_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_commission_config(
    config_uuid: UUID,
    soft_delete: bool = Query(True, description="Si True, desactiva; si False, elimina físicamente"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Eliminar o desactivar configuración

    **Requiere**: Permisos de moderador o superior

    **Params**:
    - `soft_delete=true`: Desactiva la configuración (is_active=false)
    - `soft_delete=false`: Elimina físicamente la configuración
    """
    config_repo = CommissionConfigRepository(db)

    # Verificar que existe
    config = config_repo.get_by_uuid(config_uuid)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commission configuration with UUID {config_uuid} not found"
        )

    if soft_delete:
        success = config_repo.deactivate_configuration(config.id)
    else:
        success = config_repo.delete_configuration(config.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete configuration"
        )


@router.get("/stats/summary")
async def get_config_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Obtener estadísticas de configuraciones

    **Requiere**: Permisos de moderador o superior
    """
    config_repo = CommissionConfigRepository(db)
    return config_repo.get_config_stats()
