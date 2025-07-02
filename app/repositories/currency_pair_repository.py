from sqlalchemy.orm import Session, joinedload
from typing import Optional, List
from datetime import datetime
from app.models.currency_pair import CurrencyPair
from app.models.currency import Currency
from app.schemas.currency_pair import CurrencyPairCreate, CurrencyPairUpdate

class CurrencyPairRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_currency_pair(self, pair_data: CurrencyPairCreate) -> CurrencyPair:
        """Create new currency pair"""
        # Generate pair symbol
        from_currency = self.db.query(Currency).filter(Currency.id == pair_data.from_currency_id).first()
        to_currency = self.db.query(Currency).filter(Currency.id == pair_data.to_currency_id).first()
        
        if not from_currency or not to_currency:
            raise ValueError("Invalid currency IDs provided")
        
        pair_symbol = CurrencyPair.create_pair_symbol(from_currency.symbol, to_currency.symbol)
        
        db_pair = CurrencyPair(
            from_currency_id=pair_data.from_currency_id,
            to_currency_id=pair_data.to_currency_id,
            pair_symbol=pair_symbol,
            description=pair_data.description,
            is_active=pair_data.is_active,
            is_monitored=pair_data.is_monitored
        )
        
        self.db.add(db_pair)
        self.db.commit()
        self.db.refresh(db_pair)
        return db_pair

    def get_by_id(self, pair_id: int) -> Optional[CurrencyPair]:
        """Get currency pair by ID with related currencies"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
            .filter(CurrencyPair.id == pair_id).first()

    def get_by_symbol(self, pair_symbol: str) -> Optional[CurrencyPair]:
        """Get currency pair by symbol"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
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

    def get_all_pairs(self, skip: int = 0, limit: int = 100, active_only: bool = False) -> List[CurrencyPair]:
        """Get all currency pairs with pagination"""
        query = self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))
        
        if active_only:
            query = query.filter(CurrencyPair.is_active == True)
        
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

    def get_pairs_by_currency(self, currency_id: int) -> List[CurrencyPair]:
        """Get all pairs that include a specific currency"""
        return self.db.query(CurrencyPair)\
            .options(joinedload(CurrencyPair.from_currency), 
                    joinedload(CurrencyPair.to_currency))\
            .filter(
                (CurrencyPair.from_currency_id == currency_id) |
                (CurrencyPair.to_currency_id == currency_id)
            ).all()

    def update_currency_pair(self, pair_id: int, pair_data: CurrencyPairUpdate) -> Optional[CurrencyPair]:
        """Update currency pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return None
        
        update_data = pair_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(pair, field, value)
        
        pair.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(pair)
        return pair

    def delete_currency_pair(self, pair_id: int) -> bool:
        """Delete currency pair"""
        pair = self.get_by_id(pair_id)
        if not pair:
            return False
        
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