from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, case as sa_case
from typing import Optional, List
from datetime import datetime
from uuid import UUID

from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
from app.models.user import User
from app.models.transaction import Transaction, TransactionProfitSplit, TransactionStatus


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
    ) -> FundGroup:
        group = FundGroup(
            name=name,
            currency=currency.upper(),
            description=description,
            whatsapp_group_jid=whatsapp_group_jid,
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
    ) -> FundGroupMember:
        member = FundGroupMember(
            group_id=group_id,
            user_id=user_id,
            is_fund_manager=is_fund_manager,
            whatsapp_phone=whatsapp_phone,
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
    ) -> FundGroupMember:
        if is_fund_manager is not None:
            member.is_fund_manager = is_fund_manager
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
            query = query.filter(FundMovement.movement_date <= date_to)

        total = query.count()
        movements = query.order_by(FundMovement.movement_date.desc()).offset(skip).limit(limit).all()
        return movements, total

    def delete_movement(self, movement_id: int) -> bool:
        movement = self.db.query(FundMovement).filter(FundMovement.id == movement_id).first()
        if not movement:
            return False
        self.db.delete(movement)
        self.db.commit()
        return True

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

        # Depósitos
        deposit_result = self.db.query(
            func.coalesce(func.sum(FundMovement.amount), 0).label("total"),
            func.coalesce(func.sum(FundMovement.amount_usdt), 0).label("total_usdt"),
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.user_id == user_id,
            FundMovement.movement_type == FundMovementType.DEPOSIT
        ).first()

        # Salidas (EXCHANGE + PERSONAL)
        outflow_result = self.db.query(
            func.coalesce(func.sum(FundMovement.amount), 0).label("total"),
            func.coalesce(func.sum(FundMovement.amount_usdt), 0).label("total_usdt"),
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

        # Posición consolidada de todos los miembros
        deposit_result = self.db.query(
            func.coalesce(func.sum(FundMovement.amount_usdt), 0).label("total_usdt")
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.movement_type == FundMovementType.DEPOSIT
        ).first()

        outflow_result = self.db.query(
            func.coalesce(func.sum(FundMovement.amount_usdt), 0).label("total_usdt")
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.movement_type.in_([FundMovementType.EXCHANGE, FundMovementType.PERSONAL])
        ).first()

        total_deposited_usdt = float(deposit_result.total_usdt)
        total_outflow_usdt = float(outflow_result.total_usdt)
        total_position_usdt = total_deposited_usdt - total_outflow_usdt

        # Ganancia acumulada: splits de miembros MENOS splits de agentes externos
        # en las mismas transacciones COMPLETED.
        # Replica la columna "Acumulada" del Excel:
        #   ganancia_neta = sum(member_splits) - sum(non_member_agent_splits)
        # Subquery: IDs de transacciones donde al menos un miembro del grupo tiene split
        member_tx_subq = (
            self.db.query(TransactionProfitSplit.transaction_id)
            .filter(TransactionProfitSplit.user_id.in_(member_user_ids))
        )
        profit_result = self.db.query(
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
         ).first()

        total_profit_usdt = float(profit_result.total)
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
