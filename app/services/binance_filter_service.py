import httpx
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class BinanceFilterService:
    """Service to fetch filter conditions from Binance P2P API"""
    
    BASE_URL = "https://p2p.binance.com/bapi/c2c/v2/public/c2c/adv/filter-conditions"
    ICON_BASE_URL = "https://bin.bnbstatic.com"
    
    @classmethod
    async def get_filter_conditions(cls, fiat_currency: str) -> Optional[Dict[str, Any]]:
        """
        Fetch filter conditions for a specific fiat currency from Binance P2P API
        
        Args:
            fiat_currency: The fiat currency code (e.g., 'VES', 'COP', 'BRL')
            
        Returns:
            Dict containing trade methods with identifier and complete icon URL
        """
        payload = {
            "fiat": fiat_currency.upper(),
            "classifies": [
                "mass",
                "profession", 
                "fiat_trade"
            ]
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(cls.BASE_URL, json=payload)
                response.raise_for_status()
                
                data = response.json()
                
                # Check if response is successful
                if data.get("code") != "000000":
                    logger.error(f"Binance API error: {data.get('message')}")
                    return None
                
                # Extract and format trade methods
                trade_methods = data.get("data", {}).get("tradeMethods", [])
                formatted_methods = []
                
                for method in trade_methods:
                    icon_url = method.get("iconUrlColor", "")
                    full_icon_url = f"{cls.ICON_BASE_URL}{icon_url}" if icon_url else ""
                    
                    formatted_methods.append({
                        "identifier": method.get("identifier", ""),
                        "icon_url": full_icon_url,
                        "name": method.get("tradeMethodName", ""),
                        "short_name": method.get("tradeMethodShortName", ""),
                        "bg_color": method.get("tradeMethodBgColor", "")
                    })
                
                return {
                    "fiat_currency": fiat_currency.upper(),
                    "trade_methods": formatted_methods
                }
                
        except httpx.TimeoutException:
            logger.error(f"Timeout while fetching Binance filter conditions for {fiat_currency}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error while fetching Binance filter conditions: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error while fetching Binance filter conditions: {e}")
            return None

    @classmethod
    async def get_trade_methods_only(cls, fiat_currency: str) -> List[Dict[str, str]]:
        """
        Get only trade method identifiers and icon URLs for a fiat currency
        
        Args:
            fiat_currency: The fiat currency code
            
        Returns:
            List of dicts with identifier and icon_url
        """
        result = await cls.get_filter_conditions(fiat_currency)
        if not result:
            return []
        
        return [
            {
                "identifier": method["identifier"],
                "icon_url": method["icon_url"]
            }
            for method in result.get("trade_methods", [])
            if method.get("identifier")
        ]