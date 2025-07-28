import httpx
from typing import List, Dict, Any, Optional, Tuple
import logging
from decimal import Decimal
from app.models.currency import CurrencyType

logger = logging.getLogger(__name__)

class BinanceValidationService:
    """Service to validate Binance P2P configuration by checking if ads exist"""
    
    SEARCH_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    
    @classmethod
    async def validate_currency_pair_configuration(
        cls, 
        from_currency: str,
        to_currency: str,
        from_currency_type: CurrencyType,
        to_currency_type: CurrencyType,
        banks_to_track: List[str],
        amount_to_track: Decimal
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Validate if a currency pair configuration returns valid ads in Binance P2P
        
        Args:
            from_currency: Source currency symbol
            to_currency: Target currency symbol  
            from_currency_type: CurrencyType enum value
            to_currency_type: CurrencyType enum value
            banks_to_track: List of payment methods to search
            amount_to_track: Amount to search for
            
        Returns:
            Tuple of (is_valid, error_message, sample_ad_data)
        """
        try:
            # Determine crypto asset and fiat currency
            if from_currency_type == CurrencyType.FIAT and to_currency_type == CurrencyType.CRYPTO:
                # FIAT -> CRYPTO: User wants to BUY crypto with fiat
                crypto_asset = to_currency
                fiat_currency = from_currency
                trade_type = "BUY"
            elif from_currency_type == CurrencyType.CRYPTO and to_currency_type == CurrencyType.FIAT:
                # CRYPTO -> FIAT: User wants to SELL crypto for fiat
                crypto_asset = from_currency
                fiat_currency = to_currency
                trade_type = "SELL"
            else:
                return False, "Invalid currency pair: must be between FIAT and CRYPTO currencies", None
            
            # Test each bank to see if at least one returns results
            valid_banks = []
            sample_ads = []
            
            for bank in banks_to_track:
                payload = {
                    "page": 1,
                    "rows": 5,  # Just need a few to validate
                    "payTypes": [bank],
                    "asset": crypto_asset.upper(),
                    "tradeType": trade_type,
                    "fiat": fiat_currency.upper(),
                    "transAmount": float(amount_to_track)
                }
                
                ads = await cls._search_binance_ads(payload)
                
                if ads and len(ads) > 0:
                    valid_banks.append(bank)
                    sample_ads.extend(ads[:2])  # Keep a couple of sample ads
                    
            if not valid_banks:
                return False, f"No ads found for any of the specified banks {banks_to_track} with {from_currency}/{to_currency} pair and amount {amount_to_track}", None
            
            if len(valid_banks) < len(banks_to_track):
                invalid_banks = [b for b in banks_to_track if b not in valid_banks]
                logger.warning(f"Some banks returned no ads: {invalid_banks}")
            
            validation_result = {
                "valid_banks": valid_banks,
                "invalid_banks": [b for b in banks_to_track if b not in valid_banks],
                "total_ads_found": len(sample_ads),
                "sample_ads": sample_ads[:3],  # Return max 3 sample ads
                "search_params": {
                    "crypto_asset": crypto_asset,
                    "fiat_currency": fiat_currency,
                    "amount": float(amount_to_track),
                    "trade_type": trade_type,
                    "pair": f"{from_currency}/{to_currency}"
                }
            }
            
            success_msg = f"Configuration valid. Found ads for {len(valid_banks)}/{len(banks_to_track)} banks"
            if len(valid_banks) < len(banks_to_track):
                success_msg += f". Warning: No ads found for {validation_result['invalid_banks']}"
            
            return True, success_msg, validation_result
            
        except Exception as e:
            logger.error(f"Error validating Binance configuration: {e}")
            return False, f"Error validating configuration: {str(e)}", None

    @classmethod
    async def _search_binance_ads(cls, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Search for ads in Binance P2P with given payload
        
        Args:
            payload: Search parameters for Binance API
            
        Returns:
            List of ad data
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(cls.SEARCH_URL, json=payload)
                response.raise_for_status()
                
                data = response.json()
                
                # Check if response is successful
                if data.get("code") != "000000":
                    logger.error(f"Binance search API error: {data.get('message')}")
                    return []
                
                # Extract ads from response
                ads_data = data.get("data", [])
                
                # Format ads for easier consumption
                formatted_ads = []
                for ad in ads_data:
                    formatted_ads.append({
                        "adv_no": ad.get("advNo", ""),
                        "price": ad.get("price", ""),
                        "min_amount": ad.get("minSingleTransAmount", ""),
                        "max_amount": ad.get("maxSingleTransAmount", ""),
                        "available_amount": ad.get("surplusAmount", ""),
                        "payment_methods": [pm.get("identifier", "") for pm in ad.get("tradeMethods", [])],
                        "merchant_name": ad.get("advertiser", {}).get("nickName", ""),
                        "completion_rate": ad.get("advertiser", {}).get("monthFinishRate", 0)
                    })
                
                return formatted_ads
                
        except httpx.TimeoutException:
            logger.error(f"Timeout while searching Binance ads")
            return []
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error while searching Binance ads: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error while searching Binance ads: {e}")
            return []