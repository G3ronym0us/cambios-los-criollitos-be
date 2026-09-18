# Fondo por defecto del par

**Fecha:** 2026-09-13
**Estado:** aprobado el 2026-09-15 e implementado en la rama `feat/fondo-por-defecto-del-par`
(backend) y en la rama del mismo nombre del front. Falta correr la suite del backend.

**Desviación al implementar.** La tabla de abajo cubre 9 pares y la lista de «revisar tú» otros
7, pero en producción hay 29: quedaban 13 sin mencionar (BRL-ZELLE, COP-ZELLE, BRL-PAYPAL,
COP-PAYPAL, BRL-COP, COP-BRL, BRL-USD, USDT-COP, PAYPAL-COP, VES-COP, VES-PAYPAL, VES-USDT,
VES-ZELLE) que se habrían quedado sin fondo, y eso contradice «que nada cambie al desplegar».
Así que la migración aplica a TODOS los pares la regla por moneda que rige hoy —el único fondo
activo de la moneda de cada pata, con ZELLE y PAYPAL liquidando como USD— y las excepciones
son las de la tabla: USD-VES y los 7 a revisar quedan sin fondo, y ZELLE-BRL suma su 7/3. Sobre
los pares que la tabla sí nombra, esa regla da exactamente lo que la tabla dice.

## El problema

Una operación nace con sus fondos resueltos **por moneda** (`_resolve_fund_legs_for_new_op`,
`app/services/whatsapp_payment_service.py:2420`): cada pata busca el único fondo activo de su
moneda. Acierta en los pares simples y falla donde la moneda no dice nada del negocio
(últimos 60 días en producción):

| par | qué pasa hoy | qué debería pasar |
|---|---|---|
| USD-VES | 664 ops en `Zelle/Paypal`, sólo porque es el fondo USD | ningún fondo: es efectivo |
| ZELLE-BRL | 41 ops repartidas en 5 combinaciones de fondos | entra Zelle, sale Brasil |
| ZELLE-BRL | 100% del margen a un solo fondo | 7% Zelle + 3% Brasil |

El reparto 7/3 **nunca se ha hecho**: `ProfitAllocationService.ensure_defaults`
(`app/services/profit_allocation_service.py:58`) manda todo el margen a un único fondo y
nunca reparte más de lo cobrado.

## Reglas acordadas

1. **El par es la única fuente del fondo por defecto.** Un par sin fondo configurado da una
   operación sin fondo — es normal, ya hay muchas así. Se quita la regla por moneda.
2. El par declara fondo de **entrada** y, cuando el trato toca dos fondos, de **salida**.
3. **El fondo del comprobante gana al del par.** Orden por pata:
   elegido a mano > heredado del comprobante > defecto del par > sin fondo.
4. Se aplica **en el backend al nacer cualquier operación** (cotización del bot y creación
   manual); el formulario manual lo muestra preseleccionado.
5. **USD-VES no va a ningún fondo** por ahora.
6. **Los porcentajes del par son la meta y se aplican tal cual**, aunque la operación se haya
   cobrado distinto: con 7/3 y un cobro al 8%, Zelle registra 7 y Brasil 3, y los 2 puntos de
   diferencia corren por cuenta del operador. En cada operación se pueden **editar a mano**
   (ya existe) o **ajustar a lo cobrado** (nuevo: 5,6 / 2,4).

## Modelo de datos

Cuatro columnas nuevas en `currency_pairs`, todas nullable:

```
default_fund_in_id           → fund_groups.id  ON DELETE SET NULL
default_fund_in_profit_pct   float   -- puntos del margen para el fondo de entrada
default_fund_out_id          → fund_groups.id  ON DELETE SET NULL
default_fund_out_profit_pct  float   -- puntos del margen para el fondo de salida
```

Un porcentaje sin su fondo no tiene sentido: el schema lo rechaza (400).

Migración encadenada a `e1f2a3b4c5d6`, la cabeza real (coincide con producción).

### Configuración inicial (en la misma migración)

Como se quita la regla por moneda, cada par recibe el fondo que usa hoy, para que nada cambie
al desplegar. Se busca por `pair_symbol` y nombre de fondo; si alguno no existe, se salta.

| par | entrada | salida | % entrada / salida |
|---|---|---|---|
| ZELLE-VES, PAYPAL-VES, ZELLE-USDT | Zelle/Paypal | — | — |
| COP-VES, COP-USDT | Cambios Colombia | — | — |
| BRL-VES, BRL-USDT | Cambios Brasil | — | — |
| ZELLE-BRL | Zelle/Paypal | Cambios Brasil | 7 / 3 |
| USD-VES | — | — | — |

Sin porcentaje configurado, el reparto se comporta como hoy (ver abajo), así que los pares
simples no cambian su ganancia.

**Los 7 que quedaban por revisar ya están decididos** (con el operador, 18-09-2026). Se
configuran en la migración porque un par sin fondo pierde además la clasificación automática:
`_resolve_scenario_for_new_op` saca el socio del fondo, así que sin fondo la operación deja de
nacer VIA_PARTNER o ZELLE_DIRECT y cae en NORMAL.

| par | entrada | salida | por qué |
|---|---|---|---|
| USDT-VES | Zelle/Paypal | — | 9 de sus 10 operaciones fueron así |
| ZELLE-COP | Zelle/Paypal | Cambios Colombia | los pesos salen de Colombia; nunca se registró |
| PAYPAL-BRL | Zelle/Paypal | Cambios Brasil | los reales salen de Brasil; nunca se registró |
| USDT-BRL | — | Cambios Brasil | USDT no tiene fondo propio |
| VES-BRL | — | Cambios Brasil | los bolívares son efectivo |
| USD-BRL | — | Cambios Brasil | los dólares entran en físico, como en USD-VES |
| USDT-USDT | — | — | paridad 1:1 para colgar porcentajes; no mueve plata |

## Resolución del fondo de cada pata

`_resolve_fund_legs_for_new_op` sigue corriendo UNA vez al nacer la operación y sigue sin
pisar lo que el caller ya puso. Para lo que está en NULL toma `pair.default_fund_in_id` /
`pair.default_fund_out_id`, y si el par no lo tiene, deja la pata sin fondo.
`get_active_group_by_currency` deja de usarse aquí (sigue para otros consumidores, si los hay).

Un fondo por defecto **inactivo** no se asigna: la pata queda sin fondo.

## Reparto de la ganancia

`ensure_defaults` sigue sin pisar un reparto existente. Cambia el caso por defecto:

- **El par declara algún porcentaje** → una fila `FUND` por cada pata que tenga fondo **y**
  porcentaje en el par, con ese porcentaje exacto, **aunque la suma pase lo cobrado**. La
  fila va al fondo que la pata tiene de verdad (si el comprobante cambió la entrada, el 7% va
  a ese fondo). Nota en la fila: «Reparto por defecto del par».
- **El par no declara porcentajes** → exactamente como hoy: todo al fondo que pagó (o al que
  recibió), por `default_profit_percentage` del fondo, sin pasar lo cobrado.

### Ajustar a lo cobrado

`POST /operations/{uuid}/profit-allocations/scale-to-charged` (moderador): reescala cada fila
en proporción para que la suma dé `applied_percentage`, guarda con `set_allocations` —queda
firmado por quien lo hizo— y resincroniza la transacción. Con reparto vacío o cobro 0 → 400.

## Front

- **Detalle del par** (`/admin/currency-pairs/[uuid]`): sección «Fondos» junto a
  `StatusSection`: fondo + % de entrada; «Agregar fondo de salida» despliega fondo + % de
  salida. Muestra la suma («10% en total»).
- **Crear operación** (`CreateOperationForm.tsx`): si el comprobante no trae fondo, el campo
  arranca en el fondo de entrada del par; `splitFundOptions` lo sube primero en sugeridos.
- **Reparto de la ganancia** (`ProfitAllocationCard.tsx`): cuando lo repartido difiere de lo
  cobrado, botón «Ajustar a lo cobrado» junto al aviso existente.

## Fuera de alcance

- Rehacer fondos o repartos de operaciones que ya existen (las 664 USD-VES en `Zelle/Paypal`
  quedan como están). Si se quiere, CLI aparte con `--dry-run`.
- Gasto personal y préstamos, que resuelven su fondo por otro camino.

## Pruebas (contra Postgres real)

- Par con fondo de entrada → la op nace con ese fondo, desde el bot y desde el panel.
- Par sin fondo → la op nace sin fondo aunque exista un fondo de esa moneda (USD-VES).
- Comprobante con fondo → gana al del par.
- Fondo por defecto inactivo → pata sin fondo.
- ZELLE-BRL 7/3 cobrado al 8% → dos filas, 7 y 3; `unallocated_percentage` = −2.
- Ajustar a lo cobrado → 5,6 y 2,4, firmado por el actor.
- Par sin porcentajes → `ensure_defaults` idéntico al actual.
- Schema: porcentaje sin fondo → 400; editar otra cosa no borra la configuración.
- Migración: la tabla de configuración inicial queda como se describe.
