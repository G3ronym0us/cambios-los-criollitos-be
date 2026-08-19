# Par ZELLE→USDT gobernado por un porcentaje

**Fecha:** 2026-08-19
**Estado:** diseño aprobado, sin implementar

## Lo que hace falta

Cotizar **ZELLE → USDT**: el cliente paga con Zelle y recibe USDT. La tasa no viene de ningún
mercado, es una política de la casa: **1 Zelle = 0,93 USDT**, o sea un 7% de comisión sobre la
paridad. El operador quiere manejar ese número **como porcentaje** (7%), igual que el resto de
los pares, no como una cifra suelta que hay que recalcular a mano cada vez que cambie.

La cotización tiene que estar disponible en el calculador del panel **y** en el bot, para los
clientes autorizados a USDT.

## Por qué no entra en el modelo actual

Los 26 pares vivos se dividen en tres formas, y ZELLE-USDT no es ninguna:

- **`BASE`**: la tasa la trae el scraper de Binance (`VES-USDT`, `USDT-COP`…) o la empuja un
  script externo (`BRL-USDT`, vía `POST /rates/external`).
- **`DERIVED`**: cuelga de un par base y le aplica un porcentaje. Todos los pares Zelle y PayPal
  de hoy son así: `ZELLE-VES` = `USDT-VES` −8%, `ZELLE-BRL` = `USDT-BRL` −10%.
- **`CROSS`**: fiat contra fiat, puenteado por USDT.

Un porcentaje siempre necesita un par base del que colgarse (`create_currency_pair` rechaza un
`derived_percentage` sin base, y `_calculate_dynamic_derived_rates` busca la tasa del base para
aplicárselo). Para ZELLE-USDT ese base tendría que ser **la paridad**: "1 unidad de dólar = 1
USDT". Y esa paridad, aunque el sistema ya la asume en todas partes, **no está escrita en ningún
lado**. `USD-VES` deriva de `USDT-VES` con 1,5%, o sea que el modelo ya trata al dólar y al USDT
como la misma cosa con un spread; simplemente nunca lo dijo explícitamente.

## La forma elegida

Dos filas de datos. **Cero código nuevo** salvo un test y un filtro cosmético.

### El par de paridad

| campo | valor |
|---|---|
| `pair_symbol` | `USDT-USDT` |
| `pair_type` | `BASE` (obligatorio: `GET /currency-pairs/base-pairs` solo ofrece pares `BASE` activos) |
| `is_active` | `true` |
| `binance_tracked` | `false` |
| `is_monitored` | `false` |
| `description` | «Paridad 1:1. Existe solo como base de los pares de método de pago (ZELLE-USDT). No se cotiza: quitarle el precio manual congela sus derivados.» |
| tasa | **precio manual = 1** (botón *Precio manual* de `/admin/currency-pairs` → `adminService.setManualRate`) |

### El par de cotización

| campo | valor |
|---|---|
| `pair_symbol` | `ZELLE-USDT` |
| `pair_type` | `DERIVED` |
| `base_pair` | `USDT-USDT` |
| `derived_percentage` | `7` |
| `use_inverse_percentage` | `true` |

**El orden importa:** primero la paridad, después su precio manual, y recién entonces el
derivado. `create_currency_pair` rechaza una base que no sea `binance_tracked` ni tenga tasa
manual activa (`currency_pair_repository.py:105`).

### Por qué la paridad es `USDT-USDT` y no `USD-USDT`

Fue la corrección más importante del diseño. `USD-USDT` era lo natural, y está mal:

`GET /rates` es **público** y devuelve **todas** las filas de tasa activas, sin mirar el
`is_active` del par (`rates.py:386`). El calculador arma su lista de monedas recorriendo esas
tasas (`CurrencyCalculator.tsx:118`) y el bot cachea lo que venga (`rates.ts loadRates`).
Desactivar el par **no esconde la tasa**. Con `USD-USDT = 1` publicado, un cliente autorizado
podría pedir "100 dólares a usdt" y el sistema le cotizaría **100 USDT con margen cero** — una
oferta que hoy es imposible porque el par no existe, y que estaríamos abriendo sin querer.

`USDT-USDT = 1` publica una tautología: no hay nada que explotar, porque el resolver corta en
seco cuando `from == to` y devuelve 1 sin consultar la base
(`whatsapp_rate_resolver.py:69`). Nada impide crear un par con la misma moneda a ambos lados:
no hay validación en el modelo, ni en el repositorio, ni en el formulario del front.

Su único costo es cosmético: el calculador listaría un "USDT → USDT". **Entra en el alcance**
taparlo: filtrar `rate.from_currency !== rate.to_currency` en el bucle que arma la lista de
monedas del calculador (`frontend/src/components/CurrencyCalculator.tsx:118`).

### Por qué `use_inverse_percentage = true`

El scraper mezcla las tasas manuales de la DB con las de Binance y luego aplica el porcentaje
(`binance_scraper._calculate_dynamic_derived_rates` → `_merge_rates_with_manual_priority`). Es
el mismo camino por el que `BRL-ZELLE` vive colgado de un `BRL-USDT` que nadie scrapea.

```
base USDT_USDT = 1,0 (manual)
inversa → rate = 1 / (1 − 0,07) = 1,0752688     inverse_percentage = true
```

Y como todos los consumidores hacen `inverse ? monto/rate : monto*rate`
(`whatsapp_rate_resolver.apply_rate`, `applyRateConversion` en el front):

```
100 ZELLE ÷ 1,0752688 = 93,00 USDT
```

Guardar `rate = 0,93` con `inverse = false` da **el mismo resultado**. Se elige la inversa
porque es la convención de la casa: los tres pares `X-USDT` que ya existen (`VES-USDT`,
`COP-USDT`, `BRL-USDT`) son inversos, y el `rate` se lee siempre como "cuánto de la otra moneda
por 1 USDT". El número que ve el cliente es idéntico por ambos caminos.

El bot no necesita cambios: `loadRates()` lee `GET /rates` y cachea el par nuevo solo.

## Puesta en marcha

Todo desde `/admin/currency-pairs`, sin deploy:

1. Crear `USDT-USDT` con los campos de arriba.
2. Ponerle precio manual `1`. Dispara `manual_scrape` automáticamente.
3. Crear `ZELLE-USDT` con los campos de arriba.
4. Verificar. Si hace falta forzar la ronda sin esperar la horaria: `POST /scrape/manual`.

Cambiar el 7% más adelante se hace con `PATCH /currency-pairs/{uuid}/percentage`, que no
revalida la base y dispara el scrape solo — así que funciona aunque la paridad se haya tocado.

## Verificación

1. `GET /rates` trae `ZELLE-USDT` con `rate ≈ 1,0752688`, `percentage = 7`,
   `inverse_percentage = true`.
2. Calculador del panel: 100 ZELLE → **93,00 USDT**.
3. Bot, cliente **autorizado** (`!usdt on <teléfono>`): "100 zelle a usdt" → 93 USDT.
4. Bot, cliente **no autorizado**: no cotiza y avisa al operador («USDT no autorizado… usa
   `!usdt on`»). Es el comportamiento que ya existe (`whatsapp.ts:2128`) y aquí es el correcto.
5. Los 26 pares vivos conservan su tasa: el scrape del paso 2 los recorre todos.

## Test

Un test de integración en `backend/tests/test_parity_derived_rates.py`, contra Postgres real
como el resto de la suite, sobre `_calculate_dynamic_derived_rates`: sembrar la paridad
`USDT-USDT` con tasa manual 1, un `ZELLE-USDT` derivado al 7% inverso, y afirmar que la tasa
resultante es `1,0752688` con `inverse_percentage = true`, y que 100 unidades dan 93.

No prueba aritmética trivial: **fija el mecanismo** de que las tasas manuales alimentan a las
derivadas. Es exactamente lo que un refactor del scraper podría romper dejando el par congelado
en su último valor, sin error visible.

## Riesgos y limitaciones conocidas

- **Quitarle el precio manual a `USDT-USDT` congela `ZELLE-USDT`.** Deja de recalcularse y se
  queda con el último valor; el único rastro es un `❌ No se encontró base rate` en el log del
  scraper. Por eso la descripción del par de paridad es parte del diseño, no un adorno.
- **`USDT→ZELLE` queda disponible por inversión, con el margen volteado.** El cotizador acepta
  el símbolo inverso (`whatsapp_quote_service.py:129`) y el resolver invierte la tasa
  (`whatsapp_rate_resolver.py:143`), así que daría 1 USDT = 1,0753 Zelle: entregar 107 Zelle por
  100 USDT. **Decisión del operador: no se ofrece esa dirección y no se crea el par.** Queda
  anotado como limitación conocida, hermana del `VES→USD` que ya está igual hoy (`USD-VES`
  existe, `VES-USD` no). El arreglo de fondo —no invertir cuando el par directo no existe— es
  otro trabajo.
- **La paridad `USDT-USDT` se publica en `/rates`.** Es inofensiva, pero aparece en el listado
  público de tasas y en el histórico.

## Fuera de alcance

- Crear `USDT-ZELLE`, `PAYPAL-USDT` o `USD-USDT`. Si más adelante hacen falta, cuelgan de la
  misma paridad con su propio porcentaje.
- Convertir la paridad en un concepto del modelo (un `DERIVED` sin base cuya tasa base se asume
  1). Es la alternativa que se evaluó y se descartó por ahora: mismo resultado numérico, pero
  toca la validación del modelo, la consulta de pares derivados, el bucle del scraper, los
  schemas y el formulario del front. Se vuelve rentable cuando haya tres o cuatro pares de
  método de pago contra USDT; migrar entonces es cambiarles la base, no rehacer nada.
- Una fuente de precio real USDT/USD. Mientras el negocio trate 1 USD = 1 USDT, la paridad fija
  es fiel.
