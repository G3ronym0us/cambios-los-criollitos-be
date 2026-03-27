from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
from uuid import UUID

from app.database.connection import get_db
from app.schemas.transaction import (
    TransactionCreate, TransactionUpdate, TransactionResponse, TransactionList,
    UserProfitReport, ProfitSummary, SimilarTransactionWarning
)
from app.repositories.transaction_repository import TransactionRepository
from app.models.transaction import TransactionStatus
from app.models.user import User
from app.core.dependencies import get_current_user, get_moderator_user

router = APIRouter(prefix="/transactions", tags=["Transactions"])


# ===== Helper Functions =====

def enrich_transaction_response(transaction, db: Session) -> dict:
    """
    Enriquecer una transacción con currency_pair_uuid, pair_symbol y símbolos de moneda del par.
    """
    cp = transaction.currency_pair
    response_data = transaction.dict()
    response_data['currency_pair_uuid'] = cp.uuid if cp else None
    response_data['pair_symbol'] = cp.pair_symbol if cp else None
    response_data['from_currency'] = cp.from_currency.symbol if cp else None
    response_data['to_currency'] = cp.to_currency.symbol if cp else None
    response_data['profit_splits'] = [split.dict() for split in transaction.profit_splits]
    return response_data


# ===== CRUD Endpoints =====

@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    transaction_data: TransactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crear nueva transacción con distribución de ganancias

    **Opción 1 - Usar configuración predefinida:**
    ```json
    {
      "currency_pair_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "from_amount": 100.0,
      "to_amount": 3650.0,
      "exchange_rate": 36.5,
      "description": "Transacción Zelle/VES cliente Juan",
      "commission_config_uuid": "a7f8e9d0-b3c4-4a5b-8c9d-0e1f2a3b4c5d"
    }
    ```

    **Opción 2 - Splits manuales:**
    ```json
    {
      "currency_pair_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "from_amount": 100.0,
      "to_amount": 3650.0,
      "exchange_rate": 36.5,
      "description": "Transacción Zelle/VES cliente Juan",
      "total_profit_percentage": 10.0,
      "profit_splits": [
        {"user_uuid": "b8f9e0d1-c4d5-5b6c-9d0e-1f2a3b4c5d6e", "profit_percentage": 5.0},
        {"user_uuid": "c9f0e1d2-d5e6-6c7d-0e1f-2a3b4c5d6e7f", "profit_percentage": 5.0}
      ]
    }
    ```
    """
    transaction_repo = TransactionRepository(db)
    from app.repositories.user_repository import UserRepository
    from app.repositories.commission_config_repository import CommissionConfigRepository
    from app.repositories.currency_pair_repository import CurrencyPairRepository
    from app.schemas.transaction import ProfitSplitCreate

    user_repo = UserRepository(db)
    config_repo = CommissionConfigRepository(db)
    pair_repo = CurrencyPairRepository(db)

    # Resolver currency_pair para obtener from_currency y to_currency
    currency_pair = pair_repo.get_by_uuid(transaction_data.currency_pair_uuid)
    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair with UUID {transaction_data.currency_pair_uuid} not found"
        )

    if not currency_pair.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Currency pair '{currency_pair.pair_symbol}' is not active"
        )

    # Verificar transacciones similares (solo si no es force=True)
    if not transaction_data.force:
        similar_transactions = transaction_repo.find_similar_transactions(
            currency_pair_id=currency_pair.id,
            from_amount=transaction_data.from_amount
        )

        if similar_transactions:
            # Tomar la primera (más reciente)
            similar = similar_transactions[0]

            # Enriquecer la transacción similar
            similar_response = enrich_transaction_response(similar, db)

            # Retornar advertencia
            warning_message = (
                f"Se encontró una transacción similar del mismo día. "
                f"Par: {currency_pair.pair_symbol}, "
                f"Monto: {similar.from_amount} {currency_pair.from_currency.symbol}, "
                f"Hora: {similar.created_at.strftime('%H:%M:%S')}. "
                f"Si desea continuar, envíe la solicitud con 'force': true."
            )

            warning_response = SimilarTransactionWarning(
                warning="Similar transaction found",
                similar_transaction=TransactionResponse(**similar_response),
                requires_confirmation=True,
                message=warning_message
            )

            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=jsonable_encoder(warning_response)
            )

    # Si se usa configuración predefinida, cargar splits
    config = None
    if transaction_data.commission_config_uuid:
        config = config_repo.get_by_uuid(transaction_data.commission_config_uuid)
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Commission configuration with UUID {transaction_data.commission_config_uuid} not found"
            )

        if not config.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Commission configuration '{config.name}' is not active"
            )

        # Cargar splits de la configuración
        transaction_data.total_profit_percentage = config.total_percentage
        transaction_data.profit_splits = [
            ProfitSplitCreate(user_uuid=split.user.uuid, profit_percentage=split.percentage)
            for split in config.splits
        ]

    # Validar que los usuarios de profit_splits existan
    if transaction_data.profit_splits:
        for split in transaction_data.profit_splits:
            if not user_repo.get_by_uuid(split.user_uuid):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with UUID {split.user_uuid} not found"
                )

    transaction = transaction_repo.create_transaction(
        transaction_data,
        created_by_user_id=current_user.id,
        currency_pair_id=currency_pair.id
    )

    # Crear FundMovement automático si aplica
    if not transaction_data.skip_fund:
        from app.repositories.fund_repository import FundRepository
        from app.models.fund import FundMovementType
        from datetime import datetime

        fund_repo = FundRepository(db)
        effective_fund_group = None

        if transaction_data.fund_group_uuid:
            # Override explícito en el request
            effective_fund_group = fund_repo.get_group_by_uuid(transaction_data.fund_group_uuid)
            if not effective_fund_group:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Fund group with UUID {transaction_data.fund_group_uuid} not found"
                )
        elif config is not None and config.fund_group_id:
            # Fondo configurado por defecto en la commission config
            effective_fund_group = config.fund_group

        if effective_fund_group:
            resolved_amount_usdt = None
            resolved_usdt_rate = transaction_data.usdt_rate

            if transaction_data.usdt_rate:
                # Explicit rate in request always wins
                resolved_amount_usdt = transaction.from_amount / transaction_data.usdt_rate
            elif currency_pair.usdt_reference_side:
                reference_amount = (
                    transaction.from_amount if currency_pair.usdt_reference_side == "FROM"
                    else transaction.to_amount
                )
                if reference_amount is not None:
                    if currency_pair.usdt_manual_rate is not None:
                        resolved_amount_usdt = reference_amount * currency_pair.usdt_manual_rate
                        resolved_usdt_rate = currency_pair.usdt_manual_rate
                    elif currency_pair.usdt_pair_id:
                        from app.repositories.exchange_rate_repository import ExchangeRateRepository
                        er = ExchangeRateRepository(db).get_latest_rate_by_pair_id(currency_pair.usdt_pair_id)
                        if er:
                            rate = (1.0 / er.rate) if currency_pair.usdt_pair_inverse else er.rate
                            resolved_amount_usdt = reference_amount * rate
                            resolved_usdt_rate = rate

            fund_repo.create_movement(
                group_id=effective_fund_group.id,
                user_id=current_user.id,
                movement_type=FundMovementType.EXCHANGE,
                amount=transaction.from_amount,
                currency=currency_pair.from_currency.symbol,
                movement_date=datetime.utcnow(),
                amount_usdt=resolved_amount_usdt,
                usdt_rate=resolved_usdt_rate,
                transaction_id=transaction.id,
                recorded_by_user_id=current_user.id,
            )

    return TransactionResponse(**enrich_transaction_response(transaction, db))


@router.get("", response_model=TransactionList)
async def get_transactions(
    page: int = Query(1, ge=1, description="Número de página"),
    per_page: int = Query(20, ge=1, le=100, description="Transacciones por página"),
    status_filter: Optional[str] = Query(None, description="Filtrar por status: pending, completed, cancelled, failed"),
    currency_pair_uuid: Optional[UUID] = Query(None, description="Filtrar por par de monedas (UUID)"),
    user_id: Optional[int] = Query(None, description="Filtrar por usuario"),
    start_date: Optional[datetime] = Query(None, description="Fecha inicio (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="Fecha fin (ISO format)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obtener lista de transacciones con filtros y paginación"""
    transaction_repo = TransactionRepository(db)

    # Convertir status string a enum si se proporciona
    status_enum = None
    if status_filter:
        try:
            status_enum = TransactionStatus(status_filter.lower())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}. Valid options: pending, completed, cancelled, failed"
            )

    # Resolver currency_pair_uuid a currency_pair_id
    cp_id = None
    if currency_pair_uuid:
        from app.repositories.currency_pair_repository import CurrencyPairRepository
        pair = CurrencyPairRepository(db).get_by_uuid(currency_pair_uuid)
        if not pair:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Currency pair not found")
        cp_id = pair.id

    skip = (page - 1) * per_page
    transactions, total = transaction_repo.get_all_transactions(
        skip=skip,
        limit=per_page,
        status=status_enum,
        currency_pair_id=cp_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date
    )

    total_pages = (total + per_page - 1) // per_page

    return TransactionList(
        transactions=[TransactionResponse(**enrich_transaction_response(t, db)) for t in transactions],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )


@router.get("/recent", response_model=List[TransactionResponse])
async def get_recent_transactions(
    limit: int = Query(10, ge=1, le=50, description="Número de transacciones recientes"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obtener las transacciones más recientes"""
    transaction_repo = TransactionRepository(db)
    transactions = transaction_repo.get_recent_transactions(limit=limit)

    return [TransactionResponse(**enrich_transaction_response(t, db)) for t in transactions]


@router.get("/stats")
async def get_transaction_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obtener estadísticas generales de transacciones"""
    transaction_repo = TransactionRepository(db)
    return transaction_repo.get_transaction_stats()


@router.get("/{transaction_uuid}", response_model=TransactionResponse)
async def get_transaction(
    transaction_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obtener transacción por UUID"""
    transaction_repo = TransactionRepository(db)
    transaction = transaction_repo.get_by_uuid(transaction_uuid)

    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction with UUID {transaction_uuid} not found"
        )

    return TransactionResponse(**enrich_transaction_response(transaction, db))


@router.put("/{transaction_uuid}", response_model=TransactionResponse)
async def update_transaction(
    transaction_uuid: UUID,
    transaction_data: TransactionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores o admins
):
    """Actualizar transacción (requiere permisos de moderador)"""
    transaction_repo = TransactionRepository(db)

    transaction = transaction_repo.get_by_uuid(transaction_uuid)
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction with UUID {transaction_uuid} not found"
        )

    # Si se actualiza currency_pair_uuid, resolver el par
    new_currency_pair_id = None
    if transaction_data.currency_pair_uuid:
        from app.repositories.currency_pair_repository import CurrencyPairRepository
        pair_repo = CurrencyPairRepository(db)
        currency_pair = pair_repo.get_by_uuid(transaction_data.currency_pair_uuid)
        if not currency_pair:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Currency pair with UUID {transaction_data.currency_pair_uuid} not found"
            )
        new_currency_pair_id = currency_pair.id

    updated_transaction = transaction_repo.update_transaction(
        transaction.id,
        transaction_data,
        currency_pair_id=new_currency_pair_id
    )
    if not updated_transaction:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update transaction"
        )

    return TransactionResponse(**enrich_transaction_response(updated_transaction, db))


@router.delete("/{transaction_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(
    transaction_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores o admins
):
    """Eliminar transacción (requiere permisos de moderador)"""
    transaction_repo = TransactionRepository(db)

    transaction = transaction_repo.get_by_uuid(transaction_uuid)
    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction with UUID {transaction_uuid} not found"
        )

    success = transaction_repo.delete_transaction(transaction.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete transaction"
        )


# ===== Report Endpoints =====

@router.get("/reports/user/{user_uuid}", response_model=UserProfitReport)
async def get_user_profit_report(
    user_uuid: UUID,
    start_date: Optional[datetime] = Query(None, description="Fecha inicio para el reporte"),
    end_date: Optional[datetime] = Query(None, description="Fecha fin para el reporte"),
    page: int = Query(1, ge=1, description="Número de página"),
    per_page: int = Query(50, ge=1, le=200, description="Transacciones por página"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener reporte de ganancias para un usuario específico

    **Retorna:**
    - Total de ganancias del usuario
    - Cantidad de transacciones
    - Lista detallada de transacciones donde participó
    """
    # Verificar que el usuario existe
    from app.repositories.user_repository import UserRepository
    user_repo = UserRepository(db)
    user = user_repo.get_by_uuid(user_uuid)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with UUID {user_uuid} not found"
        )

    # Solo admins pueden ver reportes de otros usuarios
    if current_user.uuid != user_uuid and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own profit report"
        )

    transaction_repo = TransactionRepository(db)
    skip = (page - 1) * per_page
    report_data = transaction_repo.get_user_profit_report(
        user_id=user.id,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=per_page
    )

    total_count = report_data["transaction_count"]
    import math
    total_pages = math.ceil(total_count / per_page) if total_count > 0 else 0

    return UserProfitReport(
        user_uuid=user_uuid,
        username=user.username,
        email=user.email,
        total_profit=report_data["total_profit"],
        transaction_count=total_count,
        transactions=[TransactionResponse(**enrich_transaction_response(t, db))
                     for t in report_data["transactions"]],
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )


@router.get("/reports/summary", response_model=ProfitSummary)
async def get_profit_summary(
    start_date: Optional[datetime] = Query(None, description="Fecha inicio para el resumen"),
    end_date: Optional[datetime] = Query(None, description="Fecha fin para el resumen"),
    last_days: Optional[int] = Query(None, ge=1, description="Últimos N días (alternativa a start_date)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)  # Solo moderadores
):
    """
    Obtener resumen general de ganancias del sistema

    **Retorna:**
    - Ganancia total del sistema
    - Total de transacciones
    - Distribución por par de monedas
    - Distribución por usuario
    - Rango de fechas del reporte

    **Requiere permisos de moderador o superior**
    """
    # Si se especifica last_days, calcular start_date
    if last_days and not start_date:
        start_date = datetime.utcnow() - timedelta(days=last_days)

    transaction_repo = TransactionRepository(db)
    summary_data = transaction_repo.get_profit_summary(
        start_date=start_date,
        end_date=end_date
    )

    return ProfitSummary(**summary_data)


@router.get("/reports/my-profits", response_model=UserProfitReport)
async def get_my_profit_report(
    start_date: Optional[datetime] = Query(None, description="Fecha inicio para el reporte"),
    end_date: Optional[datetime] = Query(None, description="Fecha fin para el reporte"),
    last_days: Optional[int] = Query(None, ge=1, description="Últimos N días"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener reporte de mis propias ganancias

    Endpoint conveniente para que cualquier usuario consulte sus ganancias
    """
    # Si se especifica last_days, calcular start_date
    if last_days and not start_date:
        start_date = datetime.utcnow() - timedelta(days=last_days)

    transaction_repo = TransactionRepository(db)
    report_data = transaction_repo.get_user_profit_report(
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date
    )

    return UserProfitReport(
        user_uuid=current_user.uuid,
        username=current_user.username,
        email=current_user.email,
        total_profit=report_data["total_profit"],
        transaction_count=report_data["transaction_count"],
        transactions=[TransactionResponse(**enrich_transaction_response(t, db))
                     for t in report_data["transactions"]]
    )
