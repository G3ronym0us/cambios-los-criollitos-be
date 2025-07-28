from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from contextlib import asynccontextmanager
from app.services.scraper_service import BinanceP2PScraperService
from app.routers import scraping, auth, currency, currency_pair, binance, rates
from app.database.connection import get_db

# Modelos Pydantic
class RateResponse(BaseModel):
    id: str
    from_currency: str
    to_currency: str
    rate: float
    percentage: float
    last_updated: Optional[datetime] = None

class UserRatesResponse(BaseModel):
    user_id: str
    user_name: str
    rates: List[RateResponse]

class ConversionRequest(BaseModel):
    amount: float
    from_currency: str
    to_currency: str
    user_id: str

class ConversionResponse(BaseModel):
    original_amount: float
    converted_amount: float
    rate: float
    from_currency: str
    to_currency: str
    user: str
    percentage: float

# Estado global
app_state = {
    "last_update": datetime.now(),
    "scraper_available": False
}

# Gestión del ciclo de vida de la aplicación (nueva forma en FastAPI)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Sistema iniciado correctamente")
    yield
    # Shutdown
    print("🛑 Sistema detenido")

# Crear aplicación FastAPI
app = FastAPI(
    title="Sistema de Tasas de Cambio",
    description="API para gestión de tasas de cambio P2P",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scraping.router)
app.include_router(auth.router)
app.include_router(currency.router)
app.include_router(currency_pair.router)
app.include_router(binance.router)
app.include_router(rates.router)

# Rutas de la API
@app.get("/")
async def root():
    return {
        "message": "🏦 Sistema de Tasas de Cambio API",
        "status": "running",
        "version": "1.0.0",
        "last_update": app_state["last_update"],
        "endpoints": {
            "docs": "/docs",
            "health": "/api/health",
            "users": "/api/users",
            "rates": "/api/rates",
            "latest_rates": "/rates/latest/{from_currency}/{to_currency}",
            "pair_rates": "/rates/pair/{pair_symbol}/latest",
            "convert": "/api/convert"
        }
    }

@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(),
        "last_update": app_state["last_update"],
        "scraper_available": app_state["scraper_available"],
    }

@app.get("/api/rates")
async def get_all_rates(db: Session = Depends(get_db)):
    """Obtener todas las tasas de cambio desde la base de datos"""
    from app.repositories.exchange_rate_repository import ExchangeRateRepository
    
    repo = ExchangeRateRepository(db)
    rates = repo.get_active_rates()
    
    return rates

@app.get("/api/rates/{user_id}", response_model=UserRatesResponse)
async def get_user_rates(user_id: str):
    scraper = BinanceP2PScraperService()
    rates = await scraper.get_offers(user_id)
    
    for rate_data in rates:
        rates.append(RateResponse(
            id=rate_data["id"],
            from_currency=rate_data["from"],
            to_currency=rate_data["to"],
            rate=rate_data["rate"],
            percentage=rate_data["percentage"],
            last_updated=app_state["last_update"]
        ))
    
    return UserRatesResponse(
        user_id=user_id,
        user_name=user_id,
        rates=rates
    )

@app.get("/api/users")
async def get_users():
    """Obtener lista de usuarios"""
    return []

@app.post("/api/convert", response_model=ConversionResponse)
async def convert_currency(request: ConversionRequest, db: Session = Depends(get_db)):
    """Calcular conversión de moneda usando tasas de base de datos con prioridad a manuales"""
    from app.repositories.exchange_rate_repository import ExchangeRateRepository
    
    repo = ExchangeRateRepository(db)
    rate_obj = repo.get_latest_rate(request.from_currency.upper(), request.to_currency.upper())
    
    if rate_obj:
        # Usar la tasa activa (ya incluye manual si está configurada)
        converted_amount = request.amount * rate_obj.rate
        
        return ConversionResponse(
            original_amount=request.amount,
            converted_amount=round(converted_amount, 2),
            rate=rate_obj.rate,
            from_currency=request.from_currency,
            to_currency=request.to_currency,
            user=request.user_id,
            percentage=rate_obj.percentage or 0
        )
    
    # Fallback al sistema anterior si no se encuentra en BD
    scraper = BinanceP2PScraperService()
    rates = await scraper.get_offers(request.user_id)
    
    # Buscar la tasa de cambio
    for rate_data in rates:
        if (rate_data["from"] == request.from_currency and 
            rate_data["to"] == request.to_currency):
            
            converted_amount = request.amount * rate_data["rate"]
            return ConversionResponse(
                original_amount=request.amount,
                converted_amount=round(converted_amount, 2),
                rate=rate_data["rate"],
                from_currency=request.from_currency,
                to_currency=request.to_currency,
                user=request.user_id,
                percentage=rate_data["percentage"]
            )
    
    # Mostrar tasas disponibles para ayudar al usuario
    available_conversions = [
        f"{rate['from']} → {rate['to']}" 
        for rate in rates
    ]
    
    raise HTTPException(
        status_code=404, 
            detail=f"Tasa de cambio no disponible. Conversiones disponibles para {request.user_id}: {available_conversions}"
    )

@app.get("/api/currencies")
async def get_currencies():
    """Obtener todas las monedas disponibles"""
    currencies = set()
    
    scraper = BinanceP2PScraperService()
    rates = await scraper.get_all_rates()
    
    for rate in rates:
        currencies.add(rate["from"])
        currencies.add(rate["to"])
    
    return {
        "currencies": sorted(list(currencies)),
        "count": len(currencies)
    }

@app.get("/api/user/{user_id}/currencies")
async def get_user_currencies(user_id: str):
    """Obtener monedas disponibles para un usuario específico"""
    scraper = BinanceP2PScraperService()
    rates = await scraper.get_offers(user_id)
    
    currencies = set()
    
    for rate in rates:
        currencies.add(rate["from"])
        currencies.add(rate["to"])
    
    return {
        "user": user_id,
        "currencies": sorted(list(currencies)),
        "count": len(currencies)
    }