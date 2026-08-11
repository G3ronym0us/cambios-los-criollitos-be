from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, or_ as sa_or, case as sa_case
from typing import Optional, List
from datetime import datetime, timezone
from uuid import UUID

from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
from app.models.profit_allocation import OperationProfitAllocation, ProfitAllocationDestination
from app.models.user import User
from app.models.transaction import Transaction, TransactionProfitSplit, TransactionStatus
from app.models.whatsapp_operation import WhatsAppOperation


def reversal_signed(column):
    """
    El aporte de un movimiento a una suma, con el signo de la reversa ya aplicado.

    Una reversa conserva el tipo del original —anular un depósito es otro DEPOSIT— así que
    dentro de su propio grupo cuenta en negativo. Se lee de `reverses_movement_id`, que está
    en la misma fila: ninguna suma necesita mirar el movimiento anulado.
    """
    return sa_case(
        (FundMovement.reverses_movement_id.isnot(None), -func.coalesce(column, 0)),
        else_=func.coalesce(column, 0),
    )


class FundRepository:
    def __init__(self, db: Session):
        self.db = db

    # ===== Grupos =====

    def create_fund_group(
        self,
        name: str,
        currency: str,
        description: Optional[str] = None,
        whatsapp_group_jid: Optional[str] = None,
        default_profit_percentage: Optional[float] = None,
    ) -> FundGroup:
        group = FundGroup(
            name=name,
            currency=currency.upper(),
            description=description,
            whatsapp_group_jid=whatsapp_group_jid,
            default_profit_percentage=default_profit_percentage,
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def update_group_whatsapp_jid(self, group: FundGroup, jid: Optional[str]) -> FundGroup:
        group.whatsapp_group_jid = jid
        self.db.commit()
        self.db.refresh(group)
        return group

    def update_group_profit_percentage(
        self, group: FundGroup, percentage: Optional[float]
    ) -> FundGroup:
        """Cuánto del margen cobrado se queda el fondo por defecto. None = todo lo cobrado."""
        group.default_profit_percentage = percentage
        self.db.commit()
        self.db.refresh(group)
        return group

    def get_group_by_uuid(self, group_uuid: UUID) -> Optional[FundGroup]:
        return self.db.query(FundGroup)\
            .options(joinedload(FundGroup.members).joinedload(FundGroupMember.user))\
            .filter(FundGroup.uuid == str(group_uuid))\
            .first()

    def get_group_by_name(self, name: str) -> Optional[FundGroup]:
        return self.db.query(FundGroup).filter(FundGroup.name == name).first()

    def get_groups(self, active_only: bool = True) -> List[FundGroup]:
        query = self.db.query(FundGroup)\
            .options(joinedload(FundGroup.members).joinedload(FundGroupMember.user))
        if active_only:
            query = query.filter(FundGroup.is_active == True)
        return query.order_by(FundGroup.name).all()

    def add_member(
        self,
        group_id: int,
        user_id: int,
        is_fund_manager: bool = False,
        whatsapp_phone: Optional[str] = None,
        profit_share_percentage: Optional[float] = None,
    ) -> FundGroupMember:
        member = FundGroupMember(
            group_id=group_id,
            user_id=user_id,
            is_fund_manager=is_fund_manager,
            whatsapp_phone=whatsapp_phone,
            profit_share_percentage=profit_share_percentage,
        )
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        return member

    def update_member(
        self,
        member: FundGroupMember,
        is_fund_manager: Optional[bool] = None,
        whatsapp_phone: Optional[str] = None,
        clear_whatsapp_phone: bool = False,
        profit_share_percentage: Optional[float] = None,
    ) -> FundGroupMember:
        if is_fund_manager is not None:
            member.is_fund_manager = is_fund_manager
        if profit_share_percentage is not None:
            member.profit_share_percentage = profit_share_percentage
        if clear_whatsapp_phone:
            member.whatsapp_phone = None
        elif whatsapp_phone is not None:
            member.whatsapp_phone = whatsapp_phone
        self.db.commit()
        self.db.refresh(member)
        return member

    def get_member(self, group_id: int, user_id: int) -> Optional[FundGroupMember]:
        return self.db.query(FundGroupMember).filter(
            FundGroupMember.group_id == group_id,
            FundGroupMember.user_id == user_id
        ).first()

    def remove_member(self, group_id: int, user_id: int) -> bool:
        member = self.get_member(group_id, user_id)
        if not member:
            return False
        self.db.delete(member)
        self.db.commit()
        return True

    # ===== Movimientos =====

    def create_movement(
        self,
        group_id: int,
        user_id: int,
        movement_type: FundMovementType,
        amount: float,
        currency: str,
        movement_date: datetime,
        amount_usdt: Optional[float] = None,
        usdt_rate: Optional[float] = None,
        transaction_id: Optional[int] = None,
        reference: Optional[str] = None,
        notes: Optional[str] = None,
        recorded_by_user_id: Optional[int] = None,
        deposit_method: Optional[str] = None,
        incoming_payment_id: Optional[int] = None,
    ) -> FundMovement:
        movement = FundMovement(
            group_id=group_id,
            user_id=user_id,
            movement_type=movement_type,
            amount=amount,
            currency=currency.upper(),
            amount_usdt=amount_usdt,
            usdt_rate=usdt_rate,
            transaction_id=transaction_id,
            reference=reference,
            notes=notes,
            recorded_by_user_id=recorded_by_user_id,
            movement_date=movement_date,
            deposit_method=deposit_method,
            incoming_payment_id=incoming_payment_id,
        )
        self.db.add(movement)
        self.db.commit()
        self.db.refresh(movement)
        return movement

    def get_movement_by_uuid(self, movement_uuid: UUID) -> Optional[FundMovement]:
        return self.db.query(FundMovement)\
            .options(
                joinedload(FundMovement.group),
                joinedload(FundMovement.user),
                joinedload(FundMovement.transaction),
                joinedload(FundMovement.recorded_by)
            )\
            .filter(FundMovement.uuid == str(movement_uuid))\
            .first()

    def get_movements(
        self,
        group_id: Optional[int] = None,
        user_id: Optional[int] = None,
        movement_type: Optional[FundMovementType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 50,
    ):
        """
        Página de movimientos. El rango de fechas es SEMIABIERTO `[date_from, date_to)`:
        el router traduce el día final elegido a la medianoche siguiente (`core.timezones.day_bounds`),
        así que `date_to` ya es el borde exclusivo. Mismo criterio en `get_movements_totals`
        y `locate_movement`, que tienen que filtrar idéntico o la página calculada no cuadra.
        """
        query = self.db.query(FundMovement)\
            .options(
                joinedload(FundMovement.user),
                joinedload(FundMovement.transaction),
                joinedload(FundMovement.recorded_by)
            )

        if group_id:
            query = query.filter(FundMovement.group_id == group_id)
        if user_id:
            query = query.filter(FundMovement.user_id == user_id)
        if movement_type:
            query = query.filter(FundMovement.movement_type == movement_type)
        if date_from:
            query = query.filter(FundMovement.movement_date >= date_from)
        if date_to:
            query = query.filter(FundMovement.movement_date < date_to)

        total = query.count()
        movements = (
            query.order_by(FundMovement.movement_date.desc(), FundMovement.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return movements, total

    def locate_movement(
        self,
        movement: FundMovement,
        per_page: int = 50,
        movement_type: Optional[FundMovementType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Optional[int]:
        """
        En qué página del historial cae un movimiento, con los filtros que se estén usando.

        Sirve para saltar de un movimiento anulado a su reversa y al revés: la reversa se
        fecha el día de la corrección, así que el par casi nunca está en la misma página.
        Devuelve None si los filtros actuales lo dejan fuera —ahí no hay página a la que ir.
        """
        query = self.db.query(func.count(FundMovement.id)).filter(
            FundMovement.group_id == movement.group_id
        )
        if movement_type:
            query = query.filter(FundMovement.movement_type == movement_type)
        if date_from:
            query = query.filter(FundMovement.movement_date >= date_from)
        if date_to:
            query = query.filter(FundMovement.movement_date < date_to)

        # ¿Sobrevive a los filtros? Si no, no tiene página.
        if not query.filter(FundMovement.id == movement.id).scalar():
            return None

        # Cuántos van antes que él en el mismo orden que la lista (fecha desc, id desc).
        ahead = query.filter(
            sa_or(
                FundMovement.movement_date > movement.movement_date,
                and_(
                    FundMovement.movement_date == movement.movement_date,
                    FundMovement.id > movement.id,
                ),
            )
        ).scalar()
        return int(ahead) // per_page + 1

    def get_running_totals(self, group_id: int, movement_ids: List[int]) -> dict:
        """
        Saldo y ganancia acumulados HASTA cada uno de esos movimientos, como un extracto
        bancario: {movement_id: {"balance_usdt": ..., "profit_usdt": ...}}.

        El acumulado se calcula sobre TODO el historial del grupo en orden cronológico y
        recién después se recortan los movimientos pedidos: si dependiera de la página o del
        filtro, la misma fila mostraría un saldo distinto según cómo se esté mirando la lista.

        El saldo sigue la misma cuenta que la posición del fondo —los depósitos suman, los
        cambios y los personales restan, los ajustes no se cuentan—, así que el saldo del
        movimiento más reciente coincide con la posición del grupo.
        """
        if not movement_ids:
            return {}

        signed_amount = sa_case(
            (FundMovement.movement_type.in_(
                [FundMovementType.DEPOSIT, FundMovementType.EXCHANGE_IN]),
             reversal_signed(FundMovement.amount_usdt)),
            (FundMovement.movement_type.in_(
                [FundMovementType.EXCHANGE, FundMovementType.PERSONAL]),
             -reversal_signed(FundMovement.amount_usdt)),
            else_=0,
        )
        chronological = [FundMovement.movement_date.asc(), FundMovement.id.asc()]

        running = (
            self.db.query(
                FundMovement.id.label("movement_id"),
                func.sum(signed_amount).over(order_by=chronological).label("balance_usdt"),
                func.sum(
                    sa_case(
                        (FundMovement.reversed_by_movement_id.is_(None),
                         func.coalesce(Transaction.profit_amount_usdt, 0)),
                        else_=0,
                    )
                )
                .over(order_by=chronological)
                .label("profit_usdt"),
            )
            .outerjoin(Transaction, Transaction.id == FundMovement.transaction_id)
            .filter(FundMovement.group_id == group_id)
            .subquery()
        )

        rows = (
            self.db.query(running.c.movement_id, running.c.balance_usdt, running.c.profit_usdt)
            .filter(running.c.movement_id.in_(movement_ids))
            .all()
        )
        return {
            row.movement_id: {
                "balance_usdt": float(row.balance_usdt or 0),
                "profit_usdt": float(row.profit_usdt or 0),
            }
            for row in rows
        }

    def get_movements_totals(
        self,
        group_id: Optional[int] = None,
        user_id: Optional[int] = None,
        movement_type: Optional[FundMovementType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict:
        """
        Acumulados de TODO lo que cae bajo el filtro, no de la página que se está viendo:
        cuánto entró, cuánto salió y cuánta ganancia dejó.

        La ganancia sale de la transacción ligada, así que solo los EXCHANGE que vienen de una
        operación suman; un depósito no deja ganancia. Por eso los dos acumulados son
        independientes: uno mide el capital que se movió y el otro lo que se ganó moviéndolo.
        """
        by_type = self.db.query(
            FundMovement.movement_type,
            func.coalesce(func.sum(reversal_signed(FundMovement.amount_usdt)), 0).label("total_usdt"),
            func.count(FundMovement.id).label("count"),
        )
        profit = self.db.query(
            func.coalesce(func.sum(Transaction.profit_amount_usdt), 0).label("total_usdt"),
            func.count(Transaction.id).label("count"),
        ).join(FundMovement, FundMovement.transaction_id == Transaction.id).filter(
            FundMovement.reversed_by_movement_id.is_(None)
        )

        for query_name, query in (("movements", by_type), ("profit", profit)):
            if group_id:
                query = query.filter(FundMovement.group_id == group_id)
            if user_id:
                query = query.filter(FundMovement.user_id == user_id)
            if movement_type:
                query = query.filter(FundMovement.movement_type == movement_type)
            if date_from:
                query = query.filter(FundMovement.movement_date >= date_from)
            if date_to:
                query = query.filter(FundMovement.movement_date < date_to)
            if query_name == "movements":
                by_type = query
            else:
                profit = query

        rows = by_type.group_by(FundMovement.movement_type).all()
        amounts = {row.movement_type: float(row.total_usdt) for row in rows}
        counts = {row.movement_type: int(row.count) for row in rows}
        profit_row = profit.first()

        deposits = amounts.get(FundMovementType.DEPOSIT, 0.0)
        exchanges = amounts.get(FundMovementType.EXCHANGE, 0.0)
        personal = amounts.get(FundMovementType.PERSONAL, 0.0)
        adjustments = amounts.get(FundMovementType.ADJUSTMENT, 0.0)

        return {
            "deposits_usdt": deposits,
            "deposits_count": counts.get(FundMovementType.DEPOSIT, 0),
            "exchanges_usdt": exchanges,
            "exchanges_count": counts.get(FundMovementType.EXCHANGE, 0),
            "personal_usdt": personal,
            "adjustments_usdt": adjustments,
            # Lo que entró menos todo lo que salió: el capital neto del período filtrado.
            "net_usdt": deposits - (exchanges + personal),
            "profit_usdt": float(profit_row.total_usdt) if profit_row else 0.0,
            "profit_count": int(profit_row.count) if profit_row else 0,
        }

    def get_client_names_by_transaction_ids(self, transaction_ids: List[int]) -> dict:
        """
        Mapa {transaction_id: nombre_del_cliente} para las operaciones de WhatsApp ligadas a
        esas transacciones. Permite mostrar el cliente en el historial de movimientos sin un
        query por fila. Cae al teléfono si el cliente no tiene display_name.
        """
        if not transaction_ids:
            return {}

        from app.models.whatsapp_operation import WhatsAppOperation
        from app.models.whatsapp_client import WhatsAppClient

        rows = (
            self.db.query(
                WhatsAppOperation.transaction_id,
                WhatsAppClient.display_name,
                WhatsAppClient.phone,
            )
            .join(WhatsAppClient, WhatsAppOperation.client_id == WhatsAppClient.id)
            .filter(WhatsAppOperation.transaction_id.in_(transaction_ids))
            .all()
        )
        return {
            tx_id: (display_name or phone)
            for tx_id, display_name, phone in rows
            if tx_id is not None
        }

    def reverse_movement(
        self, movement: FundMovement, reason: str, actor_id: Optional[int] = None
    ) -> FundMovement:
        """
        Anula un movimiento con otro que lo referencia, en vez de borrarlo.

        La reversa nace con el mismo tipo, monto y gestor del original —para que se lea como
        «esto anula aquello»— pero cuenta en negativo. Se fecha HOY, no en la fecha del
        original: el error se corrigió hoy y el extracto tiene que poder contarlo así.
        """
        reversal = FundMovement(
            group_id=movement.group_id,
            user_id=movement.user_id,
            movement_type=movement.movement_type,
            amount=movement.amount,
            currency=movement.currency,
            amount_usdt=movement.amount_usdt,
            usdt_rate=movement.usdt_rate,
            reference=movement.reference,
            notes=reason,
            recorded_by_user_id=actor_id,
            movement_date=datetime.now(timezone.utc),
            reverses_movement_id=movement.id,
        )
        self.db.add(reversal)
        self.db.flush()

        movement.reversed_by_movement_id = reversal.id
        movement.reversed_at = reversal.movement_date
        self.db.commit()
        self.db.refresh(reversal)
        return reversal

    # ===== Cálculos de posición =====

    def get_user_position(self, user_id: int, group_id: int) -> Optional[dict]:
        """
        Calcula la posición individual de un gestor dentro de un grupo.

        position = total_deposited - total_outflow
          positivo → el fondo le debe al gestor
          negativo → el gestor le debe al fondo
        """
        group = self.db.query(FundGroup).filter(FundGroup.id == group_id).first()
        user = self.db.query(User).filter(User.id == user_id).first()
        member = self.get_member(group_id, user_id)

        if not group or not user:
            return None

        # Depósitos (una reversa de depósito resta aquí mismo)
        deposit_result = self.db.query(
            func.coalesce(func.sum(reversal_signed(FundMovement.amount)), 0).label("total"),
            func.coalesce(func.sum(reversal_signed(FundMovement.amount_usdt)), 0).label("total_usdt"),
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.user_id == user_id,
            FundMovement.movement_type.in_(
                [FundMovementType.DEPOSIT, FundMovementType.EXCHANGE_IN]
            )
        ).first()

        # Salidas (EXCHANGE + PERSONAL), reversas incluidas con signo negativo
        outflow_result = self.db.query(
            func.coalesce(func.sum(reversal_signed(FundMovement.amount)), 0).label("total"),
            func.coalesce(func.sum(reversal_signed(FundMovement.amount_usdt)), 0).label("total_usdt"),
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.user_id == user_id,
            FundMovement.movement_type.in_([FundMovementType.EXCHANGE, FundMovementType.PERSONAL])
        ).first()

        total_deposited = float(deposit_result.total)
        total_deposited_usdt = float(deposit_result.total_usdt)
        total_outflow = float(outflow_result.total)
        total_outflow_usdt = float(outflow_result.total_usdt)

        return {
            "user_uuid": user.uuid,
            "username": user.username,
            "group_uuid": group.uuid,
            "group_name": group.name,
            "is_fund_manager": member.is_fund_manager if member else False,
            "total_deposited": total_deposited,
            "total_deposited_usdt": total_deposited_usdt,
            "total_outflow": total_outflow,
            "total_outflow_usdt": total_outflow_usdt,
            "position": total_deposited - total_outflow,
            "position_usdt": total_deposited_usdt - total_outflow_usdt,
            "currency": group.currency,
        }

    def get_group_balance(self, group_id: int) -> Optional[dict]:
        """
        Balance consolidado del grupo — replica las tres columnas de la hoja Excel:
          total_position_usdt  → "Total"     (depósitos - salidas de todos los miembros)
          total_profit_usdt    → "Acumulada" (ganancias de TransactionProfitSplits COMPLETED)
          available_funds_usdt → "Fondos"    (Acumulada - Total)
        """
        group = self.db.query(FundGroup)\
            .options(joinedload(FundGroup.members).joinedload(FundGroupMember.user))\
            .filter(FundGroup.id == group_id)\
            .first()

        if not group:
            return None

        member_user_ids = [m.user_id for m in group.members]

        # Posición consolidada de todos los miembros (las reversas restan de su propio tipo)
        deposit_result = self.db.query(
            func.coalesce(func.sum(reversal_signed(FundMovement.amount_usdt)), 0).label("total_usdt")
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.movement_type.in_(
                [FundMovementType.DEPOSIT, FundMovementType.EXCHANGE_IN]
            )
        ).first()

        outflow_result = self.db.query(
            func.coalesce(func.sum(reversal_signed(FundMovement.amount_usdt)), 0).label("total_usdt")
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.movement_type.in_([FundMovementType.EXCHANGE, FundMovementType.PERSONAL])
        ).first()

        total_deposited_usdt = float(deposit_result.total_usdt)
        total_outflow_usdt = float(outflow_result.total_usdt)
        total_position_usdt = total_deposited_usdt - total_outflow_usdt

        # Ganancia acumulada, de dos fuentes que no se pisan:
        #
        # 1. Lo que las operaciones le asignaron a ESTE fondo (`operation_profit_allocations`).
        allocated_result = self.db.query(
            func.coalesce(func.sum(OperationProfitAllocation.amount_usdt), 0).label("total")
        ).join(
            WhatsAppOperation,
            WhatsAppOperation.id == OperationProfitAllocation.whatsapp_operation_id,
        ).join(
            Transaction, Transaction.id == WhatsAppOperation.transaction_id
        ).filter(
            OperationProfitAllocation.fund_group_id == group_id,
            OperationProfitAllocation.destination_type == ProfitAllocationDestination.FUND,
            Transaction.status == TransactionStatus.COMPLETED,
        ).first()

        # 2. Lo anterior al reparto: splits de miembros MENOS splits de agentes externos en
        #    las mismas transacciones COMPLETED, la columna "Acumulada" del Excel original.
        #    Solo cuenta las transacciones SIN reparto, o se sumaría dos veces lo mismo.
        allocated_tx_subq = (
            self.db.query(WhatsAppOperation.transaction_id)
            .join(
                OperationProfitAllocation,
                OperationProfitAllocation.whatsapp_operation_id == WhatsAppOperation.id,
            )
            .filter(WhatsAppOperation.transaction_id.isnot(None))
        )
        member_tx_subq = (
            self.db.query(TransactionProfitSplit.transaction_id)
            .filter(TransactionProfitSplit.user_id.in_(member_user_ids))
        )
        legacy_result = self.db.query(
            func.coalesce(
                func.sum(
                    sa_case(
                        (TransactionProfitSplit.user_id.in_(member_user_ids),
                         TransactionProfitSplit.profit_amount_usdt),
                        else_=-TransactionProfitSplit.profit_amount_usdt,
                    )
                ),
                0,
            ).label("total")
        ).join(Transaction, TransactionProfitSplit.transaction_id == Transaction.id)\
         .filter(
             Transaction.status == TransactionStatus.COMPLETED,
             TransactionProfitSplit.profit_amount_usdt.isnot(None),
             TransactionProfitSplit.transaction_id.in_(member_tx_subq),
             TransactionProfitSplit.transaction_id.notin_(allocated_tx_subq),
         ).first()

        total_profit_usdt = float(allocated_result.total) + float(legacy_result.total)
        available_funds_usdt = total_profit_usdt + total_position_usdt

        # Posición individual de cada miembro
        by_member = []
        for member in group.members:
            pos = self.get_user_position(member.user_id, group_id)
            if pos:
                by_member.append(pos)

        return {
            "group_uuid": group.uuid,
            "group_name": group.name,
            "currency": group.currency,
            "total_deposited_usdt": total_deposited_usdt,
            "total_outflow_usdt": total_outflow_usdt,
            "total_position_usdt": total_position_usdt,
            "total_profit_usdt": total_profit_usdt,
            "available_funds_usdt": available_funds_usdt,
            "by_member": by_member,
        }

    def get_active_group_by_currency(self, currency: Optional[str]) -> Optional[FundGroup]:
        """
        El fondo activo que lleva esa moneda, o None.

        Devuelve None también cuando hay MÁS de uno: entre dos fondos de la misma moneda no
        hay forma de elegir sin inventar, así que la pata queda sin fondo y lo resuelve el
        operador a mano. Hoy los tres fondos tienen monedas distintas, pero el día que haya
        dos en USD esto tiene que callarse, no adivinar.
        """
        if not currency:
            return None
        rows = (
            self.db.query(FundGroup)
            .filter(FundGroup.is_active.is_(True), func.upper(FundGroup.currency) == currency.upper())
            .limit(2)
            .all()
        )
        return rows[0] if len(rows) == 1 else None
