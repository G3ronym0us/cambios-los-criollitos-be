# Par ZELLE→USDT sobre paridad — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poder cotizar ZELLE→USDT a 1 Zelle = 0,93 USDT, con el 7% editable como porcentaje del par, sin inventar un tipo de par nuevo.

**Architecture:** Dos filas de datos —un par base `USDT-USDT` con precio manual `1` y un `ZELLE-USDT` derivado de él al 7% inverso— apoyadas en la maquinaria de derivadas que ya existe (`binance_scraper._calculate_dynamic_derived_rates` mezcla las tasas manuales de la DB con las de Binance antes de aplicar el porcentaje). El único código nuevo es un arreglo en `ExchangeRateRepository.set_manual_rate`, que hoy no sabe crear la primera tasa de un par, y un test que fija el mecanismo paridad→derivada.

**Tech Stack:** FastAPI + SQLAlchemy + PostgreSQL, pytest contra Postgres real, Celery para el scrape.

## Global Constraints

- **Los mensajes de commit van en inglés** (convención del backend, `backend/CLAUDE.md`).
- **No hay migración.** Ninguna tarea toca el esquema: `exchange_rates.currency_pair_id` ya existe y es `NOT NULL`; eso es un dato del problema, no algo a cambiar.
- **Los tests necesitan Postgres local en `:5433`.** Sin él la suite se salta sola (`conftest._postgres_available`), lo que NO cuenta como verde.
- **La rama actual (`feat/operacion-dos-fondos`) tiene trabajo ajeno sin commitear** (`app/services/whatsapp_payment_service.py`, `tests/test_payments_attention.py`, `scripts/`, `app/cli/backfill_outgoing_settlements.py`). Cada `git add` de este plan nombra archivos uno por uno. **Nunca `git add -A` ni `git commit -a`.**
- **La tasa se lee siempre como "cuánto de la otra moneda por 1 USDT"** y el flag `inverse_percentage` decide multiplicar o dividir (`apply_rate`: `monto/rate` si inversa, `monto*rate` si no).

---

## File Structure

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `app/repositories/exchange_rate_repository.py` | Modificar `set_manual_rate` para que sepa crear la primera tasa de un par | 1 |
| `tests/test_manual_rate_bootstrap.py` | Crear. Fija que un par recién creado puede recibir su primer precio manual | 1 |
| `tests/test_parity_derived_rates.py` | Crear. Fija que una base con precio manual alimenta a su derivada, con la aritmética del 7% inverso | 2 |
| — | Configuración de datos en producción (dos pares desde el panel) + verificación | 3 |

---

## Task 1: `set_manual_rate` puede crear la primera tasa de un par

Sin esto el plan no arranca: el paso "ponerle precio manual `1` a `USDT-USDT`" falla en silencio.

`set_manual_rate` solo sabe **actualizar**. Cuando no hay tasa previa construye el `ExchangeRate` sin `currency_pair_id` (`exchange_rate_repository.py:148-157`), que es `NOT NULL` en el modelo y en producción (verificado). El `INSERT` revienta, el `except` se lo traga, devuelve `None`, y el endpoint responde *"No exchange rate found for pair"*. O sea: **ningún par nuevo puede recibir su primera tasa desde el panel**, y nadie lo había notado porque hasta hoy todos los pares nacían con tasa del scraper.

**Files:**
- Modify: `app/repositories/exchange_rate_repository.py:139-166` (`set_manual_rate`)
- Test: `tests/test_manual_rate_bootstrap.py` (crear)

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces: `ExchangeRateRepository.set_manual_rate(from_currency: str, to_currency: str, manual_rate: float) -> Optional[ExchangeRate]` — misma firma que hoy; cambia que devuelve una fila nueva (con `currency_pair_id` puesto) cuando el par existe y no tenía tasa, y sigue devolviendo `None` cuando no existe el par.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_manual_rate_bootstrap.py`:

```python
"""
Ponerle el primer precio manual a un par recién creado.

`set_manual_rate` solo sabía ACTUALIZAR: al crear la primera tasa armaba el ExchangeRate sin
`currency_pair_id` —que es NOT NULL—, el INSERT reventaba, el except se lo tragaba y el endpoint
respondía «No exchange rate found for pair». Un par nuevo sin scraper, como la paridad
USDT-USDT de la que cuelga ZELLE-USDT, no tenía forma de recibir su tasa desde el panel.
"""

from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.repositories.exchange_rate_repository import ExchangeRateRepository


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


def _bare_pair(db, frm: str, to: str) -> CurrencyPair:
    """Par sin ninguna tasa todavía: exactamente lo que deja POST /currency-pairs."""
    pair = CurrencyPair(
        from_currency_id=_currency(db, frm).id,
        to_currency_id=_currency(db, to).id,
        pair_symbol=f"{frm}-{to}",
        is_active=True,
    )
    db.add(pair)
    db.flush()
    return pair


def test_manual_rate_creates_the_first_rate_of_a_pair(db):
    """El caso de la paridad: par nuevo, sin tasa, recibe su precio manual."""
    pair = _bare_pair(db, "USDT", "USDT")

    rate = ExchangeRateRepository(db).set_manual_rate("USDT", "USDT", 1.0)

    assert rate is not None, "un par sin tasa previa tiene que poder recibir la primera"
    assert rate.currency_pair_id == pair.id
    assert rate.rate == 1.0
    assert rate.manual_rate == 1.0
    assert rate.is_manual is True
    assert rate.is_active is True


def test_manual_rate_still_updates_an_existing_rate(db):
    """La ruta de siempre no cambia: si ya hay tasa, se actualiza y se guarda la automática."""
    pair = _bare_pair(db, "ZELLE", "USDT")
    repo = ExchangeRateRepository(db)
    from app.models.exchange_rate import ExchangeRate

    db.add(ExchangeRate(
        currency_pair_id=pair.id, from_currency="ZELLE", to_currency="USDT",
        rate=1.05, is_active=True,
    ))
    db.flush()

    rate = repo.set_manual_rate("ZELLE", "USDT", 1.0752688)

    assert rate is not None
    assert rate.rate == 1.0752688
    assert rate.automatic_rate == 1.05, "la tasa automática se conserva para poder volver"
    assert rate.is_manual is True


def test_manual_rate_returns_none_when_the_pair_does_not_exist(db):
    """Sin par no hay tasa: se devuelve None, no se crea una fila huérfana."""
    repo = ExchangeRateRepository(db)

    assert repo.set_manual_rate("VES", "PAYPAL", 123.0) is None
```

- [ ] **Step 2: Correr el test y verificar que falla**

```bash
cd backend && ../venv/bin/python -m pytest tests/test_manual_rate_bootstrap.py -v
```

Esperado: `test_manual_rate_creates_the_first_rate_of_a_pair` **FALLA** en `assert rate is not None` (la excepción de `NOT NULL` se traga dentro del repositorio y devuelve `None`). Los otros dos pasan ya.

Si el primero pasara, **parar**: significa que alguien cambió el repositorio o la nulabilidad de la columna, y el resto del plan hay que revisarlo.

- [ ] **Step 3: Escribir la implementación mínima**

En `app/repositories/exchange_rate_repository.py`, reemplazar el cuerpo de `set_manual_rate`:

```python
    def set_manual_rate(self, from_currency: str, to_currency: str, manual_rate: float) -> Optional[ExchangeRate]:
        """Establecer una tasa manual para un par de monedas"""
        try:
            rate = self.get_latest_rate(from_currency, to_currency)

            if rate:
                # Si existe un registro, actualizarlo
                rate.set_manual_rate(manual_rate)
            else:
                # Primera tasa del par. `currency_pair_id` es NOT NULL, así que hay que
                # resolver el par por sus símbolos: sin esto el INSERT reventaba y un par
                # recién creado (una paridad, cualquier par sin scraper) no podía recibir
                # su tasa desde el panel.
                pair = self.db.query(CurrencyPair).filter(
                    CurrencyPair.pair_symbol == f"{from_currency}-{to_currency}".upper()
                ).first()
                if pair is None:
                    return None

                rate = ExchangeRate(
                    currency_pair_id=pair.id,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    rate=manual_rate,
                    is_active=True,
                    is_manual=True,
                    manual_rate=manual_rate,
                    automatic_rate=None
                )
                self.db.add(rate)

            self.db.commit()
            return rate

        except Exception as e:
            print(f"❌ Error estableciendo tasa manual: {e}")
            self.db.rollback()
            return None
```

`CurrencyPair` ya está importado arriba del archivo (línea 7): no hace falta tocar los imports.

- [ ] **Step 4: Correr los tests y verificar que pasan**

```bash
cd backend && ../venv/bin/python -m pytest tests/test_manual_rate_bootstrap.py -v
```

Esperado: **3 passed**.

- [ ] **Step 5: Correr la suite completa**

```bash
cd backend && ../venv/bin/python -m pytest -q
```

Esperado: la línea base **más 3**. El número absoluto depende de si están presentes los cambios sin commitear de la rama (con ellos son 376 + 3 = 379); lo que importa es que **no falle ninguno**. Si alguno se rompe, algo dependía de que `set_manual_rate` devolviera `None` para un par sin tasa: investigar antes de commitear.

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/repositories/exchange_rate_repository.py tests/test_manual_rate_bootstrap.py
git commit -m "fix: let set_manual_rate create the first rate of a pair

exchange_rates.currency_pair_id is NOT NULL, and set_manual_rate built the
row without it when no rate existed yet. The insert failed, the except
swallowed it and the endpoint answered 'No exchange rate found for pair', so
a freshly created pair could never receive its first rate from the panel.
Resolve the pair by its symbol and set the FK."
```

---

## Task 2: Fijar el mecanismo paridad → derivada

**Files:**
- Test: `tests/test_parity_derived_rates.py` (crear)

**Interfaces:**
- Consumes: `ExchangeRateRepository.set_manual_rate` de la Tarea 1.
- Produces: nada que consuma otra tarea.

Este es un test de caracterización: **debe pasar a la primera**, porque el mecanismo ya existe. Su valor no es descubrir un bug, es dejar clavada la aritmética exacta (7% inverso sobre base 1 → 1,0752688 → 93 de 100) y la dependencia de que las tasas manuales alimenten a las derivadas. Es justo lo que un refactor del scraper podría romper dejando `ZELLE-USDT` congelado en su último valor, sin error visible.

- [ ] **Step 1: Escribir el test**

Crear `tests/test_parity_derived_rates.py`:

```python
"""
Un par derivado colgado de una paridad con precio manual.

ZELLE→USDT no tiene mercado: 1 Zelle = 0,93 USDT es política de la casa, un 7% sobre la
paridad. Como todo porcentaje necesita un par base, la paridad se escribe como un par
`USDT-USDT` con precio manual 1 y `ZELLE-USDT` cuelga de ella al 7% inverso.

Lo que se fija acá es el mecanismo: que `_calculate_dynamic_derived_rates` tome las tasas
MANUALES de la base de datos (no solo las que trae Binance en la corrida) y les aplique el
porcentaje del par. Si eso se rompe, ZELLE-USDT no da error: se queda congelado en su último
valor, que es peor.
"""

import pytest

from app.enums.pair_type import PairType
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.services.scrapers.binance_scraper import BinanceP2PScraper
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


@pytest.fixture
def parity_and_derived(db):
    """La paridad USDT-USDT con precio manual 1 y ZELLE-USDT derivado al 7% inverso."""
    parity = CurrencyPair(
        from_currency_id=_currency(db, "USDT").id,
        to_currency_id=_currency(db, "USDT").id,
        pair_symbol="USDT-USDT",
        pair_type=PairType.BASE,
        is_active=True,
        binance_tracked=False,
        is_monitored=False,
    )
    db.add(parity)
    db.flush()
    ExchangeRateRepository(db).set_manual_rate("USDT", "USDT", 1.0)

    derived = CurrencyPair(
        from_currency_id=_currency(db, "ZELLE").id,
        to_currency_id=_currency(db, "USDT").id,
        pair_symbol="ZELLE-USDT",
        pair_type=PairType.DERIVED,
        base_pair_id=parity.id,
        derived_percentage=7,
        use_inverse_percentage=True,
        is_active=True,
    )
    db.add(derived)
    db.flush()
    return parity, derived


def _zelle_usdt(rates):
    return next(r for r in rates if r.from_currency == "ZELLE" and r.to_currency == "USDT")


def test_derived_rate_is_built_from_the_manual_parity(db, parity_and_derived):
    """Sin una sola tasa de Binance en la corrida, la derivada sale de la paridad manual."""
    rates = []

    BinanceP2PScraper(db)._calculate_dynamic_derived_rates(rates, {})

    rate = _zelle_usdt(rates)
    assert rate.rate == pytest.approx(1.0752688, abs=1e-6)  # 1 / (1 - 0,07)
    assert rate.percentage == 7
    assert rate.inverse_percentage is True


def test_a_hundred_zelle_are_ninety_three_usdt(db, parity_and_derived):
    """La cuenta que ve el cliente, con el mismo apply_rate que usan el bot y el front."""
    rates = []

    BinanceP2PScraper(db)._calculate_dynamic_derived_rates(rates, {})

    rate = _zelle_usdt(rates)
    usdt = WhatsAppRateResolver.apply_rate(100.0, rate.rate, rate.inverse_percentage)
    assert usdt == pytest.approx(93.0, abs=0.01)
```

- [ ] **Step 2: Correr el test y verificar que pasa**

```bash
cd backend && ../venv/bin/python -m pytest tests/test_parity_derived_rates.py -v
```

Esperado: **2 passed**.

- [ ] **Step 3: Comprobar que el test no es vacío**

Un test de caracterización que pasa a la primera puede estar probando nada. Comprobarlo a mano:
comentar la línea que mezcla las tasas manuales en `app/services/scrapers/binance_scraper.py:203`
y sustituirla por `effective_rates = base_rates`.

```bash
cd backend && ../venv/bin/python -m pytest tests/test_parity_derived_rates.py -v
```

Esperado: los **2 FALLAN** con `StopIteration` en `_zelle_usdt` (sin la paridad manual no hay base, el scraper imprime `❌ No se encontró base rate para USDT_USDT` y no genera la derivada).

**Deshacer el cambio en `binance_scraper.py`** y volver a correr: 2 passed. Confirmar con `git diff --stat app/services/scrapers/binance_scraper.py`, que debe salir vacío.

- [ ] **Step 4: Correr la suite completa**

```bash
cd backend && ../venv/bin/python -m pytest -q
```

Esperado: lo mismo que en la Tarea 1 **más 2**, y ninguno en rojo.

- [ ] **Step 5: Commit**

```bash
cd backend
git add tests/test_parity_derived_rates.py
git commit -m "test: pin the parity-to-derived rate mechanism

ZELLE-USDT hangs off a USDT-USDT parity priced manually at 1, at 7% inverse.
The test pins both the arithmetic (1/(1-0.07) = 1.0752688, so 100 Zelle are
93 USDT) and the dependency on manual rates feeding derived ones. Breaking
that dependency does not raise: it freezes the pair at its last value."
```

---

## Task 2b: Permitir el par de paridad (apareció al ejecutar)

El plan daba por hecho que crear `USDT-USDT` era solo configuración. No lo era: el schema lo
rechazaba con *«From and to currencies must be different»*
(`CurrencyPairBase.validate_different_currencies`, heredado por `CurrencyPairCreate`), y el
formulario del panel tenía la misma regla. Ambas se quitaron; el par de paridad no se cotiza
—el resolver corta cuando `from == to`— así que no abre ninguna cotización nueva.

Cubierto por `tests/test_parity_pair_creation.py` (crear la paridad y que un par normal siga
funcionando) y, del lado del front, por `_lib/newPairForm.test.ts`.

---

## Task 3: Desplegar y configurar los pares en producción

No hay código en esta tarea; es la puesta en marcha y su verificación. Se hace **después** de que el arreglo de la Tarea 1 esté en producción, porque el paso 2 depende de él.

**Files:** ninguno.

**Interfaces:**
- Consumes: `set_manual_rate` arreglado (Tarea 1), desplegado en producción.

- [ ] **Step 1: Desplegar el backend**

Solo viaja lo **commiteado**: los dos commits de las Tareas 1 y 2. El resto del working tree de la rama (el trabajo ajeno listado en *Global Constraints*) no sale de la máquina, así que decidir aparte qué hacer con él antes de mezclar a `main`.

El camino es el de siempre: llevar los commits a `origin/main` y dejar correr la Action *Deploy to Production* (~2m30s, incluye `alembic upgrade head`, que aquí no tiene nada que aplicar). Antes de pushear, revisar el disco del EC2 —el build necesita ~2,5 GB libres:

```bash
ssh tasas-ec2 'df -h /'
```

- [ ] **Step 2: Crear el par de paridad `USDT-USDT`**

En `/admin/currency-pairs` → nuevo par:

| campo | valor |
|---|---|
| from / to | USDT / USDT |
| tipo | `BASE` |
| activo | sí |
| Binance tracked | no |
| monitoreado | no |
| descripción | `Paridad 1:1. Existe solo como base de los pares de método de pago (ZELLE-USDT). No se cotiza: quitarle el precio manual congela sus derivados.` |

- [ ] **Step 3: Ponerle precio manual `1`**

Desde la misma pantalla, acción *Precio manual* → `1`. Guardar.

Esto dispara `manual_scrape` solo. Verificar que la tasa quedó:

```bash
curl -s https://api.cambiosloscriollitos.com/rates | \
  python3 -c "import json,sys; print([r for r in json.load(sys.stdin) if r['pair_symbol']=='USDT-USDT'])"
```

Esperado: una entrada con `rate: 1.0`, `is_manual: true`. Si sale vacío, el arreglo de la Tarea 1 no está desplegado.

- [ ] **Step 4: Crear el par `ZELLE-USDT`**

| campo | valor |
|---|---|
| from / to | ZELLE / USDT |
| tipo | `DERIVED` |
| par base | `USDT-USDT` |
| porcentaje | `7` |
| porcentaje inverso | sí |
| activo | sí |

- [ ] **Step 5: Forzar el scrape y verificar la tasa**

```bash
curl -s -X POST https://api.cambiosloscriollitos.com/scrape/manual
sleep 45
curl -s https://api.cambiosloscriollitos.com/rates | \
  python3 -c "import json,sys; print([r for r in json.load(sys.stdin) if r['pair_symbol']=='ZELLE-USDT'])"
```

Esperado: `rate ≈ 1.0752688`, `percentage: 7`, `inverse_percentage: true`.

- [ ] **Step 6: Verificar la cotización de punta a punta**

1. Calculador de `cambiosloscriollitos.com`: 100 ZELLE → **93,00 USDT**.
2. Bot, con un cliente **autorizado** (`!usdt on <teléfono>`): "100 zelle a usdt" → 93 USDT.
3. Bot, con un cliente **no autorizado**: no cotiza y te llega el aviso «USDT no autorizado… usa `!usdt on`». Es el comportamiento correcto, no un fallo.
4. Que no se movió nada más: en `/admin/currency-pairs`, los 26 pares anteriores conservan su tasa (el scrape del paso 5 los recorre todos).

- [ ] **Step 7: Dejar constancia de la vuelta atrás**

No hay nada que commitear, pero anotar dónde queda el interruptor: para revertir, desactivar `ZELLE-USDT` y quitarle el precio manual a `USDT-USDT` (*Desactivar precio manual*). Sin deploy.

---

## Notas de alcance

- **`USDT→ZELLE` no se crea.** Queda técnicamente disponible por inversión y con el margen volteado (1 USDT = 1,0753 Zelle, o sea entregar 107 Zelle por 100 USDT). Decisión explícita del operador: no se ofrece esa dirección. Es la misma trampa que ya existe con `VES→USD`; arreglarla de fondo —no invertir cuando el par directo no existe— es otro trabajo.
- **No hay cambios en el front ni en el bot.** El bot toma el par nuevo solo en su próximo `loadRates()` (lee `GET /rates`). Se verificó que la paridad no ensucia ninguna pantalla: el calculador arma un `Set` de monedas, no de pares.
