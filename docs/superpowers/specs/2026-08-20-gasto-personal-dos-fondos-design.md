# El gasto personal mueve los fondos que toca

**Fecha:** 2026-08-20
**Estado:** diseño aprobado, sin implementar

## El problema

Marcar un comprobante saliente como **gasto personal** hoy solo pone una bandera:
`set_personal_expense` escribe `is_personal_expense`, guarda una descripción y desvincula la
operación. No genera ningún `FundMovement`, así que la plata sale de la caja real pero el libro
de fondos no se entera. El tipo `FundMovementType.PERSONAL` existe desde el principio ("gasto
personal del gestor con fondos del fondo, queda como deuda") y solo se puede crear a mano desde
`/admin/funds` → *Registrar movimiento*, en una pantalla distinta y sin ningún vínculo con el
comprobante que lo originó.

El resultado es que el operador tiene que anotar lo mismo dos veces en dos sitios, y nada obliga
a que concuerden.

### El caso que lo motiva

Comprobante saliente **#4581**: 36.000 COP por Nequi al 3503601080, el 15-ago-2026. Es un gasto
personal de **Jean**, que necesitaba que le enviaran esos pesos a Colombia.

Quién está en cada fondo importa:

- **Cambios Colombia** (COP): Dionis y Diohandres.
- **Zelle/Paypal** (USD): Diohandres y Jean.
- Diohandres es el operador, y el único que está en los dos: es el puente.

Los pesos los puso el fondo de Colombia, pero el gasto es de Jean, cuya cuenta vive en Zelle.
Por eso el asiento tiene dos patas y se compensan:

```
Cambios Colombia   +36.000,00 COP   a nombre de Diohandres   (el fondo entregó y queda repuesto)
Zelle/Paypal          -11,50 USD    a nombre de Jean         (Jean queda debiendo)
```

Ninguna de las dos toca la ganancia acumulada.

Así está anotado en el Excel del negocio (archivo *Cambios* de Drive). En la hoja
`Colombia2026`, fila 1254: `Personales | Diohandres | 36.000 | sin porcentaje | sin ganancia`,
sumando **positivo** al acumulado del fondo (−12.363.781,85 → −12.327.781,85). La hoja `Zelle`
lleva las salidas personales con el mismo criterio: monto en *Salida*, `Ganancia = 0`, la
columna *Acumulada* no se mueve y *Fondos* baja (206 filas a nombre de Jean, 30 de Diodimar, 12
de Mariana).

Conviene no confundirlo con el otro tipo del Excel: las filas `Gastos` (Kraken, papelería,
comisión Zelle — 49 filas) sí castigan la ganancia con un monto negativo. Eso **no** es un gasto
personal y queda fuera de este diseño.

### Los gastos personales que ya existen

Los 5 que hay en producción son en **VES** (Arianna, Café, Gasolina, Cerradura Abuela) y no hay
fondo VES. Esos son de una sola pata, o de ninguna, y se quedan exactamente como están.

## Alcance

**Dentro:**

1. Al marcar un comprobante como gasto personal se pueden declarar hasta **dos patas** de fondo,
   cada una opcional e independiente: la que acredita y la que carga.
2. La pata que carga va en otra moneda: el sistema propone el equivalente con la tasa del día y
   el operador lo puede corregir.
3. Los movimientos quedan colgados del comprobante, y corregir o desmarcar el gasto los reversa.
4. Un tipo de movimiento nuevo, `PERSONAL_IN`, para la pata que suma.

**Fuera:**

- Las filas `Gastos` del Excel (gasto del negocio que sí castiga la ganancia). Es otro concepto y
  no existe en el sistema.
- Crear un fondo VES. Los gastos en bolívares siguen sin patas.
- Movimientos retroactivos para los gastos en VES que ya están marcados.
- Traspasos entre fondos que no nazcan de un gasto personal.

## Diseño

### Modelo de datos

**Tipo nuevo `FundMovementType.PERSONAL_IN`** — "Personal · entra". El tipo es lo que determina
el signo en este libro (`position = DEPOSIT + EXCHANGE_IN − EXCHANGE − PERSONAL`), y la pata que
acredita a Colombia necesita sumar. Se descartó reusar `EXCHANGE_IN` porque ese tipo significa
"un cliente nos pagó" y aquí no hay cliente: el libro diría algo falso.

`PERSONAL_IN` cuenta como entrada, junto a `DEPOSIT` y `EXCHANGE_IN`, en los cuatro sitios que
agregan en `FundRepository`:

- `get_user_position` — el `in_([DEPOSIT, EXCHANGE_IN])` de los depósitos.
- `get_group_balance` — misma lista.
- `get_running_totals` — el saldo corrido del historial.
- las stats de movimientos (`deposits` / `deposits_count` / `net_usdt`).

`fund_movements.movement_type` es `varchar` con `CaseInsensitiveEnum`, no un enum nativo de
Postgres, así que agregar el valor no necesita `ALTER TYPE`.

La pata que carga usa **`PERSONAL`**, que ya existe y ya significa lo correcto: baja la posición
del gestor ("el gestor le debe al fondo") y no toca la ganancia acumulada, que sale de
`TransactionProfitSplit` por otro camino.

**Vínculo con el comprobante: `fund_movements.outgoing_payment_id`**, FK a
`whatsapp_outgoing_payments` con `ON DELETE SET NULL` e índice. Es el espejo exacto de
`incoming_payment_id`, que ya existe para los depósitos nacidos de un comprobante entrante. Es lo
que permite encontrar las patas de un gasto para pintarlas, corregirlas o reversarlas.

Las dos patas guardan `amount_usdt` y `usdt_rate` con `valuation.equivalents`, para que los
totales en USDT de `/admin/funds` sigan cuadrando.

Migración Alembic: una revisión que agrega la columna y su índice. El tipo nuevo no toca la base.

### Servicio

`WhatsAppPaymentService.set_personal_expense` recibe dos patas opcionales y, dentro de la misma
transacción de base de datos:

1. Marca el comprobante como hoy (bandera, descripción, desvincular, decisión de huérfana).
2. Reversa las patas anteriores que siga habiendo vivas (ver *Corregir y deshacer*).
3. Crea la pata que acredita: `PERSONAL_IN`, fondo y gestor elegidos, monto y moneda **del
   comprobante**, `outgoing_payment_id` apuntando al pago, `notes` con la descripción del gasto.
4. Crea la pata que carga: `PERSONAL`, fondo y gestor elegidos, monto en la moneda del fondo.

No usa `FundRepository.create_movement`, que hace `commit()` propio: sigue el mismo criterio que
`_sync_fund_legs`, que construye los `FundMovement` a mano porque corre dentro de una operación
de servicio que todavía no terminó.

**Todo o nada.** Si una pata falla —fondo inactivo, gestor que no es miembro de ese fondo, monto
≤ 0— se deshace la operación completa y el comprobante **no** queda marcado. Nunca un gasto
marcado a medias con un solo movimiento.

### API

```
PATCH /payments/outgoing/{id}/personal-expense
{
  "is_personal_expense": true,
  "personal_description": "Jean",
  "credit_leg": { "fund_group_uuid": "…", "user_uuid": "…" },
  "debit_leg":  { "fund_group_uuid": "…", "user_uuid": "…", "amount": 11.5 }
}
```

Las dos patas son opcionales: sin ninguna, el endpoint se comporta igual que hoy. La que acredita
no lleva monto — siempre es el del comprobante, en su moneda. Con
`is_personal_expense: false` las patas del payload se ignoran y se reversan las que existan.

```
GET /payments/outgoing/{id}/fund-leg-preview?group_uuid=…
→ { "amount": 11.5, "currency": "USD", "rate": 3130.4, "valuation_at": "…", "warnings": [] }
```

Devuelve el equivalente propuesto para la pata que carga y la tasa con que se calculó. Si no hay
tasa histórica para esa fecha, responde 200 con `amount: null` y el motivo en `warnings`: el
formulario avisa y deja escribir el monto a mano, no bloquea.

La respuesta del pago incluye sus patas (`fund_legs`: tipo, fondo, gestor, monto, moneda,
reversada o no), así que el drawer las pinta sin pedir nada extra.

Autorización: la misma que ya exige el endpoint (JWT de operador).

### Corregir y deshacer

Los movimientos no se borran nunca, se reversan con otro que los anula (`reverses_movement_id`),
que es la regla que ya sigue el libro.

- **Desmarcar el gasto personal** → reversa las dos patas.
- **Corregir fondo, gestor o monto** → reversa las viejas y crea nuevas. El historial conserva lo
  que pasó.
- **Una pata ya reversada a mano** desde `/admin/funds` no se vuelve a tocar: la operación es
  idempotente.
- **Borrar el comprobante** reversa sus patas antes de soltarlo.

### Panel

En el drawer del comprobante (`OutgoingPaymentActionDialog`, paso `personal`), debajo de la
descripción aparece un bloque opcional **"Movimiento de fondos"** con las dos patas:

- **Pata que acredita.** Fondo precargado por la moneda del comprobante —COP → Cambios
  Colombia, vía `get_active_group_by_currency`, que devuelve `None` si hay más de un fondo en esa
  moneda— y gestor precargado con el operador firmado si es miembro de ese fondo. El monto es el
  del comprobante y no se edita.
- **Pata que carga.** Fondo y gestor sin precargar. Al elegir fondo se pide el preview y se
  muestra "36.000 COP ≈ $11,50 · tasa 3.130,40"; el monto es editable.

Si el comprobante ya está marcado, el bloque muestra sus patas ("Colombia +36.000 COP ·
Diohandres" / "Zelle −$11,50 · Jean") y permite corregirlas sin desmarcar el gasto.

Validaciones en el formulario: eligiendo fondo, el gestor es obligatorio y debe ser miembro de
ese fondo; monto > 0. Las mismas se revalidan en el backend.

En `/admin/funds`, `MovementsList` muestra las filas nuevas con su etiqueta ("Personal · entra"),
las `notes` con la descripción del gasto y un enlace al comprobante que las originó.
`movementMeta.ts` necesita etiqueta, tono e icono para `PERSONAL_IN`; `RegisterMovementDialog`
**no** lo ofrece entre los tipos manuales, igual que no ofrece `DEPOSIT`: esta pata solo nace de
un gasto personal, para que siempre tenga comprobante detrás.

Todo mobile-first y con tokens semánticos, como el resto del admin.

## Pruebas

En `backend/tests`, junto a `test_payments_attention.py`:

- Marcar con las dos patas crea dos movimientos y deja las posiciones en Jean −11,50 USD y
  Diohandres +36.000 COP.
- Marcar sin patas no crea ningún movimiento (el caso VES de hoy).
- Desmarcar reversa las dos patas.
- Corregir el monto de la pata que carga reversa la vieja y crea una nueva; la posición refleja
  solo la nueva.
- Gestor que no es miembro del fondo → 400, y el comprobante queda **sin** marcar.
- `PERSONAL_IN` suma como entrada en `get_user_position` y en `get_group_balance`.

En el front, `npm run lint` y `npm run build`, que es lo que hay configurado.

## Datos existentes

No hay que migrar nada. Los 5 gastos en VES se quedan como están, y el **#4581**, marcado como
personal el 2026-08-20 sin patas, recibe las suyas desde el panel una vez desplegado: es la
prueba real contra la fila 1254 del Excel.
