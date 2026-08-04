# Confirmación de Zelle contra el correo del banco

Fecha: 2026-08-04
Estado: diseño aprobado, sin implementar

## Problema

Cuando un cliente paga por Zelle a una de las cuentas alquiladas, lo único que tenemos es
la captura que él mismo manda por WhatsApp. Esa captura se registra como pago entrante y,
cuando el operador la reenvía al grupo del dueño de la cuenta (escenario `ZELLE_DIRECT`,
`whatsapp-bot/src/whatsapp.ts:1021-1060`), se marca como contabilizada. En ningún momento
se verifica que el dinero haya entrado de verdad.

Los bancos sí avisan: mandan un correo a la cuenta que recibió el Zelle. Hoy nadie lo lee.

## Objetivo

Al reenviar una captura de Zelle al grupo, buscar en los buzones de las cuentas si llegó
una notificación del banco por el mismo monto, y avisarle al operador por WhatsApp:

> ✅ Confirmado por correo — a nombre de *Carlos R Barrientos*, en la cuenta de *Jean*
> (Bank of America, 4 ago 14:01)

Si no aparece, insistir de forma escalonada y terminar avisando que el pago quedó sin
confirmar.

## Decisiones tomadas

| Decisión | Elección |
|---|---|
| Buzones | Uno por cuenta (Jean, Mariana, …), todos Gmail |
| Acceso | IMAP con contraseña de aplicación |
| Dónde corre | Backend (FastAPI + Celery), no el bot |
| Criterio de match | Monto exacto + ventana de tiempo |
| Dónde busca | En todos los buzones; el aviso dice en cuál apareció |
| Si no aparece | Avisos escalonados y cierre a la hora |
| Front | Ninguna pantalla nueva |

El bot no toca IMAP ni conoce los buzones: sigue la línea de "bot tonto, backend cerebro".

## Formato de los correos

Los dos bancos ponen **nombre y monto en el asunto**, así que no hace falta parsear el
cuerpo HTML (que es la parte frágil de estos correos).

| Banco | From | Asunto |
|---|---|---|
| Bank of America | `customerservice@ealerts.bankofamerica.com` | `Carlos R Barrientos le envió $30.00` |
| FNBT-FCB | `CustServ@1stnb.com` | `Notification - Aristides Bravo sent you $107.00.` |

Buzones conocidos al momento del diseño: `azocarjean98@gmail.com` (Jean),
`mmendozaperez53@gmail.com` (Mariana; recibe de los dos bancos).

## Arquitectura

Cuatro piezas nuevas, todas en el backend.

### 1. Configuración de buzones (`.env`)

```
ZELLE_MAILBOXES='[{"label":"Jean","email":"azocarjean98@gmail.com","password":"xxxx xxxx xxxx xxxx"},
                  {"label":"Mariana","email":"mmendozaperez53@gmail.com","password":"..."}]'
```

`label` es lo que aparece en el aviso ("en la cuenta de Mariana"). Va en `.env` y no en la
base: son credenciales, cambian poco, y así no hace falta cifrado en base ni pantalla de
administración. Se valida al arrancar (JSON bien formado, campos presentes).

### 2. Tabla `bank_email_notifications`

Un renglón por correo ingerido.

| Columna | Notas |
|---|---|
| `id` | PK |
| `message_id` | **Único**. Es lo que hace idempotente la ingesta |
| `mailbox_label` | `Jean`, `Mariana` |
| `mailbox_email` | Buzón donde llegó |
| `bank` | `BANK_OF_AMERICA`, `FNBT` |
| `sender_name` | Quien envió el Zelle, tal como viene en el asunto |
| `amount` | Numeric(12,2) |
| `currency` | `USD` |
| `received_at` | `Date` del correo, normalizado a UTC |
| `subject` | Asunto crudo, para depurar |
| `auth_result` | Texto de `Authentication-Results`, para auditar |
| `consumed_by_payment_id` | FK nullable al pago entrante que confirmó |
| `created_at` | |

`consumed_by_payment_id` es lo que impide que dos Zelle del mismo monto se confirmen con
el mismo correo.

### 3. Tabla `bank_email_verifications`

Una verificación pendiente por pago entrante reenviado al grupo.

| Columna | Notas |
|---|---|
| `id` | PK |
| `incoming_payment_id` | FK, **único** (un pago se verifica una sola vez) |
| `amount` | Monto a buscar |
| `status` | `PENDING` \| `CONFIRMED` \| `NOT_FOUND` |
| `matched_notification_id` | FK nullable |
| `requested_at` | Momento del reenvío |
| `escalation_step` | Índice en la escalera de avisos |
| `next_notify_at` | Cuándo toca el siguiente aviso |
| `frozen_until` | Si el buzón está caído, no avanzar la escalera |
| `resolved_at` | |

### 4. Tarea Celery `poll_bank_emails` (cada 60 s)

Se agrega al `beat_schedule` de `app/celery_app.py`, junto a las de scraping y BCV.

Cada vuelta hace tres cosas, en orden:

1. **Ingesta**: por cada buzón, IMAP sobre `INBOX`, `SEARCH SINCE <ayer>`, y trae solo
   cabeceras con `BODY.PEEK[HEADER]`. El `PEEK` es obligatorio: **no marca los correos
   como leídos**, el operador sigue viendo su bandeja igual. Parsea, filtra e inserta
   ignorando los `message_id` ya vistos.
2. **Resolución**: para cada verificación `PENDING`, busca match. Si lo hay, consume el
   correo, marca `CONFIRMED` y avisa.
3. **Escalada**: para las `PENDING` cuyo `next_notify_at` ya pasó y no están congeladas,
   manda el aviso del escalón y avanza.

### 5. Parser de asuntos (`app/services/bank_email_parsers.py`)

Plantillas por dominio del remitente, misma idea que las plantillas de OCR del bot.

| Dominio | Regex sobre el asunto |
|---|---|
| `ealerts.bankofamerica.com` | `^(?P<name>.+?)\s+(?:le envió\|sent you)\s+\$(?P<amount>[\d,]+\.\d{2})` |
| `1stnb.com` | `^Notification\s*-\s*(?P<name>.+?)\s+sent you\s+\$(?P<amount>[\d,]+\.\d{2})` |

Agregar un banco es agregar una fila a esa tabla, no tocar la lógica.

## Autenticidad del correo

Sin esto, cualquiera que mande un correo con el asunto correcto "confirma" un pago falso.
Tres candados, todos en la ingesta:

1. **Remitente en lista blanca.** Solo se ingiere si el `From` es exactamente una de las
   direcciones esperadas. La plantilla del banco *es* la lista blanca.
2. **DKIM/SPF verificados por Gmail.** Se pide la cabecera `Authentication-Results` junto
   con el asunto y se exige que el dominio que pasó la autenticación coincida con el del
   remitente esperado. Un correo falsificado desde fuera no puede producir ese `pass`.
3. **Solo `INBOX`, nada reenviado.** No se mira Spam, y se descarta cualquier asunto que
   empiece con `Fwd:` / `RV:` / `Fw:`.

**Regla de aceptación**: `dkim=pass` **o** `spf=pass` con dominio coincidente. Se acepta
cualquiera de las dos porque el banco chico (FNBT-FCB) puede no tener DKIM alineado, y
exigirlo a rajatabla haría que esa cuenta dejara de confirmarse en silencio — la peor
falla posible aquí.

Un correo que parezca notificación de pago pero no pase ninguna de las dos **no se
ingiere y se avisa**: `⚠️ Correo de pago descartado por autenticación (<remitente>)`.

## Flujo

### Disparo

En `whatsapp-bot/src/whatsapp.ts`, dentro de la rama `if (forwardedSource)` (línea ~1042),
justo después de `bridgeMarkIncomingForwardedToGroup`, el bot llama a

```
POST /whatsapp/payments/incoming/{id}/verify-by-email
```

Respuesta inmediata: `{status: "confirmed", mailbox_label, sender_name, bank, received_at}`
o `{status: "pending"}`.

### Caso feliz (el correo ya está)

El backend busca en `bank_email_notifications` filas sin consumir, mismo monto, dentro de
ventana. La consume y responde confirmado. El bot agrega una línea al mensaje que ya
manda al operador:

```
🔁 Reenvío Zelle al grupo detectado — 1203…@g.us
💰 30,00 ZELLE
✅ Confirmado por correo — a nombre de Carlos R Barrientos, en la cuenta de Jean
   (Bank of America, 4 ago 14:01)
```

### Caso lento (el correo no llegó todavía)

Se crea la verificación `PENDING` y el bot responde `⏳ Buscando confirmación en los
correos…`. Cada vuelta del poller la reevalúa. Cuando aparece:

```
✅ Confirmado por correo a los 22 min — $30,00 a nombre de Carlos R Barrientos,
   en la cuenta de Jean
```

Ese aviso sale por `BOT_NOTIFY_URL` → `POST /api/notify`, el mismo canal que ya usan las
alertas de divergencia (`app/services/alert_service.py:52`).

### Escalera de avisos

La búsqueda corre cada minuto; lo escalonado es cuándo se le insiste al operador.

| Momento | Aviso |
|---|---|
| Al reenviar | `⏳ Buscando confirmación en los correos…` |
| 5 min | `⏳ $30,00 sigue sin aparecer en los correos (5 min)` |
| 15 min | `⚠️ $30,00 sin confirmar (15 min)` |
| 30 min | `⚠️ $30,00 sin confirmar (30 min)` |
| 60 min | `🚨 $30,00 SIN CONFIRMAR (1 h) — cierro la verificación` |

A los 60 min la verificación pasa a `NOT_FOUND` y deja de avisar: si a esa altura no llegó
el correo, el problema ya no es de latencia. Los escalones son una lista de constantes
(`[5, 15, 30, 60]`).

### Ventanas

Son dos, distintas:

- **Hacia atrás**: se consideran correos recibidos desde 12 h antes del pago entrante.
  Cubre que el reenvío al grupo ocurra mucho después de que el cliente pagó.
- **Hacia adelante**: 60 min de reintentos desde el reenvío (el último escalón).

### Criterio de match, exacto

Un correo confirma un pago entrante cuando se cumplen las cuatro condiciones:

1. `notification.amount == payment.amount` — igualdad exacta a dos decimales, sin
   tolerancia. Ambos lados son dólares con centavos; no hay conversión de por medio.
2. `notification.received_at` entre `payment.created_at - 12 h` y el momento actual.
3. `notification.consumed_by_payment_id IS NULL`.
4. Cualquier buzón. El buzón donde apareció no es criterio: es dato para el aviso.

El nombre del remitente **no** entra en el criterio (el OCR de la captura no lo lee de
forma confiable); se muestra en el aviso para que el operador lo juzgue.

Solo se verifican pagos entrantes con `currency = 'ZELLE'` y `amount` no nulo. Si el bot
pide verificar uno sin monto, el endpoint responde `{status: "skipped"}` y no crea
verificación.

### Montos repetidos

Si al confirmar hay **más de un** correo candidato sin consumir, se toma el más antiguo y
el aviso lo dice:

```
⚠️ Había 2 correos de $30,00 sin asignar; tomé el de las 14:01
```

Se avisa en vez de callar porque una confirmación equivocada haría entregar Bs de más.

## Manejo de errores

**Buzón ilegible (credencial revocada, red caída).** Es la falla más peligrosa: si la
escalera siguiera corriendo, llegarían "🚨 SIN CONFIRMAR" de pagos buenos y el operador
dejaría de creerle al sistema. Por eso, cuando un buzón falla, las verificaciones
pendientes **congelan su escalera** (`frozen_until`) en vez de avanzar, y tras ~5 minutos
de fallos seguidos se avisa distinto:

```
🚨 No puedo leer el correo de Mariana — revisar credenciales
```

El sistema nunca declara "no confirmado" cuando en realidad no pudo mirar.

**Consumo doble de un correo.** El poller toma un lock en Redis y el `UPDATE` que marca
consumido va con `SELECT … FOR UPDATE`. Sin eso, dos vueltas solapadas podrían confirmar
dos pagos con el mismo correo.

**Reenvío repetido de la misma captura.** La verificación es única por
`incoming_payment_id`; si ya está confirmada se devuelve la confirmación existente sin
consumir otro correo.

**Fechas.** El `Date` del correo viene en la zona del banco; se normaliza a UTC al
ingerir, como el resto del backend.

**Aviso que no sale.** Un `POST /api/notify` fallido no pierde el aviso: se reintenta en
la vuelta siguiente del poller.

**Límites de Gmail.** Con 2-3 buzones cada 60 s no hay riesgo de tocar el tope de
conexiones IMAP simultáneas; aun así se abre y cierra una conexión por buzón por vuelta.

## Pruebas

La capa IMAP queda detrás de una interfaz pequeña que devuelve cabeceras crudas, así que
parser y matching se prueban sin tocar Gmail.

- **Parser**: casos en JSON, al estilo de `whatsapp-bot/src/test-cases/`. Asuntos reales de
  BoA (español e inglés) y FNBT, más los que deben rechazarse: `Fwd:`, remitente parecido
  pero falso (`customerservice@ealerts-bankofamerica.com`), DKIM fallido.
- **Matching**: monto exacto, fuera de ventana, correo ya consumido, dos candidatos.
- **Escalera**: con el reloj inyectado.
- **Congelado**: buzón caído no avanza escalones ni cierra verificaciones.

Para probar contra Gmail de verdad: `python -m app.cli.check_mailboxes`, que se conecta a
cada buzón, dice si las credenciales sirven y muestra las últimas notificaciones
parseadas. Es lo que se corre al agregar una cuenta nueva.

## Fuera de alcance

- Ninguna pantalla nueva en el front.
- No cambia el estado de la operación ni la completa: la confirmación es informativa.
- Solo se dispara al reenviar al grupo; los Zelle que no se reenvían no se verifican.
- No se cruza el número de confirmación de Zelle: los correos de estos bancos no lo traen.

## Archivos afectados

**Backend (nuevo)**
- `app/models/bank_email.py` — las dos tablas
- `app/services/bank_email_parsers.py` — plantillas por banco
- `app/services/bank_email_service.py` — ingesta, matching, escalada
- `app/tasks/bank_email_tasks.py` — tarea Celery
- `app/cli/check_mailboxes.py` — diagnóstico
- `alembic/versions/xxxx_bank_email_notifications.py` — migración

**Backend (modificado)**
- `app/core/config.py` — `ZELLE_MAILBOXES`
- `app/celery_app.py` — `beat_schedule` + `include`
- `app/routers/whatsapp.py` — `POST /payments/incoming/{id}/verify-by-email`
- `app/schemas/whatsapp.py` — request/response

**Bot (modificado)**
- `src/api-client.ts` — cliente del endpoint nuevo
- `src/op-bridge.ts` — puente
- `src/whatsapp.ts` — llamada en la rama `forwardedSource` y línea en el mensaje
