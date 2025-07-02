from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from app.models.currency import Currency, CurrencyType
from app.schemas.currency import CurrencyCreate, CurrencyUpdate

class CurrencyRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_currency(self, currency_data: CurrencyCreate) -> Currency:
        """Create new currency"""
        db_currency = Currency(
            name=currency_data.name,
            symbol=currency_data.symbol.upper(),
            description=currency_data.description,
            currency_type=currency_data.currency_type
        )
        
        self.db.add(db_currency)
        self.db.commit()
        self.db.refresh(db_currency)
        return db_currency

    def get_by_id(self, currency_id: int) -> Optional[Currency]:
        """Get currency by ID"""
        return self.db.query(Currency).filter(Currency.id == currency_id).first()

    def get_by_symbol(self, symbol: str) -> Optional[Currency]:
        """Get currency by symbol"""
        return self.db.query(Currency).filter(Currency.symbol == symbol.upper()).first()

    def get_all_currencies(self, skip: int = 0, limit: int = 100) -> List[Currency]:
        """Get all currencies with pagination"""
        return self.db.query(Currency).offset(skip).limit(limit).all()

    def get_by_type(self, currency_type: CurrencyType) -> List[Currency]:
        """Get currencies by type"""
        return self.db.query(Currency).filter(Currency.currency_type == currency_type).all()

    def update_currency(self, currency_id: int, currency_data: CurrencyUpdate) -> Optional[Currency]:
        """Update currency"""
        currency = self.get_by_id(currency_id)
        if not currency:
            return None
        
        update_data = currency_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            if field == 'symbol' and value:
                value = value.upper()
            setattr(currency, field, value)
        
        currency.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(currency)
        return currency

    def delete_currency(self, currency_id: int) -> bool:
        """Delete currency"""
        currency = self.get_by_id(currency_id)
        if not currency:
            return False
        
        self.db.delete(currency)
        self.db.commit()
        return True

    def symbol_exists(self, symbol: str, exclude_id: Optional[int] = None) -> bool:
        """Check if symbol already exists"""
        query = self.db.query(Currency).filter(Currency.symbol == symbol.upper())
        if exclude_id:
            query = query.filter(Currency.id != exclude_id)
        return query.first() is not None

    def search_currencies(self, search_term: str) -> List[Currency]:
        """Search currencies by name or symbol"""
        search_pattern = f"%{search_term}%"
        return self.db.query(Currency).filter(
            Currency.name.ilike(search_pattern) | 
            Currency.symbol.ilike(search_pattern)
        ).all()