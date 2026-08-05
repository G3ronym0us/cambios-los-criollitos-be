# Préstamos a una entidad (negocio sin teléfono)

Fecha: 2026-08-05
Estado: diseño aprobado, sin implementar

## Problema

El operador le presta dinero a un negocio propio para que pague facturas. Los comprobantes
de esos pagos los manda al grupo de WhatsApp del negocio, así que llegan al sistema como
pagos salientes con `client_phone` terminado en `@g.us`.

Hoy eso no se puede registrar. `ClientLoanService.create_from_outgoing`
(`app/services/client_loan_service.py:221`) corta seco:

```python
if payment.client_phone.endswith("@g.us"):
    raise QuoteServiceError("invalid_client", "No se puede registrar un préstamo a un grupo", 400)
```

La regla que hay detrás es correcta y se conserva: **un grupo no es un cliente**. Por eso
existe `upsert_anonymous_group_client` (`app/services/whatsapp_quote_service.py:105-115`),
que deja la op a nombre de un cliente anónimo cuando el comprobante llegó reenviado a un
grupo. Lo que falta no es prestarle a un grupo: es poder decir **a nombre de quién** queda
el préstamo cuando el comprobante pasó por ahí.

Además, no todos los préstamos tienen comprobante: a veces el pago sale por un canal que
el bot no monitorea y hoy no hay forma de registrarlo.

## Objetivo

1. Registrar préstamos a un negocio que no tiene teléfono propio en el bot.
2. Registrar préstamos sin comprobante (monto, moneda y fecha a mano).
3. Ver el total que ese negocio debe: una cifra en USDT más el desglose por referencia.

Cada préstamo conserva su propia referencia (FIAT / USDT / BCV); el caso que motiva esto
es una deuda en **USD BCV**, no en USDT.

## Decisiones tomadas

| Decisión | Elección |
|---|---|
| Representación del negocio | `WhatsAppClient` con clave sintética `entity:{slug}` — sin tabla nueva |
| Deudor cuando el pago fue a un grupo | `client_uuid` explícito, preseleccionado por el JID vinculado |
| Préstamos sin comprobante | `outgoing_payment_id` nullable, alta manual con fecha |
| Valuación | Sin cambios: fiat + USDT + BCV (si VES) + `preferred_value` |
| Total del negocio | Cifra única en USDT (tasa de hoy) + subtotales por referencia |
| Deudor de primera clase (tabla `loan_borrowers`) | Descartado: duplica pantallas y endpoints para un solo negocio |

## Arquitectura

### 1. Cliente-entidad

El negocio se registra como un `WhatsAppClient` con `phone = "entity:{slug}"` (ej.
`entity:bodegon-x`) y `display_name` = nombre del negocio. El slug se deriva del nombre.

El sistema ya usa claves sintéticas para clientes sin teléfono real (`anon:group:{id}`,
`anon:partner:{user_id}`). La diferencia: un `anon:` es un marcador de "todavía no sabemos
quién es" y `isGroupOrAnon` lo excluye de varios flujos; un `entity:` es un cliente de
verdad, con ficha, historial y saldo. Gana la ficha de `/admin/clients/{uuid}` y su pestaña
Préstamos sin escribir pantalla nueva.

Consecuencia: no se acepta un préstamo cuyo deudor sea un cliente `anon:` (código
`loan_client_invalid`). Un anónimo es precisamente el caso en que no se sabe a quién
cobrarle.

Se agrega `POST /clients` (hoy no existe) con `display_name` obligatorio y
`linked_group_jid` opcional; el `phone` lo arma el backend como `entity:{slug}` a partir del
nombre, resolviendo colisiones con un sufijo numérico.

### 2. Migración

Una sola migración Alembic:

- `whatsapp_clients.linked_group_jid` — `String(64)`, nullable, único. Ata el grupo del
  negocio a su entidad para preseleccionar el deudor.
- `client_loans.outgoing_payment_id` — pasa de `NOT NULL UNIQUE` a **nullable** UNIQUE. En
  Postgres los `NULL` no chocan entre sí, así que el `UNIQUE` sigue impidiendo dos
  préstamos sobre el mismo comprobante.

No se agrega columna `origin`: `outgoing_payment_id IS NULL` ya identifica el alta manual,
y `valuation_at` (que ya existe) guarda la fecha del préstamo, venga del comprobante o la
ponga el operador.

El resto del modelo (`fiat_amount`/`fiat_currency`, `usdt_amount`/`usdt_rate`,
`bcv_amount`/`bcv_rate`, `preferred_value`, `manual_values`) queda intacto.

### 3. Préstamo desde un comprobante enviado al grupo

`create_from_outgoing` acepta un `client_uuid` opcional y el bloqueo por `@g.us` se
reemplaza por la resolución del deudor:

| Pago | `client_uuid` | Resultado |
|---|---|---|
| Teléfono normal | ausente | Deudor = cliente del teléfono (comportamiento actual) |
| Teléfono normal | presente | Deudor = el indicado (el explícito manda) |
| Grupo (`@g.us`) o `anon:` | ausente | 400 `loan_borrower_required` |
| Grupo (`@g.us`) o `anon:` | presente | Deudor = el indicado |
| Cualquiera | apunta a un `anon:` | 400 `loan_client_invalid` |

`GET /payments/outgoing/{id}/loan-valuation` agrega `suggested_client` (uuid + nombre)
cuando el `client_phone` del pago matchea un `linked_group_jid`. El front lo preselecciona.

Las guardas actuales se conservan sin tocar: un pago marcado como préstamo no admite otra
clasificación ni operación (`_assert_not_loan`, `app/services/whatsapp_payment_service.py:143`),
y el `ondelete="RESTRICT"` sigue impidiendo borrar el comprobante mientras exista el préstamo.

### 4. Alta manual, sin comprobante

Dos endpoints nuevos colgando del cliente:

**`GET /clients/{uuid}/loans/valuation?amount=&currency=&at=`**
Sugiere las tres equivalencias a la fecha indicada, reusando las mismas funciones
históricas que el alta desde comprobante (`valuation.historical_rate`,
`historical_convert`, `historical_bcv`, delegadas en `client_loan_service.py:83-101`). Si a
esa fecha falta una tasa, devuelve `warnings` y el campo vacío para que el operador lo
llene.

**`POST /clients/{uuid}/loans`**
Crea el préstamo con monto, moneda fiat, fecha (`valuation_at`), `preferred_value`, notas y
los tres valores editables. `manual_values` se marca solo si el operador corrigió alguna
sugerencia, con la misma tolerancia de centavos que ya usa `create_from_outgoing`
(`changed()`, `client_loan_service.py:286-301`). Fecha futura → 400 `invalid_valuation_date`.

Un préstamo manual es indistinguible de uno con comprobante para todo lo demás: mismos
abonos, mismo saldo, misma serialización.

### 5. Abonos

Sin cambios. `POST /clients/{uuid}/loans/{loan_uuid}/repayments` ya cobra en la referencia
preferida del préstamo y guarda la equivalencia fiat/USDT/BCV del día del abono. Una deuda
en BCV se abona en USD BCV.

### 6. Total del negocio

`GET /clients/{uuid}/loans` devuelve un bloque `totals` calculado en el backend sobre los
préstamos `OPEN` y `PARTIAL`:

```json
{
  "by_reference": [
    { "currency": "USD_BCV", "amount": 1250.00 },
    { "currency": "VES", "amount": 8400.00 }
  ],
  "usdt_total": 1310.42,
  "warnings": []
}
```

- `by_reference`: el desglose fiel, sin mezclar referencias.
- `usdt_total`: cada saldo convertido a USDT a la tasa de hoy, sumado.

Si falta una tasa para convertir algún saldo, `usdt_total` viene `null` con el aviso en
`warnings` y los subtotales se muestran igual. La pestaña nunca revienta por una tasa
caída. El front deja de sumar por su cuenta (hoy lo hace en `ClientLoansTab.tsx:52-58`) y
pinta lo que manda el backend.

## Front

1. **Crear la entidad.** En `/admin/clients`, botón "Nuevo cliente-entidad" → diálogo con
   nombre del negocio y, opcional, el grupo de WhatsApp al que se le mandan los
   comprobantes (selector alimentado por los JID que ya aparecen en pagos salientes, o
   pegado a mano). Necesita `POST /clients`, que hoy no existe: los clientes solo nacen del
   tráfico del bot vía `upsert_client`.

2. **Mostrar una entidad como entidad.** Se agrega `isEntityClient()` junto a
   `isGroupOrAnon()` (`frontend/src/utils/functions.ts:136`). Donde hoy se pinta el
   teléfono, una entidad muestra la etiqueta **Entidad** y, si tiene, el grupo vinculado.
   Nunca el `entity:bodegon-x` crudo.

3. **Pestaña Préstamos (`ClientLoansTab.tsx`).**
   - Tarjeta de totales arriba: la cifra en USDT en grande, los subtotales por referencia
     como chips debajo. Con `usdt_total: null` muestra solo los subtotales y el aviso.
   - Botón "Registrar préstamo" → formulario de alta manual: monto, moneda, fecha,
     referencia preferida, notas y las tres equivalencias sugeridas y editables.
   - El bloque de equivalencias (fiat / USDT / BCV con sus avisos) hoy vive incrustado en
     `OutgoingPaymentActionDialog.tsx` (paso `loan`, ~573-710). Se extrae a un
     `LoanValuationFields` compartido que consumen los dos formularios. Ese diálogo ya está
     grande y este es el refactor mínimo que corresponde al pasar por ahí; no se toca nada
     ajeno al préstamo.

4. **Selector de deudor en el diálogo del comprobante.** En el paso `loan`, cuando el pago
   viene de un grupo, aparece el selector de cliente preseleccionado con la entidad
   vinculada al JID. Con un teléfono normal no se muestra nada nuevo.

## Pruebas

Archivo nuevo `backend/tests/test_client_loans.py`, sobre el patrón de `tests/conftest.py`
y `tests/factories.py`:

- Comprobante a grupo sin `client_uuid` → `loan_borrower_required` (400).
- Comprobante a grupo con `client_uuid` → préstamo a nombre de la entidad.
- Comprobante de teléfono normal sin `client_uuid` → sigue creando el préstamo como hoy
  (no regresión).
- Deudor `anon:` → `loan_client_invalid`.
- Alta manual en VES con fecha pasada → usa las tasas históricas de esa fecha y calcula BCV.
- Alta manual con fecha futura → `invalid_valuation_date`.
- Alta manual con una equivalencia corregida a mano → `manual_values = True`.
- `totals`: subtotales por referencia y `usdt_total`; con la tasa BCV caída → `usdt_total`
  en `null`, warning presente, subtotales intactos.
- Abono sobre un préstamo manual en BCV → reduce el saldo y cierra en `PAID`.

Verificación manual de la migración contra la BD local: los préstamos existentes quedan
igual y el `UNIQUE` sigue rechazando dos préstamos sobre el mismo comprobante.

## Fuera de alcance

Tabla de deudores aparte, préstamos con interés, vencimientos o recordatorios, y cruzar la
deuda del negocio con sus ganancias.
