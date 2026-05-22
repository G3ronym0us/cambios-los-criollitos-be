from sqlalchemy.orm import Session, joinedload
from sqlalchemy import case, desc, asc
from typing import Optional, List
from datetime import datetime
from uuid import UUID
from app.models.currency_pair import CurrencyPair
from app.models.currency import Currency
from app.schemas.currency_pair import CurrencyPairCreate, CurrencyPairUpdate

class CurrencyPairRepository:
    def __init__(self, db: Session):
        self.db = db

    def has_manual_rates(self, pair_id: int) -> bool:
        """Check if a currency pair has active manual rates"""
        from app.models.exchange_rate import ExchangeRate

        manual_rate = self.db.query(ExchangeRate)\
            .filter(
                ExchangeRate.currency_pair_id == pair_id,
                ExchangeRate.is_active == True,
                ExchangeRate.is_manual == True
            ).first()

        return manual_rate is not None

    async def create_currency_pair(self, pair_data: CurrencyPairCreate) -> CurrencyPair:
        """Create new currency pair"""
        # Get currencies from UUIDs
        from_currency = self.db.query(Currency).filter(Currency.uuid == pair_data.from_currency_uuid).first()
        to_currency = self.db.query(Currency).filter(Currency.uuid == pair_data.to_currency_uuid).first()

        if not from_currency or not to_currency:
            raise ValueError("Invalid currency UUIDs provided")

        pair_symbol = CurrencyPair.create_pair_symbol(from_currency.symbol, to_currency.symbol)

        # Validate base_pair if provided
        base_pair = None
        if pair_data.base_pair_uuid:
            base_pair = self.get_by_uuid(pair_data.base_pair_uuid)
            if not base_pair:
                raise ValueError("Base pair not found")
            if not (base_pair.binance_tracked or self.has_manual_rates(base_pair.id)):
                raise ValueError("Base pair must be either Binance tracked or have manual rates")

        # Resolve usdt_pair_uuid to usdt_pair_id if provided
        usdt_pair_id = None
        if pair_data.usdt_pair_uuid:
            usdt_pair = self.get_by_uuid(pair_data.usdt_pair_uuid)
            if not usdt_pair:
                raise ValueError("USDT conversion pair not found")
            usdt_pair_id = usdt_pair.id

        db_pair = CurrencyPair(
            from_currency_id=from_currency.id,
            to_currency_id=to_currency.id,
            pair_type=pair_data.pair_type,
            base_pair=base_pair,
            derived_percentage=pair_data.derived_percentage,
            use_inverse_percentage=pair_data.use_inverse_percentage,
            pair_symbol=pair_symbol,
            description=pair_data.description,
            is_active=pair_data.is_active,
            is_monitored=pair_data.is_monitored,
            binance_tracked=pair_data.binance_tracked,
            banks_to_track=pair_data.banks_to_track,
            amount_to_track=pair_data.amount_to_track,
            usdt_reference_side=pair_data.usdt_reference_side,
            usdt_manual_rate=pair_data.usdt_manual_rate,
            usdt_pair_id=usdt_pair_id,
            usdt_pair_inverse=pair_data.usdt_pair_inverse,
        )
        
        # Validate base pair configuration
        valid_base, base_error = db_pair.validate_base_pair()
        if not valid_base:
            raise ValueError(base_error)

        # Validate binance tracking requirements
        if db_pair.binance_tracked:
            # First do basic validation
            valid, error_msg = db_pair.validate_binance_tracking()
            if not valid:
                raise ValueError(error_msg)
            
            # Then validate with Binance API
            try:
                api_valid, api_msg, validation_data = await db_pair.validate_binance_tracking_with_api()
                if not api_valid:
                    raise ValueError(f"Binance validation failed: {api_msg}")
            except Exception as e:
                raise ValueError(f"Could not validate configuration with Binance: {str(e)}")
        
        self.db.add(db_pair)
        self.db.commit()
        self.db.refresh(db_pair)
        return db_pair

    def get_by_id(self, pair_id: int) -> Optional[CurrencyPair]:
        """Get currency pair by ID with related currencies"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency),
                    joinedload(CurrencyPair.to_currency),
                    joinedload(CurrencyPair.base_pair))\
            .filter(CurrencyPair.id == pair_id).first()

    def get_by_uuid(self, pair_uuid: UUID) -> Optional[CurrencyPair]:
        """Get currency pair by UUID with related currencies"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency),
                    joinedload(CurrencyPair.to_currency),
                    joinedload(CurrencyPair.base_pair))\
            .filter(CurrencyPair.uuid == pair_uuid).first()

    def get_by_symbol(self, pair_symbol: str) -> Optional[CurrencyPair]:
        """Get currency pair by symbol"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency),
                    joinedload(CurrencyPair.base_pair))\
            .filter(CurrencyPair.pair_symbol == pair_symbol.upper()).first()

    def get_by_currencies(self, from_currency_id: int, to_currency_id: int) -> Optional[CurrencyPair]:
        """Get currency pair by currency IDs"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                CurrencyPair.from_currency_id == from_currency_id,
                CurrencyPair.to_currency_id == to_currency_id
            ).first()

    def get_all_pairs(
        self,
        skip: int = 0,
        limit: int = 100,
        active_only: bool = False,
        currency_symbol: Optional[str] = None,
    ) -> List[CurrencyPair]:
        """Get all currency pairs with pagination and simple ordering"""
        from app.models.currency import Currency
        from sqlalchemy.orm import aliased

        FromCurrency = aliased(Currency)
        ToCurrency = aliased(Currency)

        query = self.db.query(CurrencyPair)\
            .join(FromCurrency, CurrencyPair.from_currency_id == FromCurrency.id)\
            .join(ToCurrency, CurrencyPair.to_currency_id == ToCurrency.id)\
            .options(
                joinedload(CurrencyPair.from_currency),
                joinedload(CurrencyPair.to_currency),
            )

        if active_only:
            query = query.filter(CurrencyPair.is_active == True)

        if currency_symbol:
            symbol = currency_symbol.upper()
            query = query.filter(
                (FromCurrency.symbol == symbol) | (ToCurrency.symbol == symbol)
            )

        query = query.order_by(
            desc(CurrencyPair.binance_tracked),
            desc(CurrencyPair.is_monitored),
            desc(CurrencyPair.is_active),
            asc(CurrencyPair.pair_symbol)
        )

        return query.offset(skip).limit(limit).all()

    def get_monitored_pairs(self) -> List[CurrencyPair]:
        """Get pairs that are monitored for scraping"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                CurrencyPair.is_active == True,
                CurrencyPair.is_monitored == True
            ).all()

    def get_binance_tracked_pairs(self) -> List[CurrencyPair]:
        """Get pairs that are tracked on Binance (fiat-crypto pairs)"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                CurrencyPair.is_active == True,
                CurrencyPair.binance_tracked == True
            ).all()

    def get_pairs_by_currency(self, currency_id: int) -> List[CurrencyPair]:
        """Get all pairs that include a specific currency"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                (CurrencyPair.from_currency_id == currency_id) |
                (CurrencyPair.to_currency_id == currency_id)
            ).all()

    async def update_currency_pair(self, pair_id: int, pair_data: CurrencyPairUpdate) -> Optional[CurrencyPair]:
        """Update currency pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return None

        update_data = pair_data.dict(exclude_unset=True)

        # Convert base_pair_uuid to base_pair_id if provided
        if 'base_pair_uuid' in update_data and update_data['base_pair_uuid']:
            base_pair = self.get_by_uuid(update_data['base_pair_uuid'])
            if not base_pair:
                raise ValueError("Base pair not found")
            update_data['base_pair_id'] = base_pair.id
            del update_data['base_pair_uuid']
        elif 'base_pair_uuid' in update_data:
            del update_data['base_pair_uuid']

        # Convert usdt_pair_uuid to usdt_pair_id if provided
        if 'usdt_pair_uuid' in update_data and update_data['usdt_pair_uuid']:
            usdt_pair = self.get_by_uuid(update_data['usdt_pair_uuid'])
            if not usdt_pair:
                raise ValueError("USDT conversion pair not found")
            update_data['usdt_pair_id'] = usdt_pair.id
            del update_data['usdt_pair_uuid']
        elif 'usdt_pair_uuid' in update_data:
            update_data['usdt_pair_id'] = None
            del update_data['usdt_pair_uuid']

        for field, value in update_data.items():
            setattr(pair, field, value)
        
        # Validate binance tracking requirements if it's being enabled
        if pair.binance_tracked:
            # First do basic validation
            valid, error_msg = pair.validate_binance_tracking()
            if not valid:
                raise ValueError(error_msg)
            
            # Then validate with Binance API
            try:
                api_valid, api_msg, validation_data = await pair.validate_binance_tracking_with_api()
                if not api_valid:
                    raise ValueError(f"Binance validation failed: {api_msg}")
            except Exception as e:
                raise ValueError(f"Could not validate configuration with Binance: {str(e)}")
        
        pair.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(pair)
        return pair

    def delete_currency_pair(self, pair_id: int) -> bool:
        """Delete currency pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return False
        
        # Validate that no derived pairs depend on this pair
        can_delete, error_msg = self.validate_base_pair_usage(pair_id)
        if not can_delete:
            raise ValueError(error_msg)
        
        self.db.delete(pair)
        self.db.commit()
        return True

    def pair_exists(self, from_currency_id: int, to_currency_id: int, exclude_id: Optional[int] = None) -> bool:
        """Check if currency pair already exists"""
        query = self.db.query(CurrencyPair).filter(
            CurrencyPair.from_currency_id == from_currency_id,
            CurrencyPair.to_currency_id == to_currency_id
        )
        if exclude_id:
            query = query.filter(CurrencyPair.id != exclude_id)
        return query.first() is not None

    def symbol_exists(self, pair_symbol: str, exclude_id: Optional[int] = None) -> bool:
        """Check if pair symbol already exists"""
        query = self.db.query(CurrencyPair).filter(CurrencyPair.pair_symbol == pair_symbol.upper())
        if exclude_id:
            query = query.filter(CurrencyPair.id != exclude_id)
        return query.first() is not None

    def toggle_monitoring(self, pair_id: int, is_monitored: bool) -> bool:
        """Toggle monitoring status for a pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return False
        
        pair.is_monitored = is_monitored
        pair.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def toggle_active_status(self, pair_id: int, is_active: bool) -> bool:
        """Toggle active status for a pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return False
        
        pair.is_active = is_active
        pair.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def toggle_binance_tracking(self, pair_id: int, binance_tracked: bool) -> bool:
        """Toggle Binance tracking status for a pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return False
        
        pair.binance_tracked = binance_tracked
        pair.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def get_base_pairs(self) -> List[CurrencyPair]:
        """
        Get all pairs that can be used as base pairs for derived rates.

        A pair can be a base pair if:
        1. pair_type = BASE
        2. binance_tracked = True OR has an active manual rate (is_manual = True)
        """
        from app.models.exchange_rate import ExchangeRate
        from app.enums.pair_type import PairType
        from sqlalchemy import or_

        # Subquery to check if pair has manual rates
        subquery = self.db.query(ExchangeRate.currency_pair_id)\
            .filter(
                ExchangeRate.is_active == True,
                ExchangeRate.is_manual == True
            ).subquery()

        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency),
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                CurrencyPair.is_active == True,
                CurrencyPair.pair_type == PairType.BASE,
                or_(
                    CurrencyPair.binance_tracked == True,
                    CurrencyPair.id.in_(subquery)
                )
            ).all()

    def get_derived_pairs(self, base_pair_id: int) -> List[CurrencyPair]:
        """Get all pairs that are derived from a specific base pair"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency),
                    joinedload(CurrencyPair.base_pair))\
            .filter(CurrencyPair.base_pair_id == base_pair_id).all()

    def validate_base_pair_usage(self, pair_id: int) -> tuple[bool, str]:
        """Validate if a pair can be deleted (no derived pairs depend on it)"""
        derived_pairs = self.get_derived_pairs(pair_id)
        if derived_pairs:
            pair_names = [p.pair_symbol for p in derived_pairs]
            return False, f"Cannot delete pair because it's used as base by: {', '.join(pair_names)}"
        return True, ""

    def get_pairs_with_base_rates(self) -> List[CurrencyPair]:
        """Get all pairs that have base rates"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency),
                    joinedload(CurrencyPair.to_currency),
                    joinedload(CurrencyPair.base_pair))\
            .filter(CurrencyPair.base_pair_id.isnot(None)).all()

    def get_cross_rate_pairs(self) -> List[CurrencyPair]:
        """
        Get all active cross rate pairs (FIAT-FIAT pairs).
        These require two base rates for calculation.
        """
        from app.enums.pair_type import PairType

        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency),
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                CurrencyPair.is_active == True,
                CurrencyPair.pair_type == PairType.CROSS
            ).all()