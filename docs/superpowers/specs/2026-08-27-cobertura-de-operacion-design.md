# La cobertura de una operación: varios comprobantes, una tasa que sale de la suma

**Fecha:** 2026-08-27
**Estado:** diseño aprobado, sin implementar
**Pantallas:** artboards `4a`, `4b`, `4c` de `Admin Payments Screens.dc.html`
(proyecto claude.ai/design `CambiosLosCriollitos`)

## El problema

Un trato pagado en varias partes no se puede cuadrar. Todo el panel está anclado en el
**comprobante** —abres un pago y le buscas operación—, pero juntar tres pagos móviles en una
operación es trabajo de la **operación**, no de cada pieza suelta.

### El caso que lo motiva

Operación **3898** (`584148861273`): 350 USD → VES, pagados con **tres** pagos móviles del
24-ago:

```
#4935     6.277 Bs   15:56
#4937   250.000 Bs   16:08
#4938    65.723 Bs   16:32   ← desde éste se creó la operación
        ─────────
        322.000 Bs   = 350 USD a 920
```

El operador armó la op desde 4938 y al intentar engancharle los otros dos **la operación no
aparecía entre las sugeridas**. Dos causas encadenadas:

1. **La operación nació creyéndose pagada entera.** `create_operation_from_payment` hacía
   `_apply_settlement(row, op, from_amount)`: crear la op desde un saliente afirmaba que ese
   pago la cubría toda. 4938 quedó marcado por los 350 (vale ~73 a la tasa de la op), el
   pendiente en cero, y el prorrateo de `expected_amount` —que pide `pending > 0.01`— nunca se
   activó. Los otros dos se compararon contra los 315.000 completos: desvíos del 26% y del 98%,
   muy fuera del `AMOUNT_TOLERANCE` del 1% que exige `pick_suggestion`.
   *Ya corregido* (`FULL_PAYOUT_MIN_RATIO`, 2026-08-27), pero es una curita sobre el síntoma.

2. **La tasa se tecleó mal y no hay dónde arreglarla.** El trato debía cotizarse a **920**; se
   guardó `to_amount = 315.000`, o sea 900. Corregirlo exige subir la tasa por encima de la base
   del momento (917,0), y el único camino de edición es `applied_percentage`, que sólo **resta**
   (`Field(None, ge=0, le=99)`). No hay forma de dejar la operación bien sin borrarla y rehacerla.

### No es un caso aislado

11 operaciones en producción quedaron sobre-saldadas
(`settled_amount > pagado/tasa × 1,15`). Vivas y del mismo patrón: **3898** y **3950**.

## La decisión de fondo

**Manda la suma de los comprobantes.** El operador pone el **valor** del trato (350 USD); el
monto en bolívares **no se teclea nunca** — es lo que suman los comprobantes marcados, y la tasa
sale de dividir uno entre otro. Así el error de hoy deja de ser posible: no hay número tecleado
que pueda salir mal, y no hace falta ningún «recalcular».

**Excepción declarada:** el valor puede ser mayor que la suma porque una parte **no se puede
representar en el sistema** (efectivo en mano, un pago por un canal que el bot no lee, saldo a
favor, o un ajuste a favor del cliente). Eso no es un error a corregir: se declara con un motivo.

### Cuándo se deriva la tasa

«La tasa sale de la suma» es circular mientras la cobertura está a medias: con un solo
comprobante marcado, `65.723 / 350` daría una tasa de **187,78**. El valor de cada comprobante
depende de la tasa y la tasa depende de todos los comprobantes.

Se rompe cerrando el ciclo en un solo punto:

- **Mientras está incompleta**, cada comprobante se valora a la **tasa de referencia del día**
  (o a la que la operación ya tenga, si venía cotizada). Los números del pie son aproximados y
  se rotulan como tales.
- **Al cerrar la cobertura** —completa, o completa con resto declarado— `to_amount` pasa a ser
  la suma, `rate_used = to_amount / valor`, y **todas** las liquidaciones se revalúan a esa tasa
  para que sumen exactamente el valor.

La revaluación ocurre **una sola vez**, al cerrar. Guardar a medias no toca la cotización.

## Alcance

**Entra:**
- Panel de cobertura anclado en la operación, con tres puertas de entrada.
- La tasa de la operación pasa a derivarse de la suma; deja de ser tecleable.
- Declarar el resto no representable, con motivo.
- Sugerir la combinación de comprobantes que suma el valor.

**No entra:**
- El lado ENTRANTE (`whatsapp_payment_allocations`). Mismo problema, otra tanda.
- Enganchar el motivo «saldo a favor» con `whatsapp_balance_entries` ni «efectivo» con
  `delivery_status`. Por ahora el motivo se registra; conectarlo con esa maquinaria es después.
- Corregir las 11 operaciones históricas. Es arrastre de datos, se decide aparte.

## El modelo ya lo soporta

`whatsapp_outgoing_settlements` es una tabla `(outgoing_payment_id, whatsapp_operation_id,
settled_amount, settled_reference_rate)` **sin cardinalidad fija**. Hoy se lee en un solo
sentido —un comprobante repartido entre varias ops, `OutgoingSettlementsPanel`— y lo que falta
es leerla en el otro. No hay tabla nueva.

### Lo único que se agrega

Dos columnas en `whatsapp_operations`, para el resto declarado:

| columna | tipo | qué es |
|---|---|---|
| `uncovered_amount` | `double precision` | Cuánto del valor no tiene comprobante. `NULL` = nada declarado. |
| `uncovered_reason` | `varchar(24)` | `CASH`, `OTHER_CHANNEL`, `BALANCE`, `ADJUSTMENT`. |

Y `pending_amount` pasa a ser `valor − delivered_amount − (uncovered_amount ?? 0)`: declarar el
resto es lo que deja el trato en cero y permite cerrarlo.

## La pantalla

Un panel lateral (`SidePanel`), hermano de `OutgoingSettlementsPanel` y con su misma anatomía:
cabecera con el estado, barra de cobertura por tramos, filas, pie con el resultado.

### Cabecera — artboard `4a`

Par, cliente, estado, **valor del trato**, `cubierto X · falta Y`, y la barra. El tramo sin
cubrir va rayado, igual que el «sin asignar» del panel inverso.

### Cuerpo — la lista de candidatos

Comprobantes SALIENTES del mismo cliente que estén **sin repartir o con saldo libre**, con
casilla. Cada fila: monto, fecha, proveedor, referencia. Los que ya cubren esta operación vienen
marcados y rotulados `ya vinculado`.

**Sugerir combinación** busca el subconjunto cuya suma cuadra con el valor a la tasa de
referencia. Si lo encuentra, marca esas filas; si no, no hace nada y no molesta.

### Pie — artboard `4b`

Tres estados:

| estado | qué muestra | botón |
|---|---|---|
| **Incompleta** | `seleccionado 65.723 VES · cubre ~72,76 de 350,00` (a la tasa de referencia) | habilitado pero sin énfasis — se puede guardar a medias |
| **Completa** | `cubre 350,00 de 350,00` + **tasa 920,00** calculada, con la referencia del día al lado | primario |
| **Resto por fuera** | `cubre 350,00 de 400,00` + `faltan 50,00` + los cuatro motivos | primario, **habilitado sólo con motivo elegido** |

La tasa se pinta **calculada y no editable**, con la referencia del día al lado para que el
desvío se vea de un golpe (920,00 contra 903,25).

### Qué es editable — artboard `4c`

| | |
|---|---|
| ✕ El monto del comprobante | Es lo que dice el comprobante. Si el OCR lo leyó mal se corrige **en el comprobante**, donde queda el rastro (`correction_original`). |
| ✕ La tasa de la operación | Sale de la suma ÷ el valor. Un campo de tasa editable es justo lo que hoy permite equivocarse. |
| ✓ El valor del trato | Lo pactado con el cliente. Ponerlo mayor que la suma es lo que abre «el resto va por fuera». |
| ✓ El reparto de un comprobante | **Sólo** si ese comprobante abarca dos tratos; ahí aparece un campo en su fila. Es el caso que el panel inverso ya resuelve. |

### Las tres puertas

Una sola pantalla invocada desde tres sitios:

1. **Desde la operación** — en `/admin/operations`, un trato con pendiente muestra `falta 350,00`
   y abre el panel. Es la puerta natural del cuadre.
2. **Desde un comprobante** — «vincular a operación» deja de terminar en un vínculo suelto:
   elegida la op, abre el panel con ese comprobante marcado.
3. **Al crear la operación** — se pone el valor y el panel aparece con el comprobante de origen
   marcado. **El campo de bolívares desaparece del formulario.**

## Backend

### Endpoints

```
GET  /operations/{uuid}/coverage
PUT  /operations/{uuid}/coverage
```

`GET` devuelve el estado y los candidatos en una sola llamada (la parte cara es la consulta, no
la aritmética — mismo criterio que `suggest_for_payments`):

```jsonc
{
  "value": 350.0, "value_currency": "USD",
  "delivered": 72.76, "uncovered": null, "pending": 277.24,   // aún a la tasa de referencia
  "reference_rate": 903.245,          // la del día, para contraste
  "settlements": [ { "payment_id": 4938, "settled_amount": 72.76, "rate": 903.245 } ],
  "candidates": [ { "payment_id": 4937, "amount": 250000, "currency": "VES",
                    "created_at": "...", "provider": "pago_movil",
                    "reference": "062363469628", "free_amount": 250000 } ],
  "suggestion": [4935, 4937, 4938]     // el subconjunto que cuadra, o []
}
```

`PUT` recibe el conjunto completo, no deltas (mismo contrato que `set_settlements`):

```jsonc
{
  "value_amount": 350.0,                    // opcional: corrige el valor del trato
  "payments": [ { "payment_id": 4935 }, { "payment_id": 4937 }, { "payment_id": 4938 } ],
  "uncovered": { "amount": 50.0, "reason": "OTHER_CHANNEL" }   // opcional
}
```

Y hace, en una transacción:

1. Fija el valor si vino.
2. Si la cobertura **cierra** (suma + `uncovered` = valor): `to_amount` = suma de los
   comprobantes, `rate_used = to_amount / value`, y revalúa todas las liquidaciones a esa tasa.
   Si no cierra, la cotización no se toca.
3. Reescribe las liquidaciones: cada comprobante aporta su monto completo salvo que traiga
   `settled_amount` explícito (el caso de un comprobante que abarca dos tratos).
4. Guarda `uncovered_amount` / `uncovered_reason`.
5. Recalcula el estado con lo entregado, como ya hace `set_operation_value`.

### Una sola implementación para las dos direcciones

`PUT /operations/{uuid}/coverage` y `PUT /payments/outgoing/{id}/settlements` escriben **las
mismas filas**. Los dos delegan en `_upsert_settlement` + `_sync_settlement_totals`, que ya
existen, para que repartir desde un lado y desde el otro no puedan dar números distintos. Es el
mismo criterio con el que hoy conviven `readReceiptPaymentBlock` (bot) y `resolve_fund_channel`
(backend): la regla vive en un sitio y se lee desde donde haga falta.

### Qué pasa con `FULL_PAYOUT_MIN_RATIO`

El guardarraíl de hoy (un comprobante cubre la op entera sólo si es ≥90% de `to_amount`) deja de
hacer falta en el camino nuevo, porque `to_amount` ya no se teclea. Se **conserva** para el
camino viejo: `create_operation_from_payment` sigue existiendo y el bot lo usa.

## Riesgos

- **Un comprobante mal leído ensucia la tasa de la operación.** Es la contracara de que mande la
  suma. Lo mitiga que la tasa se pinte junto a la referencia del día: un desvío grande se ve.
  Si el OCR se equivocó, se corrige el comprobante y la tasa se recalcula sola.
- **El campo de bolívares desaparece del formulario de crear.** Es el cambio más visible para el
  operador y hay que avisarlo.

## Pruebas

**Backend**
- Op de 350 con tres comprobantes de 6.277 / 250.000 / 65.723 → `to_amount` 322.000,
  `rate_used` 920, `pending` 0. Es el caso 3898 al centavo.
- Marcar sólo uno → la cotización NO se toca, el comprobante se valora a la tasa de referencia
  y la op sigue abierta.
- Marcar los tres de a uno, en cualquier orden → el resultado final es el mismo: 322.000 y 920.
- Valor 400 con los mismos tres → `pending` 50 hasta declarar `uncovered`; con
  `uncovered = {50, OTHER_CHANNEL}` → `pending` 0.
- `uncovered` sin `reason` → 400.
- Un comprobante ya repartido con otra op aporta sólo su saldo libre.
- La sugerencia encuentra `[4935, 4937, 4938]` y devuelve `[]` cuando ningún subconjunto cuadra.
- Escribir por `PUT /operations/.../coverage` y leer por `GET /payments/outgoing/{id}/settlements`
  da lo mismo, y al revés.

**Front**
- Los tres estados del pie con sus botones (el de «resto» deshabilitado sin motivo).
- La tasa se pinta calculada y no hay input para ella.
