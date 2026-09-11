# Deuda: la bandeja de pagos tiene dos andamios para una sola pantalla

**Fecha:** 2026-08-28
**Estado:** deuda registrada, sin plan de ejecución
**Decisión del usuario:** aparcada a propósito. Se anota para no volver a descubrirla.

## Qué pasa

Un comprobante se abre en **dos componentes distintos según su lado**, con el mismo esqueleto
duplicado:

| | entrante | saliente |
|---|---|---|
| componente | `IncomingPaymentDrawer` (578 líneas) | `OutgoingPaymentActionDialog` (988 líneas) |
| contenedor | `SidePanel` (cajón) | `Dialog` (centrado) |
| entrada | abre en el detalle, con botones sueltos | abre en una **rejilla** de tarjetas |

Los dos declaran por su cuenta `type Step`, `STEP_META`, `[step, setStep]`,
`[stepHeader, setStepHeader]` y `[suggestion, setSuggestion]`. Es el mismo andamio escrito dos
veces.

## Por qué existe

Sedimento, no diseño:

```
2026-06-13   nace OutgoingPaymentActionDialog     (flujo classify-before-link)
2026-08-03   nace SidePanel  +  IncomingPaymentDrawer   (rediseño de la bandeja)
```

El cajón se creó dos meses después del diálogo. El entrante se hizo con el componente nuevo y el
saliente se quedó con el patrón viejo porque funcionaba. Que la intención era el cajón se ve en
el propio artboard **3a** del proyecto de diseño, titulado «paso dentro del **drawer**», que
describe una pantalla del saliente.

## Qué es deuda y qué no

**Deuda:**
- Dos andamios para una sola pantalla.
- Dos contenedores (el saliente debería ser cajón, como decidió el rediseño de agosto).
- «Vincular a operación» está duplicada **palabra por palabra**, título y subtítulo.
- El paso de préstamo iza ~10 `useState` al diálogo en vez de vivir dentro de su propio paso.

**No es deuda — la asimetría es del negocio:**
- *Saldo a favor* sólo aplica a dinero que entró (y sólo a `USD`/`ZELLE`/`PAYPAL`).
- *Préstamo*, *gasto personal* e *irrelevante* sólo aplican a dinero que salió.

## El caso de «Repartir», que parece deuda y sólo lo es a medias

`whatsapp_payment_allocations` y `whatsapp_outgoing_settlements` tienen la misma forma —
`(pago, operación, monto)` con `UNIQUE(pago, operación)` — y el docstring del segundo dice
literalmente que es *«el espejo»* del primero.

La diferencia real es **en qué moneda va el monto**:

- **Entrante:** en la moneda del PAGO. El cliente manda un Zelle de 220 que cubre dos cambios de
  200 y 20; las tres cifras son ZELLE. Una sola moneda, así que la suma se compara directo
  contra el pago.
- **Saliente:** en la moneda del VALOR de cada operación. Le pagas 98.711,4 Bs y eso cubre dos
  tratos de 80 y 35 ZELLE; el comprobante va en bolívares y las partes en ZELLE. Dos monedas, y
  por eso existe `settled_reference_rate`: sin la tasa de cada parte no se sabe cuánto consume
  del comprobante.

Esa asimetría **es real**: del lado entrante el pago y la operación comparten moneda por
definición (la op nace del dinero que el cliente mandó), del saliente no — son las dos patas del
cambio. En el entrante la tasa siempre valdría 1.

## Hacia dónde iría, si se retoma

Un shell `PaymentActionPanel` dueño de lo común (cabecera, rejilla, máquina de pasos,
`stepHeader`, sugerencia) con la lista de acciones como prop:

```
ENTRANTE  → vincular · repartir · saldo a favor · depósito al fondo
SALIENTE  → vincular · repartir · préstamo · personal · irrelevante · depósito al fondo
```

Y una sola forma de reparto — `[{ operación, monto, moneda_del_monto, tasa }]` — donde el
entrante no pinta moneda ni tasa porque son constantes.

**Queda fuera y es decisión aparte:** unificar las dos TABLAS. Es una migración sobre 300+
liquidaciones y los repartos de entrantes, o sea tocar contabilidad viva. Unificar sólo la
pantalla es barato y reversible; unificar las tablas no.

## Riesgo de dejarlo

Cada cambio de la bandeja se escribe dos veces. Ya pasó: el 26 de agosto el mismo cambio —«la
diferencia con el comprobante se decide dentro del cajón»— tocó los dos ficheros por separado.
