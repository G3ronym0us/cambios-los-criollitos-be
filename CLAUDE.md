# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Application Overview

This is a **FastAPI currency exchange rate tracking system** that scrapes P2P exchange rates from Binance for Latin American currencies (VES, COP, BRL) and provides real-time conversion services. The backend includes user authentication with role-based permissions, background scraping tasks, and a comprehensive API for rate management.

## Core Architecture

**Stack**: FastAPI + PostgreSQL + Redis + Celery + Docker
**Key Services**: Web API, Background workers, Scheduled tasks, Database, Cache

### Directory Structure
- `app/main.py` - FastAPI application entry point with main endpoints
- `app/core/` - Configuration, security, authentication, dependencies
- `app/models/` - SQLAlchemy database models (User, ExchangeRate, Transaction)
- `app/routers/` - API route handlers grouped by feature
- `app/services/` - Business logic (scraping, rate calculation, conversions)
- `app/repositories/` - Data access layer
- `app/schemas/` - Pydantic models for request/response validation
- `app/tasks/` - Celery background tasks
- `alembic/` - Database migrations

## Development Commands

### Docker Environment (Recommended)
```bash
# Start all services (API + DB + Redis + Celery workers)
docker-compose up -d

# View logs
docker-compose logs -f backend

# Stop services
docker-compose down

# Rebuild after code changes
docker-compose up --build
```

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start API server (requires PostgreSQL and Redis running)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker
celery -A app.tasks.scraping_tasks.celery_app worker --loglevel=info

# Start Celery beat scheduler
celery -A app.tasks.scraping_tasks.celery_app beat --loglevel=info
```

### Database Operations
```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Downgrade migration
alembic downgrade -1
```

### Testing
```bash
# Run tests (pytest available in requirements.txt)
pytest

# Run with coverage
pytest --cov=app
```

## Key Configuration

**Environment Variables** (set in docker-compose.yml or .env):
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string  
- `JWT_SECRET_KEY` - JWT token signing key
- `PYTHONPATH=/app` - Required for module imports

**Main config file**: `app/core/config.py` - Comprehensive Pydantic settings

## Authentication System

**User Roles**: USER (read-only) → MODERATOR (manage rates) → ROOT (full admin)
**JWT-based** authentication with refresh tokens
**Key files**: `app/core/security.py`, `app/routers/auth.py`, `app/models/user.py`

## Scraping System

**Primary source**: Binance P2P API via `app/services/scraper_service.py`
**Currencies**: VES, COP, BRL against USDT, with PayPal/Zelle variants
**Background tasks**: Scheduled scraping via Celery in `app/tasks/scraping_tasks.py`
**Manual triggers**: Available through `/api/scraping/` endpoints

## API Structure

**Main endpoints**:
- `/` - API status and docs
- `/api/rates` - Current exchange rates
- `/api/convert` - Currency conversion
- `/auth/` - Authentication endpoints
- `/docs` - FastAPI automatic documentation

**Service architecture**: Router → Repository → Database Model pattern

## Important Notes

- **Database**: Uses PostgreSQL with SQLAlchemy 2.0 syntax
- **Async**: Services use both async/await and sync patterns appropriately  
- **Selenium**: Web scraping setup with Chrome/Chromium in Docker
- **Background processing**: Celery tasks for rate updates every hour
- **Security**: JWT tokens, password hashing, role-based access control

## Root User Setup

Create initial admin user:
```bash
# Via Docker
docker-compose exec backend python create_root_user.py

# Or use CLI tool
python app/cli/create_root_user.py create
python app/cli/create_root_user.py list  # List existing users
```

## Critical Architecture Details

### Scraping System Architecture

**Multi-layered scraping with rate derivation**:
- **Base rates**: Fetches VES, COP, BRL ↔ USDT from Binance P2P API
- **Derived rates**: Automatically calculates Zelle/PayPal rates with configurable margins (5-12%)
- **Cross rates**: Generates direct fiat-to-fiat conversions (VES↔COP, VES↔BRL, COP↔BRL)
- **Source attribution**: Primary (`binance_p2p`), derived (`binance_p2p_derived`), cross (`binance_p2p_cross`)

**Scraper pattern**: Abstract `BaseScraper` class with `initialize()`, `get_rates()`, `close()` methods. `ScrapingService` orchestrates multiple scrapers and handles database persistence via `ExchangeRateRepository`.

### ExchangeRate Model Important Notes

**Always use `ExchangeRate.create_safe()` method** when creating instances with percentage adjustments:
```python
# CORRECT - handles percentage and validation
rate = ExchangeRate.create_safe('VES', 'USDT', 45.5, source='binance', percentage=5, inverse_percentage=True)

# INCORRECT - will cause "percentage is invalid keyword" error
rate = ExchangeRate(from_currency='VES', to_currency='USDT', rate=45.5, percentage=5)
```

### Authentication & Authorization Flow

**Role hierarchy**: USER (read-only) → MODERATOR (rate management) → ROOT (full admin)
**Multi-method auth**: Supports JWT via Authorization header, cookies, and query parameters
**Security features**: Failed login tracking, account lockout (15 min), configurable password policies

### Background Tasks & Redis Configuration

**Critical**: Docker Redis runs on port 6380, local Redis on 6379. Ensure `REDIS_URL` environment variable matches your setup:
- Docker: `redis://redis:6379/0` (internal Docker network)  
- Local: `redis://localhost:6380/0` (mapped port)

**Celery tasks**: Hourly scraping via `celery_beat`, manual triggers via API endpoints

### Database Patterns

**Repository pattern**: Use repositories (`UserRepository`, `ExchangeRateRepository`) rather than direct model access
**Migrations**: Alembic configured with dynamic URL from settings. Always test migrations in development first
**Connection**: Async-capable but uses sync patterns where needed for Celery compatibility

## Development Troubleshooting

### Common Issues

1. **Celery tasks stuck in PENDING**: Check Redis port configuration mismatch between local/Docker
2. **ExchangeRate creation errors**: Use `create_safe()` method instead of direct constructor
3. **Import errors**: Ensure `PYTHONPATH=/app` is set in environment
4. **Migration errors**: Run `alembic upgrade head` after pulling new migrations

### Testing Framework

**Status**: ⚠️ No tests currently implemented despite pytest in requirements
**Recommended structure**:
```
tests/
├── conftest.py           # Fixtures for auth, database
├── test_auth.py          # JWT, role-based access  
├── test_scraping.py      # Scraper services
├── test_repositories.py  # Data access layer
└── test_api/            # Endpoint integration tests
```

### Environment Variables

**Required for development**:
```bash
DATABASE_URL=postgresql://tasas_user:tasas_password@localhost:5433/tasas_db
REDIS_URL=redis://localhost:6380/0
JWT_SECRET_KEY=your-32-character-minimum-secret-key
ROOT_USER_EMAIL=admin@example.com
ROOT_USER_PASSWORD=your-secure-password
PYTHONPATH=/app
```