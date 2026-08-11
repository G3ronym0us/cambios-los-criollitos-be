# La operación mueve los dos fondos que toca

**Fecha:** 2026-08-11
**Estado:** diseño aprobado, sin implementar

## El problema

Una operación cruza dos monedas y, cuando las dos tienen fondo, mueve la caja de los dos. Hoy
la operación se liga a **un** solo `FundGroup` (`whatsapp_operations.fund_group_id`) y genera
**un** solo `FundMovement` de tipo `EXCHANGE`, que además **resta** del balance.

### El caso que lo motiva

Operación **3251** (Helimer, `559293100680`): el cliente entregó **100 ZELLE** y recibió
**465,75 BRL** por PIX (comprobante saliente `#4378`, ya vinculado). Quedó `COMPLETED`, con
`fund_group_id = 1` (Zelle/Paypal) y un único movimiento:

```
mov 6198 | fondo 1 (Zelle/Paypal) | EXCHANGE | 100 USD | -100 USDT en el balance
```

Falta la pata de Brasil, y la que hay tiene el signo al revés: en esta operación el fondo
Zelle **recibió** 100 USD del cliente y el fondo Brasil **pagó** 465,75 BRL. Lo que tiene que
quedar es:

```
fondo Zelle/Paypal    +100,00 USD    (recibió el Zelle del cliente)
fondo Cambios Brasil  -465,75 BRL    (pagó el PIX al cliente)
```

### No es un caso aislado: el signo está mal en todas

Los movimientos que hay en producción se anotan **siempre** en el fondo cuya moneda coincide
con lo que el cliente *entrega*, y **siempre restando**:

| op | el cliente entrega | movimiento hoy |
|---|---|---|
| 3251 | 100 ZELLE | fondo Zelle **−100 USD** |
| 3090 | 98,82 BRL | fondo Brasil **−98,82 BRL** |
| 3086 | 100.000 COP | fondo Colombia **−31,55 USDT** |

En las tres el cliente entregó ese dinero: el fondo lo recibió. **Decisión del usuario: el
criterio es de caja** — el fondo que recibe sube, el que paga baja — y aplica a todas las
operaciones, no solo a las de dos fondos.

## Alcance

**Dentro:**

1. Una operación declara hasta dos fondos, uno por pata, y genera hasta dos movimientos con
   su signo de caja.
2. Los fondos de cada pata se resuelven solos por la moneda, y se pueden corregir a mano.
3. Corrección del arrastre histórico: 31 movimientos existentes.
4. La ganancia por defecto pasa al fondo que pagó.

**Fuera:**

- **Generar movimientos retroactivos para el histórico sin fondo.** Hay cientos de
  operaciones completadas que nunca declararon fondo; quedan como están. Si se quieren
  incorporar es otra corrida y hay que decidir desde qué fecha. *(Pendiente del usuario.)*
- Partir una pata entre varios comprobantes. La pata refleja el trato, no cada pago parcial.
- El hueco preexistente de que una operación completada por otros caminos (vincular un
  saliente a una op que nació sin fondo) no genera movimiento. Con la resolución automática
  esas ops sí pasarán a generarlos de ahora en más, pero no se toca el histórico.

## Modelo de datos

### Columna nueva

```
whatsapp_operations.fund_group_out_id → fund_groups.id
  nullable, index, ON DELETE SET NULL
```

`fund_group_id` **conserva su significado actual**: el fondo de la pata que **entra**, la
moneda que el cliente entrega. Hoy eso es cierto de hecho —`_fund_movement_figures`
(`app/services/whatsapp_payment_service.py:1379`) trabaja siempre sobre el valor de la
operación, que es lo que el cliente da— pero no está escrito en ninguna parte; el modelo pasa
a decirlo. No se renombra a `fund_group_in_id`: el nombre viaja por schemas, front y bot, y el
cambio no compra nada.

### Tipo de movimiento nuevo

```python
class FundMovementType(enum.Enum):
    DEPOSIT     = "DEPOSIT"
    EXCHANGE    = "EXCHANGE"     # la pata que SALE del fondo (resta)
    EXCHANGE_IN = "EXCHANGE_IN"  # la pata que ENTRA al fondo (suma)   ← nuevo
    PERSONAL    = "PERSONAL"
    ADJUSTMENT  = "ADJUSTMENT"
```

`EXCHANGE_IN` **cuenta como entrada en todos lados donde `DEPOSIT` cuenta como entrada**. La
columna ya es `varchar` con `CaseInsensitiveEnum`, así que el valor nuevo no necesita
migración de tipo. Tres cálculos en `app/repositories/fund_repository.py` lo tienen que
contemplar, y son tres cambios distintos:

| cálculo | dónde | qué cambia |
|---|---|---|
| saldo corriente del extracto | `signed_amount`, línea ~304 | un caso más: `EXCHANGE_IN` suma |
| posición del gestor | `get_user_position`, línea ~474 | entra al lado de `DEPOSIT` en `total_deposited` |
| balance del grupo | `get_group_balance`, línea ~530 | ídem |

**Decisión que conviene confirmar contra el Excel:** `position` se lee como "el fondo le debe
al gestor" (positivo). Tratar `EXCHANGE_IN` como una entrada significa que la pata que entra
**aumenta** lo que el fondo le debe al gestor por cuya mano entró, igual que un depósito. Es
la lectura coherente con el criterio de caja aprobado, pero `position` es el número que se
concilia contra la hoja de Excel — vale la pena mirar una hoja real antes de dar el arrastre
por bueno. No cambia nada del diseño: es un chequeo.

Se descartó la alternativa de guardar `amount_usdt` **con signo** en vez de agregar un tipo:
volvería negativos importes que hoy son positivos en todos los acumulados del panel
(`exchanges_usdt`, `total_outflow`) y obligaría a revisar cada consumidor.

## Resolución del fondo de cada pata

Para cada pata: `settlement_currency(moneda)` —que ya mapea `ZELLE`/`PAYPAL` → `USD`
(`app/services/valuation.py:22`)— y se busca el `FundGroup` **activo** con esa `currency`.

- Exactamente uno → es el fondo de esa pata.
- Cero o más de uno → la pata **queda sin fondo y sin movimiento**. El sistema no adivina
  entre dos fondos de la misma moneda.

Hoy los tres fondos tienen monedas distintas (USD, BRL, COP), así que en la práctica siempre
resuelve. La pata en bolívares nunca resuelve, porque no hay fondo VES: **eso es lo esperado**
—un fondo solo anota lo que tocó su dinero— y no se registra.

**Override manual**: por el mismo camino que hoy asigna el fondo, `_apply_scenario`
(`app/services/whatsapp_quote_service.py:840`) detrás de `PATCH /operations/{uuid}/scenario`,
que gana un `fund_group_out_uuid` y su `clear_fund_group_out` hermanos de los que ya existen.
En el front son dos `<select>` en el drawer de `/admin/operaciones` en vez de uno. Elegir "sin
fondo" en una pata borra su movimiento.

## Generación de los movimientos

### Un solo lugar

Un helper `_sync_fund_legs(op, actor)` en `WhatsAppPaymentService` deja el libro igual a lo
que dice la operación: hasta dos movimientos, creándolos, actualizándolos o borrándolos según
los fondos que la op tenga. Reemplaza a:

- `_sync_fund_movement` (`whatsapp_payment_service.py:1411`), que hoy solo reajusta el
  movimiento existente cuando cambia el valor.
- La creación suelta dentro de `create_operation_from_payment`
  (`whatsapp_payment_service.py:1886`).

Son dos caminos que pueden divergir; pasan a ser uno. Se llama desde donde hoy se toca el
libro: al crear la operación desde un comprobante, al cambiar el valor (`update_value`), y
desde `_apply_scenario` al editar los fondos de la operación.

`POST /transactions` (`app/routers/transaction.py:220`) crea su propio `EXCHANGE` para las
transacciones cargadas a mano en el panel, con `from_amount`/`from_currency` — o sea, también
la pata entrante. Pasa a crear `EXCHANGE_IN`. No usa el helper: no tiene una
`WhatsAppOperation` detrás.

### Cada pata va en su propia moneda

Esto **simplifica** lo que hay. `_fund_movement_figures` existe para convertir el valor de la
operación a la moneda del fondo (el caso de un fondo BRL atendiendo un trato en USDT). Con un
fondo por pata esa conversión desaparece: por construcción la moneda de la pata *es* la del
fondo.

```
pata que entra:  from_amount  en  settlement_currency(from_currency)
pata que sale:   to_amount    en  settlement_currency(to_currency)
```

Solo queda el `amount_usdt` de cada pata para el balance, que sale de `valuation.equivalents`
a la fecha de la operación —lo mismo que ya se calcula para `op.amount_usdt`—. Se va una
función y con ella su modo de fallo `fund_rate_unavailable`, que hoy puede impedir crear la
operación entera cuando no hay tasa para valorar.

### Gestor del movimiento

`create_operation_from_payment` hoy exige que le pasen el gestor y corta con
`exchange_user_required` si falta. Las patas automáticas no pueden depender de eso: usan quien
ejecuta la acción (`actor`), y si no hay, el `received_by_user_id` de la operación. Si tampoco
hay, la pata no se registra — pero nunca tumba la operación.

## Ganancia

`ProfitAllocationService.ensure_defaults` (`app/services/profit_allocation_service.py:58`) hoy
reparte "todo al fondo que la atendió", leyendo `op.fund_group_id`. Pasa a leer:

```
fund_group_out_id  si tiene fondo   (el que puso la plata)
fund_group_id      si no
```

De las 31 operaciones con movimiento, **27 no tienen fondo saliente** —23 pagan en bolívares y
4 en USDT, y ninguna de las dos monedas tiene fondo—, así que se comportan **exactamente como
hoy**. El reparto sigue editable por operación con `set_allocations`, y esa edición manda
sobre el default (`ensure_defaults` no pisa un reparto existente).

## Arrastre histórico

`fund_movements` tiene **31 filas en producción, todas nacidas de una operación** (no hay
depósitos, personales ni ajustes sueltos). La migración de Alembic es **solo aditiva** —la
columna y nada más—; el arrastre va en un CLI con `--dry-run`, `app/cli/backfill_fund_legs.py`,
porque hay tres tratos distintos y uno necesita ojo.

**1. Treinta movimientos cambian de tipo.** `EXCHANGE` → `EXCHANGE_IN`. Mismo fondo, mismo
monto, misma fecha; solo cambia el signo con que pesan en el balance.

**2. El movimiento `6172` se rehace, no se convierte.** Son 106,85 BRL en Cambios Brasil que
en realidad son los **21 USD** que entregó el cliente en la op 2604 (Papa de Neurys),
convertidos por `_fund_movement_figures` porque el fondo lleva otra moneda que el valor. Bajo
el diseño nuevo esa operación se parte distinto: 21 USD entran a Zelle/Paypal y 101,50 BRL
salen de Cambios Brasil. Le cambia el fondo *y* el monto.

**3. Se crean las patas salientes que faltan.** Las operaciones con movimiento cuya moneda
pagada resuelve a un fondo: 3 en BRL y 1 en COP. Ahí entra el caso que motiva todo esto,
**−465,75 BRL en Cambios Brasil para la op 3251**. Las 23 que pagan en VES y las 4 en USDT no
generan nada, porque no hay fondo de esas monedas.

El CLI imprime cada fila con su tratamiento antes de escribir, igual que se hizo con el
barrido de comprobantes del 2026-08-11.

## Pruebas

Contra Postgres real, como el resto de `backend/tests`:

- Una operación entre dos monedas con fondo genera **dos** movimientos, con sus signos.
- La pata cuya moneda no tiene fondo no deja movimiento (el caso VES, que es la mayoría).
- Dos fondos activos en la misma moneda → esa pata no auto-resuelve.
- Editar el fondo de una pata actualiza su movimiento; quitarlo lo borra.
- Cambiar el valor de la operación reajusta las dos patas.
- El saldo del extracto suma `EXCHANGE_IN` y resta `EXCHANGE`; `position` y el balance del
  grupo lo cuentan como entrada.
- `ensure_defaults` manda la ganancia al fondo saliente, y cae al entrante cuando no hay.
- El backfill sobre un fixture que reproduce los tres casos: los 30 de tipo, el `6172` que se
  rehace, y la pata saliente que falta.
