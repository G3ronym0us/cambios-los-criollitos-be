from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, or_, desc
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
from uuid import UUID
from app.models.transaction import Transaction, TransactionProfitSplit, TransactionStatus
from app.models.user import User
from app.schemas.transaction import TransactionCreate, TransactionUpdate, ProfitSplitCreate

class TransactionRepository:
    """Repository para gestionar transacciones y distribución de ganancias"""

    def __init__(self, db: Session):
        self.db = db

    def create_transaction(
        self,
        transaction_data: TransactionCreate,
        created_by_user_id: Optional[int] = None,
        from_currency: str = None,
        to_currency: str = None
    ) -> Transaction:
        """
        Crear nueva transacción con distribución de ganancias

        Args:
            transaction_data: Datos de la transacción
            created_by_user_id: ID del usuario que crea la transacción
            from_currency: Símbolo de la moneda origen (extraído del currency_pair)
            to_currency: Símbolo de la moneda destino (extraído del currency_pair)

        Returns:
            Transaction creada con profit_splits
        """
        # Obtener user_id del UUID si se proporciona
        user_id = created_by_user_id
        if transaction_data.user_uuid:
            user = self.db.query(User).filter(User.uuid == transaction_data.user_uuid).first()
            if not user:
                raise ValueError(f"User with UUID {transaction_data.user_uuid} not found")
            user_id = user.id

        # Validar que se proporcionaron las monedas
        if not from_currency or not to_currency:
            raise ValueError("from_currency and to_currency must be provided")

        # Calcular profit_amount basado en el porcentaje y el monto
        profit_amount = (transaction_data.to_amount * transaction_data.total_profit_percentage) / 100

        # Crear transacción
        db_transaction = Transaction(
            user_id=user_id,
            from_currency=from_currency.upper(),
            to_currency=to_currency.upper(),
            from_amount=transaction_data.from_amount,
            to_amount=transaction_data.to_amount,
            exchange_rate=transaction_data.exchange_rate,
            description=transaction_data.description,
            transaction_type=transaction_data.transaction_type,
            total_profit_percentage=transaction_data.total_profit_percentage,
            profit_amount=profit_amount,
            status=TransactionStatus.PENDING
        )

        self.db.add(db_transaction)
        self.db.flush()  # Para obtener el ID sin commit

        # Crear profit splits si existen
        usdt_rate = transaction_data.usdt_rate
        total_profit_usdt = None
        all_splits_have_usdt = True

        if transaction_data.profit_splits:
            for split in transaction_data.profit_splits:
                # Obtener user con preferred_settlement_currency
                user = self.db.query(User).filter(User.uuid == split.user_uuid).first()
                if not user:
                    raise ValueError(f"User with UUID {split.user_uuid} not found")

                split_amount = (profit_amount * split.profit_percentage) / transaction_data.total_profit_percentage

                # Determinar settlement_currency: explícito en split > preferred del usuario > NULL
                effective_usdt_rate = getattr(split, 'usdt_rate', None) or usdt_rate
                settlement_currency = split.settlement_currency or user.preferred_settlement_currency

                # Calcular profit_amount_usdt del split
                split_profit_usdt = None
                if effective_usdt_rate:
                    split_profit_usdt = split_amount / effective_usdt_rate
                else:
                    all_splits_have_usdt = False

                # Calcular settlement_amount (solo para USD/USDT por ahora)
                split_settlement_amount = None
                if settlement_currency and settlement_currency.upper() in ("USD", "USDT"):
                    split_settlement_amount = split_profit_usdt

                db_split = TransactionProfitSplit(
                    transaction_id=db_transaction.id,
                    user_id=user.id,
                    profit_percentage=split.profit_percentage,
                    profit_amount=split_amount,
                    profit_amount_usdt=split_profit_usdt,
                    settlement_currency=settlement_currency,
                    settlement_amount=split_settlement_amount
                )
                self.db.add(db_split)

                if split_profit_usdt is not None:
                    total_profit_usdt = (total_profit_usdt or 0) + split_profit_usdt

        # Asignar profit_amount_usdt a la transacción solo si todos los splits lo tienen
        if transaction_data.profit_splits and all_splits_have_usdt:
            db_transaction.profit_amount_usdt = total_profit_usdt
        elif usdt_rate and not transaction_data.profit_splits:
            # Sin splits: calcular directo desde el profit_amount total
            db_transaction.profit_amount_usdt = profit_amount / usdt_rate

        self.db.commit()
        self.db.refresh(db_transaction)
        return db_transaction

    def get_by_id(self, transaction_id: int) -> Optional[Transaction]:
        """Obtener transacción por ID con profit_splits cargados"""
        return self.db.query(Transaction)\
            .options(joinedload(Transaction.profit_splits))\
            .filter(Transaction.id == transaction_id)\
            .first()

    def get_by_uuid(self, transaction_uuid: UUID) -> Optional[Transaction]:
        """Obtener transacción por UUID con profit_splits cargados"""
        return self.db.query(Transaction)\
            .options(joinedload(Transaction.profit_splits))\
            .filter(Transaction.uuid == transaction_uuid)\
            .first()

    def find_similar_transactions(
        self,
        from_currency: str,
        to_currency: str,
        from_amount: float,
        tolerance_percentage: float = 1.0
    ) -> List[Transaction]:
        """
        Buscar transacciones similares basadas en par de monedas y monto del mismo día

        Args:
            from_currency: Moneda origen
            to_currency: Moneda destino
            from_amount: Monto origen a comparar
            tolerance_percentage: Porcentaje de tolerancia para considerar montos similares (default 1%)

        Returns:
            Lista de transacciones similares del mismo día
        """
        # Calcular rango de montos
        lower_bound = from_amount * (1 - tolerance_percentage / 100)
        upper_bound = from_amount * (1 + tolerance_percentage / 100)

        # Calcular inicio del día actual (00:00:00)
        now = datetime.utcnow()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Buscar transacciones similares del mismo día
        query = self.db.query(Transaction)\
            .options(joinedload(Transaction.profit_splits))\
            .filter(
                and_(
                    Transaction.from_currency == from_currency.upper(),
                    Transaction.to_currency == to_currency.upper(),
                    Transaction.from_amount >= lower_bound,
                    Transaction.from_amount <= upper_bound,
                    Transaction.created_at >= start_of_day,
                    Transaction.status.notin_([TransactionStatus.FAILED, TransactionStatus.CANCELLED])
                )
            )\
            .order_by(desc(Transaction.created_at))

        return query.all()

    def get_all_transactions(self,
                             skip: int = 0,
                             limit: int = 100,
                             status: Optional[TransactionStatus] = None,
                             currency_pair: Optional[Tuple[str, str]] = None,
                             user_id: Optional[int] = None,
                             start_date: Optional[datetime] = None,
                             end_date: Optional[datetime] = None) -> Tuple[List[Transaction], int]:
        """
        Obtener transacciones con filtros y paginación

        Returns:
            Tuple de (transacciones, total_count)
        """
        query = self.db.query(Transaction).options(joinedload(Transaction.profit_splits))

        # Aplicar filtros
        if status:
            query = query.filter(Transaction.status == status)

        if currency_pair:
            from_curr, to_curr = currency_pair
            query = query.filter(
                and_(
                    Transaction.from_currency == from_curr.upper(),
                    Transaction.to_currency == to_curr.upper()
                )
            )

        if user_id:
            query = query.filter(Transaction.user_id == user_id)

        if start_date:
            query = query.filter(Transaction.created_at >= start_date)

        if end_date:
            query = query.filter(Transaction.created_at <= end_date)

        # Contar total
        total = query.count()

        # Aplicar paginación y ordenar
        transactions = query.order_by(desc(Transaction.created_at))\
            .offset(skip)\
            .limit(limit)\
            .all()

        return transactions, total

    def update_transaction(
        self,
        transaction_id: int,
        transaction_data: TransactionUpdate,
        from_currency: str = None,
        to_currency: str = None
    ) -> Optional[Transaction]:
        """Actualizar transacción"""
        transaction = self.get_by_id(transaction_id)
        if not transaction:
            return None

        update_data = transaction_data.dict(exclude_unset=True)

        # Si se proporcionaron from_currency y to_currency desde currency_pair_uuid
        if from_currency and to_currency:
            update_data['from_currency'] = from_currency.upper()
            update_data['to_currency'] = to_currency.upper()

        # Remover campos que no pertenecen al modelo Transaction
        update_data.pop('currency_pair_uuid', None)
        usdt_rate = update_data.pop('usdt_rate', None)

        # Si cambia el porcentaje o el monto destino, recalcular profit_amount y todos los splits
        recalculate_splits = 'total_profit_percentage' in update_data or 'to_amount' in update_data
        if recalculate_splits:
            new_to_amount = update_data.get('to_amount', transaction.to_amount)
            new_total_pct = update_data.get('total_profit_percentage', transaction.total_profit_percentage)
            new_profit_amount = (new_to_amount * new_total_pct) / 100
            update_data['profit_amount'] = new_profit_amount

            # Recalcular profit_amount de cada split existente
            if transaction.profit_splits and new_total_pct:
                new_total_profit_usdt = 0.0
                all_have_usdt = True
                for split in transaction.profit_splits:
                    split.profit_amount = new_profit_amount * (split.profit_percentage / new_total_pct)
                    if usdt_rate:
                        split.profit_amount_usdt = split.profit_amount / usdt_rate
                        if split.settlement_currency and split.settlement_currency.upper() in ("USD", "USDT"):
                            split.settlement_amount = split.profit_amount_usdt
                        new_total_profit_usdt += split.profit_amount_usdt
                    elif split.profit_amount_usdt is None:
                        all_have_usdt = False

                if usdt_rate:
                    update_data['profit_amount_usdt'] = new_total_profit_usdt if all_have_usdt else None

        # Aplicar actualizaciones al modelo
        for field, value in update_data.items():
            if field in ['from_currency', 'to_currency'] and value:
                value = value.upper()
            if field == 'status' and isinstance(value, str):
                value = TransactionStatus(value)
            setattr(transaction, field, value)

        transaction.updated_at = datetime.utcnow()

        # Si se completa la transacción, marcar timestamp
        if transaction.status == TransactionStatus.COMPLETED and not transaction.completed_at:
            transaction.completed_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transaction)
        return transaction

    def delete_transaction(self, transaction_id: int) -> bool:
        """Eliminar transacción (también elimina profit_splits por CASCADE)"""
        transaction = self.get_by_id(transaction_id)
        if not transaction:
            return False

        self.db.delete(transaction)
        self.db.commit()
        return True

    def get_user_profit_report(self,
                                user_id: int,
                                start_date: Optional[datetime] = None,
                                end_date: Optional[datetime] = None) -> Dict:
        """
        Generar reporte de ganancias para un usuario específico

        Returns:
            Dict con total_profit, transaction_count y lista de transacciones
        """
        # Query base para profit splits del usuario
        query = self.db.query(TransactionProfitSplit)\
            .join(Transaction)\
            .filter(TransactionProfitSplit.user_id == user_id)\
            .filter(Transaction.status == TransactionStatus.COMPLETED)

        if start_date:
            query = query.filter(Transaction.created_at >= start_date)
        if end_date:
            query = query.filter(Transaction.created_at <= end_date)

        splits = query.all()

        # Calcular totales
        total_profit = sum(split.profit_amount for split in splits)
        transaction_count = len(set(split.transaction_id for split in splits))

        # Obtener transacciones completas
        transaction_ids = [split.transaction_id for split in splits]
        transactions = self.db.query(Transaction)\
            .options(joinedload(Transaction.profit_splits))\
            .filter(Transaction.id.in_(transaction_ids))\
            .order_by(desc(Transaction.created_at))\
            .all() if transaction_ids else []

        return {
            "user_id": user_id,
            "total_profit": total_profit,
            "transaction_count": transaction_count,
            "transactions": transactions
        }

    def get_profit_summary(self,
                           start_date: Optional[datetime] = None,
                           end_date: Optional[datetime] = None) -> Dict:
        """
        Generar resumen general de ganancias

        Returns:
            Dict con estadísticas generales y por usuario
        """
        # Query base
        query = self.db.query(Transaction)\
            .filter(Transaction.status == TransactionStatus.COMPLETED)

        if start_date:
            query = query.filter(Transaction.created_at >= start_date)
        if end_date:
            query = query.filter(Transaction.created_at <= end_date)

        transactions = query.all()

        # Totales generales
        total_profit = sum(t.profit_amount for t in transactions)
        total_transactions = len(transactions)

        # Por par de monedas
        by_currency_pair = {}
        for t in transactions:
            pair = f"{t.from_currency}/{t.to_currency}"
            if pair not in by_currency_pair:
                by_currency_pair[pair] = {
                    "total_profit": 0,
                    "transaction_count": 0,
                    "total_volume": 0
                }
            by_currency_pair[pair]["total_profit"] += t.profit_amount
            by_currency_pair[pair]["transaction_count"] += 1
            by_currency_pair[pair]["total_volume"] += t.to_amount

        # Por usuario (de profit_splits)
        user_profits = self.db.query(
            TransactionProfitSplit.user_id,
            func.sum(TransactionProfitSplit.profit_amount).label('total_profit'),
            func.count(TransactionProfitSplit.transaction_id.distinct()).label('transaction_count')
        ).join(Transaction)\
         .filter(Transaction.status == TransactionStatus.COMPLETED)

        if start_date:
            user_profits = user_profits.filter(Transaction.created_at >= start_date)
        if end_date:
            user_profits = user_profits.filter(Transaction.created_at <= end_date)

        user_profits = user_profits.group_by(TransactionProfitSplit.user_id).all()

        by_user = []
        for user_id, total_profit, tx_count in user_profits:
            user = self.db.query(User).filter(User.id == user_id).first()
            by_user.append({
                "user_id": user_id,
                "username": user.username if user else None,
                "email": user.email if user else None,
                "total_profit": float(total_profit),
                "transaction_count": int(tx_count)
            })

        return {
            "total_profit": total_profit,
            "total_transactions": total_transactions,
            "by_currency_pair": by_currency_pair,
            "by_user": by_user,
            "date_range": {
                "start": start_date.isoformat() if start_date else None,
                "end": end_date.isoformat() if end_date else None
            }
        }

    def get_recent_transactions(self, limit: int = 10) -> List[Transaction]:
        """Obtener transacciones más recientes"""
        return self.db.query(Transaction)\
            .options(joinedload(Transaction.profit_splits))\
            .order_by(desc(Transaction.created_at))\
            .limit(limit)\
            .all()

    def get_transaction_stats(self) -> Dict:
        """Obtener estadísticas generales de transacciones"""
        total = self.db.query(func.count(Transaction.id)).scalar()
        completed = self.db.query(func.count(Transaction.id))\
            .filter(Transaction.status == TransactionStatus.COMPLETED).scalar()
        pending = self.db.query(func.count(Transaction.id))\
            .filter(Transaction.status == TransactionStatus.PENDING).scalar()

        total_profit = self.db.query(func.sum(Transaction.profit_amount))\
            .filter(Transaction.status == TransactionStatus.COMPLETED).scalar() or 0

        return {
            "total_transactions": total,
            "completed": completed,
            "pending": pending,
            "total_profit": float(total_profit)
        }
