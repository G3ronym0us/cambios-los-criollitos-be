from typing import List, Dict, Optional
from sqlalchemy.orm import Session, joinedload
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.exchange_rate_repository import ExchangeRateRepository
import logging

logger = logging.getLogger(__name__)

class DerivedRateService:
    """
    Service to calculate derived rates based on base pairs configuration from database
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.pair_repo = CurrencyPairRepository(db)
        self.rate_repo = ExchangeRateRepository(db)
    
    def calculate_derived_rates(self, base_rates: List[ExchangeRate]) -> List[ExchangeRate]:
        """
        Calculate derived rates based on database configuration
        
        Args:
            base_rates: List of base exchange rates from scraping
            
        Returns:
            List of derived exchange rates
        """
        derived_rates = []
        
        # Get all pairs that have a base_pair configured
        derived_pairs = self._get_derived_pairs()
        
        # Create a mapping of base rates for quick lookup
        base_rate_map = self._create_base_rate_map(base_rates)
        
        for derived_pair in derived_pairs:
            try:
                derived_rate = self._calculate_single_derived_rate(derived_pair, base_rate_map)
                if derived_rate:
                    derived_rates.append(derived_rate)
            except Exception as e:
                logger.error(f"Error calculating derived rate for {derived_pair.pair_symbol}: {e}")
                continue
        
        return derived_rates
    
    def _get_derived_pairs(self) -> List[CurrencyPair]:
        """Get all currency pairs that have a base_pair configured"""
        return self.db.query(CurrencyPair)\
            .options(
                joinedload(CurrencyPair.from_currency),
                joinedload(CurrencyPair.to_currency),
                joinedload(CurrencyPair.base_pair).joinedload(CurrencyPair.from_currency),
                joinedload(CurrencyPair.base_pair).joinedload(CurrencyPair.to_currency)
            )\
            .filter(
                CurrencyPair.base_pair_id.isnot(None),
                CurrencyPair.is_active == True
            ).all()
    
    def _create_base_rate_map(self, base_rates: List[ExchangeRate]) -> Dict[str, ExchangeRate]:
        """Create a mapping of currency pairs to their rates"""
        rate_map = {}
        for rate in base_rates:
            key = f"{rate.from_currency}-{rate.to_currency}"
            rate_map[key] = rate
        return rate_map
    
    def _calculate_single_derived_rate(self, derived_pair: CurrencyPair, base_rate_map: Dict[str, ExchangeRate]) -> Optional[ExchangeRate]:
        """
        Calculate a single derived rate based on its base pair configuration
        
        Args:
            derived_pair: The currency pair to calculate (with base_pair loaded)
            base_rate_map: Mapping of base rates
            
        Returns:
            Calculated ExchangeRate or None if cannot be calculated
        """
        if not derived_pair.base_pair:
            logger.warning(f"Derived pair {derived_pair.pair_symbol} has no base_pair loaded")
            return None
        
        base_pair = derived_pair.base_pair
        
        # Look for the base rate in our map using currency symbols
        base_key = f"{base_pair.from_currency.symbol}-{base_pair.to_currency.symbol}"
        base_rate = base_rate_map.get(base_key)
        
        if not base_rate:
            logger.warning(f"Base rate not found for {base_key}")
            return None
        
        # Use the configuration from the database
        percentage = float(derived_pair.derived_percentage) if derived_pair.derived_percentage else None
        inverse_percentage = derived_pair.use_inverse_percentage
        
        # Create the derived rate
        derived_rate = ExchangeRate.create_safe(
            from_currency=derived_pair.from_currency.symbol,
            to_currency=derived_pair.to_currency.symbol,
            rate=base_rate.rate,
            source=f"{base_rate.source}_derived",
            percentage=percentage,
            inverse_percentage=inverse_percentage
        )
        
        if derived_rate:
            percentage_info = f"{percentage}%" if percentage else "no percentage"
            inverse_info = " (inverse)" if inverse_percentage else ""
            logger.info(f"Calculated derived rate {derived_pair.pair_symbol}: {derived_rate.rate} (base: {base_rate.rate}, {percentage_info}{inverse_info})")
        
        return derived_rate
    
    def update_derived_rates_in_db(self, derived_rates: List[ExchangeRate]) -> int:
        """
        Update derived rates in the database
        
        Returns:
            Number of rates updated
        """
        updated_count = 0
        
        for rate in derived_rates:
            try:
                # Check if rate already exists
                existing_rate = self.rate_repo.get_rate(rate.from_currency, rate.to_currency)
                
                if existing_rate and not existing_rate.is_manual:
                    # Update existing automatic rate
                    existing_rate.update_automatic_rate(rate.rate)
                    existing_rate.source = rate.source
                    existing_rate.percentage = rate.percentage
                    existing_rate.inverse_percentage = rate.inverse_percentage
                    self.db.commit()
                    updated_count += 1
                elif not existing_rate:
                    # Create new rate
                    self.db.add(rate)
                    self.db.commit()
                    updated_count += 1
                    
            except Exception as e:
                logger.error(f"Error updating derived rate {rate.from_currency}-{rate.to_currency}: {e}")
                self.db.rollback()
                continue
        
        return updated_count
    
    def get_derived_pairs_summary(self) -> List[Dict]:
        """
        Get a summary of all derived pairs configuration
        
        Returns:
            List of dictionaries with derived pair information
        """
        derived_pairs = self._get_derived_pairs()
        
        summary = []
        for pair in derived_pairs:
            summary.append({
                "id": pair.id,
                "pair_symbol": pair.pair_symbol,
                "from_currency": pair.from_currency.symbol,
                "to_currency": pair.to_currency.symbol,
                "base_pair_symbol": pair.base_pair.pair_symbol if pair.base_pair else None,
                "derived_percentage": float(pair.derived_percentage) if pair.derived_percentage else None,
                "use_inverse_percentage": pair.use_inverse_percentage,
                "is_active": pair.is_active
            })
        
        return summary