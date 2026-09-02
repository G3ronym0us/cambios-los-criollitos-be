# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Conventions

- **Commit messages must always be in English.**

## Application Overview

This is a **FastAPI currency exchange rate tracking system** that scrapes P2P exchange rates from Binance for Latin American currencies (VES, COP, BRL) and provides real-time conversion services. The backend includes user authentication with role-based permissions, background scraping tasks, commission configuration, and transaction tracking.

## Core Architecture

**Stack**: FastAPI + PostgreSQL + Redis + Celery + Docker

### Directory Structure
- `app/main.py` - FastAPI app entry point, CORS/proxy middleware, router inclusion
- `app/core/` - Configuration (`config.py`), JWT security (`security.py`), dependency injection (`dependencies.py`)
- `app/models/` - SQLAlchemy ORM models
- `app/routers/` - API route handlers grouped by feature
- `app/services/` - Business logic and scraping orchestration
- `app/repositories/` - Data access layer (always use these, never access models directly)
- `app/schemas/` - Pydantic request/response models
- `app/tasks/scraping_tasks.py` - Celery task definitions and beat schedule
- `app/enums/` - `UserRole`, `PairType`, currency enums
- `alembic/` - Database migrations (24+ versions)

## Development Commands

### Docker (Recommended)
```bash
docker-compose up -d                        # Start all services
docker-compose logs -f backend              # View API logs
docker-compose up --build                   # Rebuild after dependency changes
docker-compose exec backend python create_root_user.py  # Create root user
```

### Local Development
```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
celery -A app.tasks.scraping_tasks.celery_app worker --loglevel=info
celery -A app.tasks.scraping_tasks.celery_app beat --loglevel=info
```

### Database
```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```

### Testing
```bash
pytest
pytest --cov=app
```
> Son ~510 y corren contra un Postgres real en `:5433` (base `tasas_test`, recreada por
> sesión). Si no hay Postgres local, los de integración **se saltan solos** en vez de fallar.
> Ojo: dos corridas a la vez chocan por la base — da `database "tasas_test" is being accessed
> by other users`, y no es un fallo del código.

## Environment Variables

```bash
DATABASE_URL=postgresql://tasas_user:tasas_password@localhost:5433/tasas_db
REDIS_URL=redis://localhost:6380/0       # Local: 6380 | Docker internal: redis:6379
JWT_SECRET_KEY=<32+ char secret>
ROOT_USER_EMAIL=admin@example.com
ROOT_USER_PASSWORD=your-secure-password
PYTHONPATH=/app                          # Required for module imports
APP_ENV=development                      # development | production | testing
```

## Root User Setup

```bash
python app/cli/create_root_user.py create
python app/cli/create_root_user.py list
```

## Architecture Details

### Request Flow

**Router → Repository → Database Model**. Routers use FastAPI `Depends()` for auth and DB session injection. Never access models directly from routers—always go through repositories.

### Authentication & Authorization

**Role hierarchy**: `USER` (read-only) → `MODERATOR` (rate management) → `ROOT` (full admin)

Multi-method JWT auth: `Authorization: Bearer <token>`, cookies, or query params. Supports failed login tracking, 15-min account lockout, and configurable password policies.

**Dependency injection pattern** (`app/core/dependencies.py`):
- `get_current_user()` → validates JWT, returns User
- `get_current_active_user()` → validates active/verified/unlocked
- `require_role(UserRole.MODERATOR)` → role-gated endpoint decorator
- `require_permission("rates:write")` → permission-gated decorator

### Scraping System

**Three-layer rate system**:
1. **Base rates** (`binance_p2p`): Direct Binance P2P API hits for FIAT↔USDT pairs. Uses `aiohttp` (not Selenium) for the P2P search API.
2. **Derived rates** (`binance_p2p_derived`): Calculated from base pairs with configurable `derived_percentage` + `use_inverse_percentage`. Configured per `CurrencyPair` via `base_pair_id`.
3. **Cross rates** (`binance_p2p_cross`): Direct fiat-to-fiat conversions (VES↔COP, VES↔BRL, COP↔BRL) computed from base pairs.

**Scraper lifecycle**: `ScrapingService` calls `initialize()` → `get_rates()` → `close()` on each `BaseScraper` implementation. Celery runs this hourly; manual trigger available via `POST /scrape/manual`.

**Binance tracking configuration**: A `CurrencyPair` must have `binance_tracked=True`, a `banks_to_track` JSON array (e.g., `["Zelle", "PayPal"]`), and `amount_to_track` set. Only FIAT/CRYPTO pairs are valid for direct Binance tracking.

### ExchangeRate Model — Critical

**Always use `ExchangeRate.create_safe()`** when creating instances with percentage adjustments:
```python
# CORRECT
rate = ExchangeRate.create_safe('VES', 'USDT', 45.5, source='binance', percentage=5, inverse_percentage=True)

# INCORRECT — will raise "percentage is invalid keyword" error
rate = ExchangeRate(from_currency='VES', to_currency='USDT', rate=45.5, percentage=5)
```

**Manual rate override**: `ExchangeRate` supports `is_manual` flag. When active, `manual_rate` overrides the automatic rate; `automatic_rate` is preserved for fallback. Use `set_manual_rate()` / `remove_manual_rate()` methods.

### CurrencyPair Configuration

`CurrencyPair` drives scraping and derived rate behavior:
- **Binance tracking**: `binance_tracked=True` + `banks_to_track` + `amount_to_track`
- **Derived rates**: `base_pair_id` (FK to another CurrencyPair) + `derived_percentage` + `use_inverse_percentage`
- **Pair type**: `PairType` enum — `BASE`, `DERIVED`, or `CROSS`
- All models have a `uuid` column (via `UUIDMixin`) used in API responses; internal PKs are integer IDs

### Commission & Transaction System

**CommissionConfiguration**: Per-currency-pair profit distribution config with named splits (`CommissionConfigurationSplit`) assigning percentages to specific users.

**Transaction**: Records currency conversions with `from_amount`, `to_amount`, `exchange_rate`, profit tracking, and `TransactionProfitSplit` for multi-user profit distribution. Status: `PENDING → COMPLETED | CANCELLED | FAILED`.

### Redis & Celery

**Port distinction**: Docker Redis container is on port `6379` internally but mapped to `6380` on host. Local Redis typically runs on `6379`.
- Docker `REDIS_URL`: `redis://redis:6379/0`
- Local `REDIS_URL`: `redis://localhost:6380/0`

Celery beat schedule: hourly scraping. Tasks in `app/tasks/scraping_tasks.py` manage their own `asyncio` event loop since Celery workers run synchronously.

## El negocio, contado como funciona de verdad

Esto no se deduce del código y explica por qué muchas cosas están como están. Léelo antes de
tocar operaciones, fondos o tasas.

### Quién manda qué al grupo de WhatsApp

**Todos los comprobantes de operaciones los sube al grupo Diohandres, el operador.** Quién los
manda NO es una señal de nada. Lo que distingue es el CONTENIDO:

- **Captura de Zelle entrante** → el cliente es de Diohandres, que la recibió directamente.
  Es el escenario `ZELLE_DIRECT`.
- **Comprobante saliente en bolívares** → el cliente es de un SOCIO (Jean, Dionis). El socio
  cobró al cliente en su propio WhatsApp y le pasó a Diohandres los datos y el monto;
  Diohandres paga en Bs y sube el soporte al grupo para que el socio se lo reenvíe a su
  cliente. Es `VIA_PARTNER`, y el gestor es **el socio**, no Diohandres.

Lo que Jean sube al grupo suelen ser **depósitos**, no operaciones.

**Consecuencia que rompe suposiciones:** como el socio cobra al cliente él mismo, **el
comprobante entrante nunca llega al operador**. Una operación `VIA_PARTNER` sin entrante no
es una operación a medias: es la forma normal de ese flujo. Cualquier regla que exija un
comprobante entrante tiene que contemplarlo.

### Los pares que se cambian en efectivo

Lo mismo por otra vía: en un par con `CurrencyPair.settles_in_cash` el cliente paga con
billetes y **no hay comprobante entrante ni lo habrá** — nadie fotografía un billete. Exigirlo
borraba el par entero de «por entregar»: USD-VES tenía 134 operaciones sin cuadrar y CERO con
entrante. Allí el filtro lo hace el ESTADO (sólo `PENDING`), y el beneficiario tampoco se
pide: el gesto está invertido —lo que se marca es que el CLIENTE pagó—, y a quién se le
entregaron los bolívares lo dice el comprobante saliente.

### Los fondos

Tres, y cada uno con una moneda: `Zelle/Paypal` (USD), `Cambios Colombia` (COP),
`Cambios Brasil` (BRL). **No hay fondo en bolívares y es deliberado**: los bolívares se
manejan en efectivo, fuera del libro de fondos, y esa pata no deja movimiento.

Por eso una operación ZELLE-VES cae sola en `Zelle/Paypal`: `_resolve_fund_legs_for_new_op`
traduce ZELLE y PAYPAL a USD y busca el único fondo activo de esa moneda. Si hubiera dos de
la misma moneda **no adivina** a propósito, y la pata queda sin fondo.

Cada fondo tiene a Diohandres más un socio (`Zelle/Paypal`→jean, `Cambios Colombia`→dionis,
`Cambios Brasil`→Diodimar). Diohandres es siempre quien paga.

### La dirección de las tasas

Una entrada `(A→B, rate=r, inverse)` se consume como `amount * r` si el flag es falso y
`amount / r` si es verdadero. **Las dos filas de un mismo par llevan la MISMA magnitud** y
sólo cambia el flag:

    USDT→VES   rate=960  inverse=False   →  VES = USDT × 960
    VES→USDT   rate=967  inverse=True    →  USDT = VES / 967

Así que derivar un sentido del otro **conserva la tasa y voltea sólo el flag**. Invertir
además el número aplica la reciprocidad dos veces y desvía por `r²` — con una tasa de 900,
ochocientas mil veces. Ese bug estuvo vivo en las dos capas y cotizó reais con tasa de
bolívares. Si vas a «arreglarlo» de vuelta, corre antes los tests de
`test_whatsapp_rate_resolver.py`.

### Al cambiar el analizador del bot

Endurecerlo para matar cotizaciones fantasma es fácil; hacerlo sin dejar de leer las
cotizaciones de verdad es el problema, y **perder una cotización buena es peor que dejar viva
una zombi**. En `whatsapp-bot` hay dos redes para medirlo, y hay que mirar las dos:
`src/test-corpus.ts` (250 operaciones COMPLETED reales) y `src/score-analyzer.ts` (diff contra
una foto sobre 874). El detalle está en `whatsapp-bot/CLAUDE.md`.

## Common Issues

1. **Celery tasks stuck in PENDING**: Redis port mismatch (`REDIS_URL` env var)
2. **ExchangeRate creation errors**: Use `create_safe()` not direct constructor
3. **Import errors**: Ensure `PYTHONPATH=/app`
4. **Migration errors**: Run `alembic upgrade head` after pulling new code
