# La operación sugerida se queda con el cliente

**Fecha:** 2026-09-10
**Alcance:** comprobantes **entrantes**. El lado saliente no se toca.

## El problema

El 7 de septiembre, José Bogao reenvió en su propio chat un Zelle de $200 y pasó sus datos
(`V-15252872 / Bancamiga / 04227162042`). A las 18:27 UTC salió el pago móvil de 177.192 Bs a ese
mismo número (saliente #5603). Al procesar la bandeja tres días después, el panel sugirió para ese
comprobante la operación **4644, de Arianna**, un trato distinto del mismo monto. Al vincularlo:

1. El comprobante #582 **cambió de chat**: pasó a figurar bajo Arianna, y buscar «José Bogao» ya no
   lo encontraba.
2. La operación tampoco aparecía: es de Arianna, sigue en `QUOTED` y venció el 7-sep a las 15:40.

Tres defectos independientes lo produjeron.

### Defecto 1 — la sugerencia ignora al cliente

`OperationMatchService.suggest_for_payments` (`app/services/operation_match_service.py:862`) carga
sus candidatas así:

```python
candidates = self._load_operations(limit=limit)   # línea 883 — sin un solo filtro
```

`_load_operations` acepta `phone`, `search`, `scenario` y `statuses`, y no recibe ninguno. Compiten
las últimas 500 operaciones del sistema entero. De ese pool, hoy mismo, **232 están COMPLETED y 3
CANCELLED**. Tampoco se mira si la op ya tiene comprobante: `_to_candidates` calcula
`has_incoming_payment` pero `score_candidate` nunca lo lee — solo lo usa `pick_auto_match`, la
política del bot.

Compárese con `auto_match` (línea 695), que sí filtra: `phone=phone` y
`statuses=OPEN_STATUSES if table == "incoming"`.

La puntuación es `0,75 · monto + 0,25 · tiempo`. **El cliente no entra en la fórmula.** Para el
comprobante #582 compitieron dos operaciones dentro de ±1%:

| op | cliente | estado | creada | Δt | score | ¿ya tenía entrante? |
|---|---|---|---|---|---|---|
| 4644 | Arianna | QUOTED (vencida) | 15:10 | 2,9 h | **0,9189** ← ganó | no (entonces) |
| 4636 | Leonardo Hernandez | PENDING | 13:18 | 4,8 h | 0,8895 | sí |

Ganó por 1 h 50 de diferencia horaria. Ninguna de las dos era de José Bogao, que además tiene
`is_tracked = false`: el bot no le cotiza, así que **por construcción no puede tener operaciones**.
No es un caso raro — **79 de 437 entrantes no irrelevantes (18%) vienen de clientes no seguidos**.
Para esa quinta parte, cualquier sugerencia es necesariamente la operación de otro.

### Defecto 2 — vincular reescribe el chat del comprobante

`app/services/whatsapp_payment_service.py:2061-2064`:

```python
# En el resto de los casos se conserva el criterio existente: vincular
# el comprobante a una operación afirma que pertenece a su cliente.
elif operation_client_phone:
    row.client_phone = operation_client_phone
```

`should_infer_client` exige `table == "outgoing"`, así que **todo entrante cae siempre en el
`elif`**. El chat en el que llegó el comprobante —un hecho observado— se sobrescribe con el cliente
de la operación, sin fila en `whatsapp_payment_transfers` ni línea en la bitácora. El mismo cambio
hecho a mano exige ROOT y deja rastro (`PATCH /{table}/{id}/client`).

### Defecto 3 — un entrante vinculado no mueve el estado

`set_operation` llama a `_sync_status_from_delivery` solo en la rama de salientes. Una op `QUOTED`
que recibe su comprobante se queda `QUOTED` y vencida, fuera de las bandejas de acción. **Hay 10
operaciones así.**

## Las tolerancias de hoy

`app/services/operation_match_service.py:40-73`

| Constante | Valor | Quién la usa |
|---|---|---|
| `AMOUNT_TOLERANCE` | ±1% | front + bot |
| `AMOUNT_CUTOFF` | ±2% | scoring |
| `TIME_HALF_LIFE_HOURS` | 6 h | scoring |
| `AMOUNT_WEIGHT` | 0,75 | scoring |
| `SUGGESTION_MARGIN` | 0,05 | `pick_suggestion` |
| `DEFAULT_WINDOW_HOURS` | 24 h | **solo el bot** |
| `OPEN_STATUSES` | QUOTED, PENDING | **solo el bot** |
| `MATCH_POOL_LIMIT` | 500 | front |

**El ranking del panel no tiene ventana temporal.** El tiempo puntúa, nunca descarta.

### Lo que dicen los datos de producción

98 entrantes ya vinculados y 1.366 salientes. Desfase `pago − operación`, en horas:

| | p05 | p25 | p50 | p75 | p90 | p95 | p99 |
|---|---|---|---|---|---|---|---|
| entrantes | −18,7 | −3,5 | 0,00 | 0,05 | **0,54** | 3,4 | 23,8 |
| salientes | | | 0,04 | | 0,64 | | 8,8 |

El 90% de los comprobantes llega dentro de los 32 minutos. Los valores negativos son el flujo
inverso: la operación se crea *después*, desde el comprobante.

Diferencia de monto: de 98 vínculos, **94 son exactos** (≤0,01%) y los otros 4 se pasan del 5%. No
hay nada en medio. El ±1% ni ayuda ni estorba; **lo que decide es el tiempo, y la vida media de 6 h
está demasiado floja** — a las 5 horas una candidata todavía puntúa 0,55.

## El diseño

### 1. La candidata sabe cuánto le falta

`OperationCandidate` gana dos campos:

```python
collected_incoming: float   # Σ whatsapp_payment_allocations.amount de esa op
missing_incoming: float     # from_amount − collected_incoming
```

`_to_candidates` ya hace dos consultas agregadas (`inc_taken` / `out_taken`); la del lado entrante
pasa de `EXISTS` a `SUM(amount) GROUP BY whatsapp_operation_id`. `has_incoming_payment` queda como
`collected_incoming > 0`, de modo que `pick_auto_match` no cambia de comportamiento.

### 2. `suggest_for_payments` acota el pool

Deja de cargar el mundo. Una consulta acotada por los teléfonos del lote, con los alias resueltos en
**una sola** consulta a `FundGroupMember` (no una por pago, como haría `_client_phones_for` llamado
en bucle), más `statuses=OPEN_STATUSES` y la ventana de fechas. Después se reparte en memoria por
cliente.

### 3. Elegibilidad y clase

Una operación compite para el comprobante *p* si:

| criterio | regla |
|---|---|
| cliente | `op.client.phone ∈ _client_phones_for(p.client_phone)` — teléfono del chat más sus ops anónimas de socio (`anon:partner:{uid}`) |
| estado | `QUOTED` o `PENDING` |
| tiempo | `\|op.created_at − p.created_at\| ≤ 72 h` |
| moneda | compatible; si a alguno de los dos lados le falta, no castiga (igual que hoy) |
| cabe | `missing_incoming ≥ p.amount · (1 − AMOUNT_TOLERANCE)` |

Y su **clase** es:

- **CIERRA** — `|missing_incoming − p.amount| ≤ AMOUNT_TOLERANCE` → el comprobante deja la operación
  sin faltante.
- **ABONA** — `missing_incoming − p.amount > AMOUNT_TOLERANCE` → cubre una parte y queda resto.

El caso `p.amount > missing_incoming` queda fuera a propósito: eso es saldo a favor, no una
sugerencia.

**Los pares en efectivo quedan fuera.** En un par con `CurrencyPair.settles_in_cash` el cliente paga
con billetes y no hay comprobante entrante ni lo habrá; una operación así nunca puede cerrarse con
un comprobante, y ofrecerla solo puede terminar en un vínculo falso. Mismo criterio para las
operaciones `VIA_PARTNER`, donde el socio cobra al cliente en su propio WhatsApp y el entrante nunca
llega al operador. Ambas exclusiones aplican **solo al lado entrante**, que es el único que este
diseño toca.

### 4. Orden y confianza

**La clase manda sobre el puntaje.** Primero todas las CIERRA, ordenadas entre sí por `score`;
después todas las ABONA, igual. `confident` solo si la primera es CIERRA y ninguna otra CIERRA le
empata dentro de `SUGGESTION_MARGIN`.

`score` se queda como está (`0,75 · monto + 0,25 · tiempo`): dentro de una misma clase todos los
montos ya caen dentro de la tolerancia, así que lo que ordena de hecho es el tiempo, y no hace falta
una fórmula nueva. Lo único que cambia es que la clase se evalúa **antes** que el puntaje.

Un cierre exacto es una afirmación mucho más fuerte que una coincidencia horaria: una op con
faltante de 100 cotizada hace 5 horas le gana a una con faltante de 500 cotizada hace 10 minutos.

### 5. Constantes

```python
TIME_HALF_LIFE_HOURS = 1.0    # era 6.0
INCOMING_WINDOW_HOURS = 72    # nueva; el ranking no tenía ninguna
```

`AMOUNT_TOLERANCE` y `AMOUNT_CUTOFF` se quedan igual. La vida media solo la lee `score_candidate`
—`pick_auto_match` usa su propia ventana dura de 24 h—, así que **el bot no se entera de ningún
cambio**.

| | vida media 6 h | vida media 1 h |
|---|---|---|
| 30 min | 0,92 | 0,67 |
| 1 h | 0,86 | 0,50 |
| 3 h | 0,67 | 0,25 |
| 5 h | **0,55** | **0,17** |
| 24 h | 0,20 | 0,04 |

### 6. Sin candidatas: sugerir crear

Cuando ninguna operación del cliente es elegible, la respuesta trae un `create_hint` en vez de
nada: par sugerido, por qué se eligió, la tasa **a la fecha del comprobante** y los dos montos ya
calculados. Se resuelve en el backend y en lote — en el front serían 50 peticiones de tasa, una por
fila.

Orden de preferencia del par: `preferred_pair_id` del cliente → el que más usa en su historial → el
que se deduce de la moneda del comprobante. Si ninguno resuelve, se devuelve el `create_hint` sin
par y la tarjeta abre el formulario vacío.

`CreateOperationForm.tsx` no cambia: ya precarga el par preferido, cuenta los pares más usados y
pide la tasa a la fecha del pago. Lo único que falta es que el panel lo ofrezca.

### 7. El contrato de `POST /payments/{table}/suggestions`

Dos formas según `kind`:

```jsonc
// kind: "LINK"
{
  "payment_id": 582, "kind": "LINK",
  "operation_uuid": "...", "confident": true,
  "coverage": "CLOSES",              // CLOSES | PARTIAL
  "client_name": "Jose Bogao",       // el cliente de la OPERACIÓN
  "client_uuid": "...",
  "same_client": true,               // ¿coincide con el chat del comprobante?
  "operation_created_at": "2026-09-07T15:10:39Z",
  "hours_apart": 2.88,               // firmado: negativo = la op nació después
  "status": "QUOTED", "expired": true,
  "from_amount": 200, "from_currency": "ZELLE",
  "to_amount": 177192, "to_currency": "VES",
  "missing_before": 200, "missing_after": 0,
  "delta": 0.0
}

// kind: "CREATE"
{
  "payment_id": 582, "kind": "CREATE",
  "create_hint": {
    "pair_uuid": "...", "pair_symbol": "ZELLE/VES",
    "reason": "preferred",           // preferred | most_used | currency
    "rate": 885.96, "rate_at": "2026-09-07",
    "from_amount": 200, "to_amount": 177192,
    "from_currency": "ZELLE", "to_currency": "VES"
  }
}
```

### 8. La lista del cajón hereda los mismos criterios

El cajón de «elegir otra» no se sirve de este endpoint sino de `POST /operations/match`
(`rank_for_payment`, `operation_match_service.py:754`), que sí acepta `phone` y `status`. Dos
cambios ahí:

- **Por defecto el cajón se abre acotado al cliente del comprobante** (con sus alias de socio) y a
  los estados abiertos, en vez del lote global de hoy. El total del pie ya se calcula sobre el
  filtro, así que dirá «3 operaciones de José Bogao» en vez de «312».
- **Un botón explícito «buscar en todos los clientes»** levanta el filtro. Ahí `same_client` deja de
  ser siempre `true` y la fila de otro cliente se pinta en ámbar.

Cada item de esa lista lleva los mismos campos enriquecidos que el `kind: "LINK"` de arriba
(`coverage`, `client_name`, `same_client`, `hours_apart`, `status`, `expired`, `missing_before`,
`missing_after`), para que la lista y la tarjeta digan lo mismo con las mismas palabras.

### 9. El front

**La fila** — `describeSuggestion` (`frontend/src/app/admin/payments/_components/paymentRowData.ts:178`)
deja de ser solo par y montos:

```
hoy    ✨ ZELLE/VES · 200 → 177.192
nuevo  ✨ cierra · hace 2 h 53
       ✨ abona 100, quedan 400 · hace 12 min
       ✨ Jose Bogao · cierra · hace 2 h 53      (op de OTRO cliente → ámbar)
       ✨ crear ZELLE/VES · 200 → 177.192
```

El cliente solo se nombra cuando difiere del chat del comprobante: repetirlo siempre es ruido, y así
el ámbar salta.

**La tarjeta del cajón** — `IncomingPaymentDrawer.tsx:481-517`:

```
✨ Operación sugerida            confianza alta
   Jose Bogao
   ZELLE/VES · 200 → 177.192
   cotizada 7-sep 15:10 — 2 h 53 antes del comprobante
   cotización vencida
   cierra el faltante: 200 → 0
   [ Vincular a esta operación ]  [ Elegir otra ]
```

```
✨ Sin operación para este pago
   Crear ZELLE/VES · 200 → 177.192
   tasa 885,96 del 7-sep · par preferido de Jose Bogao
   [ Crear esta operación ]  [ Elegir par ]
```

Ficheros: `types/payment.ts` (`PaymentSuggestion`), `paymentRowData.ts`, `PaymentRow.tsx`,
`PaymentItem.tsx`, `IncomingPaymentDrawer.tsx`.

### 10. La reescritura del chat deja rastro

Se conserva el comportamiento de `whatsapp_payment_service.py:2063` —vincular sigue afirmando que el
comprobante pertenece al cliente de la operación— pero deja de ser silencioso: cuando el
`client_phone` cambia, se escribe una fila en `whatsapp_payment_transfers` (con `reason` propio de
esta causa y `created_by_user_id` del operador) y la bitácora del comprobante gana su línea.

Con el filtro por cliente ya puesto, esto será raro: la operación será casi siempre del mismo
cliente y no habrá nada que reescribir.

### 11. El entrante mueve el estado de la operación

En la rama entrante de `set_operation`: una op en `QUOTED` que queda con comprobante pasa a
`PENDING`; al desvincular el último, vuelve a `QUOTED`.

**El TTL no puede bloquearlo**, por el mismo razonamiento ya escrito para el saliente en
`whatsapp_quote_service.py:490-498`: la expiración protege «el cliente no acepta una tasa vieja», y
un comprobante vinculado es dinero que ya se movió.

El paso a `COMPLETED` lo sigue decidiendo el lado saliente. Esa regla no se toca.

## Migración de datos

### Arrastre

`app/cli/backfill_incoming_quoted_to_pending.py`, con `--dry-run`: las operaciones en `QUOTED` que
tienen al menos un comprobante entrante vinculado pasan a `PENDING`. Hoy son 10.

### Reparación del caso 582

Con la API, no con SQL, para que la lógica de negocio genere transacción y movimientos de fondo:

1. `PATCH /payments/incoming/582/client` → devuelve el comprobante a José Bogao (el endpoint ya
   desengancha la operación al mudarlo).
2. `POST /payments/incoming/582/create-operation` → ZELLE/VES, 200 → 177.192, notas
   `V-15252872 / Bancamiga / 04227162042`.
3. `PATCH /payments/outgoing/5603/operation` → engancha el pago móvil, completa la operación y crea
   transacción y movimiento de fondo.

La operación nacería con la fecha de hoy. Si importan los números del 7-sep, hay que correr a mano
las cinco filas de fechas (`whatsapp_operations`, `transactions`, `fund_movements`,
`whatsapp_outgoing_settlements`, `whatsapp_payment_allocations`).

**Pendiente de decisión del operador:** la op 4644 de Arianna queda sin comprobante. Su mensaje de
las 15:10 («me van a cambiar 200 zelle… otra clienta») sugiere que pidió el trato, pero puede que
nunca se concretara. Si no existió, se cancela.

## Tests

`backend/tests/test_operation_match.py` — primitivas puras, sin BD:

- no sugiere la operación de otro cliente
- alcanza las operaciones anónimas de un socio (`anon:partner:{uid}`)
- excluye la operación ya cubierta (`missing_incoming == 0`)
- excluye `COMPLETED` y `CANCELLED`
- CIERRA gana a ABONA aunque ABONA sea mucho más reciente
- fuera de ±72 h no compite
- dos CIERRA empatadas ⇒ sugerida pero no `confident`
- sin candidatas ⇒ `kind: "CREATE"` con el par preferido
- **el ranking sigue siendo más permisivo que el bot, nunca al revés** (el invariante que ya prueba
  `test_ranking_is_more_permissive_than_the_bot_never_the_reverse`)

`backend/tests/` del servicio de pagos:

- vincular un entrante pasa la op de `QUOTED` a `PENDING`, aunque la cotización esté vencida
- desvincular el último entrante la devuelve a `QUOTED`
- la reescritura de `client_phone` genera fila en `whatsapp_payment_transfers` y línea de bitácora

## Validación antes de desplegar

Correr el matcher nuevo contra los 437 entrantes reales de producción y diffear sugerencia vieja
contra nueva, igual que la red de regresión del analizador. Lo que hay que medir:

- cuántas sugerencias de **otro cliente** desaparecen (el defecto que se está arreglando)
- cuántas filas pasan a `CREATE` (debería acercarse al 18% de clientes no seguidos)
- cuántas sugerencias correctas se pierden por la ventana de ±72 h (debería ser ~0: el p99 es 23,8 h)

Si el tercer número no es ~0, la ventana está mal elegida y se revisa antes de desplegar.

## Fuera de alcance

- **El lado saliente.** Un comprobante de salida llega en el chat del *beneficiario*, no del cliente
  que pidió el cambio: el pago a José Bogao entró en su propio chat, no en el de Arianna. Filtrar
  salientes por cliente los rompería a todos.
- **El matcher del bot** (`pick_auto_match`, `auto_match`, `auto_match_forwarded_incoming`). No se
  toca ninguna constante que lea.
- **La regla de `COMPLETED`**, que sigue siendo del lado saliente.
