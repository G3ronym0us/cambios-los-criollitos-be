# Operación sugerida por cliente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la operación sugerida para un comprobante entrante sea siempre del mismo cliente, elegible por lo que le falta cobrar, y que cuando no exista ninguna el panel proponga crearla.

**Architecture:** Todo el cambio de reglas vive en las primitivas puras de `app/services/operation_match_service.py` (sin BD, testeables solas). El servicio acota la consulta de candidatas al cliente y sus alias; el router enriquece la respuesta; el front pinta los criterios. Aparte, dos arreglos independientes en `app/services/whatsapp_payment_service.py`: el rastro de la mudanza al vincular, y el paso `QUOTED → PENDING`.

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic v2 · pytest · Next.js 15 + TypeScript + Tailwind

## Global Constraints

- **Los mensajes de commit van en inglés** (`backend/CLAUDE.md`). El código y los comentarios, en español, como el resto del repo.
- **El matcher del bot no se toca.** `pick_auto_match`, `auto_match`, `auto_match_forwarded_incoming` y las constantes que leen (`DEFAULT_WINDOW_HOURS`, `FORWARDED_TOLERANCE`, `FORWARDED_WINDOW_MINUTES`, `MIN_TOKEN_LENGTH`) quedan exactamente igual.
- **El lado saliente no cambia de comportamiento.** Todo lo nuevo se activa solo con `table == "incoming"`. El invariante que ya prueba `test_ranking_is_more_permissive_than_the_bot_never_the_reverse` tiene que seguir en verde.
- **Nunca invertir dos veces una tasa.** Ver la sección «La dirección de las tasas» de `backend/CLAUDE.md`. Este plan no toca tasas, pero si un test las roza, correr `pytest tests/test_whatsapp_rate_resolver.py`.
- Constantes acordadas, copiadas del spec: `TIME_HALF_LIFE_HOURS = 1.0` (era `6.0`), `INCOMING_WINDOW_HOURS = 72` (nueva). `AMOUNT_TOLERANCE = 0.01`, `AMOUNT_CUTOFF = 0.02`, `AMOUNT_WEIGHT = 0.75` y `SUGGESTION_MARGIN = 0.05` se quedan como están.
- Los tests corren contra un Postgres real en `:5433`; los de integración **se saltan solos** si no hay. Dos corridas a la vez chocan por la base.

**Spec:** `docs/superpowers/specs/2026-09-10-operacion-sugerida-por-cliente-design.md`

## Correcciones al plan, aprendidas ejecutándolo

Los ejemplos de test de las tareas se escribieron con fixtures que **no existen**. Lo real, verificado al implementar la Task 7:

- **La sesión de BD es el fixture `db`**, no `db_session`.
- **No hay `make_client` / `make_operation` / `make_incoming_payment` / `make_pair` / `make_rate`.** Lo que hay:
  - Fixtures de `tests/conftest.py`: `db`, `operator`, `partner`, `bot_user`, `fund`, `fund_with_shares`, `pairs` (dict con `ZELLE-BRL`, `ZELLE-VES`, `ZELLE-COP`, `USDT-BRL`, `COP-VES`), `client` (WhatsAppClient «Naldin», `13174961478`, tracked).
  - Pagos: `tests/factories.py` → `incoming(db, amount, currency="ZELLE", phone=...)`, `outgoing(db, amount, currency, phone=...)`.
  - **Las operaciones se construyen a mano** con `WhatsAppOperation(...)` (patrón de `tests/test_operation_scenario_auto.py`), o con un helper `_op` local. `create_op_from_payment` **no sirve para estos tests**: nace siempre en `PENDING` y ya vinculada.
  - Pares nuevos: helpers privados `_pair` / `_currency` de `conftest.py`.
- **`orphan_action` va en MAYÚSCULAS**: `"KEEP"` / `"DELETE_OPERATION"`. Con `"keep"` la llamada revienta con `QuoteServiceError("operation_would_be_orphan", 409)`. Y hay que pasar `completing_user`, o `no_payments_ack_by_user_id` queda en `None`.
- **La sesión va sin autoflush**: `self.db.flush()` antes de consultar algo que dependa de un cambio recién hecho.
- **El «tiene entrante» de una operación se mira por FK entrante OR `whatsapp_payment_allocations`.** Solo el FK no basta: un pago repartido entre varias ops deja el FK en una sola. Y las allocations solas tampoco: los comprobantes anteriores a esa tabla no tienen fila.
- Postgres local en `:5433` **estaba disponible** al ejecutar la Task 7 (609 passed, 2 xfailed, 1 xpassed en la suite completa; el xpassed es `test_concurrency_cancel_vs_cover`, no estricto e intermitente a propósito, preexistente). Si en tu corrida los tests de integración **se saltan**, dilo: una suite verde vacía no prueba nada.

---

## File Structure

| Fichero | Responsabilidad | Tareas |
|---|---|---|
| `app/services/operation_match_service.py` | Primitivas puras del matching + carga de candidatas | 1, 2, 3, 4, 6 |
| `app/schemas/operation_match.py` | Contrato de `/suggestions` y de `/operations/match` | 5, 6 |
| `app/routers/payments.py` | Endpoint de sugerencias | 5 |
| `app/services/whatsapp_payment_service.py` | Rastro de la mudanza + estado de la op al vincular | 7, 8 |
| `app/cli/backfill_incoming_quoted_to_pending.py` | Arrastre de las 10 ops colgadas | 9 |
| `tests/test_operation_match.py` | Primitivas puras, sin BD | 1, 2, 4, 6 |
| `tests/test_payment_link_status.py` | Estado y rastro al vincular (con BD) | 7, 8 |
| `frontend/src/types/payment.ts` | Tipo `PaymentSuggestion` | 10 |
| `frontend/src/app/admin/payments/_components/paymentRowData.ts` | Texto de la fila | 10 |
| `frontend/src/app/admin/payments/_components/IncomingPaymentDrawer.tsx` | Tarjeta de sugerencia | 11 |
| `scripts/diff_suggestions.py` | Validación viejo-vs-nuevo antes de desplegar | 12 |

---

## Task 1: La candidata sabe cuánto le falta

**Files:**
- Modify: `app/services/operation_match_service.py` (dataclass `OperationCandidate` ~L95-136, `_to_candidates` ~L665-691)
- Test: `tests/test_operation_match.py`

**Interfaces:**
- Consumes: nada (primera tarea).
- Produces: `OperationCandidate.collected_incoming: float` y `OperationCandidate.missing_incoming: float`. `OperationCandidate.from_model(op, *, has_outgoing_payment=False, has_incoming_payment=False, collected_incoming=0.0)`.

- [ ] **Step 1: Write the failing test**

En `tests/test_operation_match.py`, al final del fichero:

```python
def test_candidate_missing_incoming_defaults_to_from_amount():
    """Sin comprobantes entrantes, lo que falta cobrar es el lado `from` entero."""
    c = op("a", 14757.0, from_amount=100.0)
    assert c.collected_incoming == 0.0
    assert c.missing_incoming == 100.0


def test_candidate_missing_incoming_subtracts_what_is_already_allocated():
    c = op("a", 14757.0, from_amount=100.0, collected_incoming=40.0)
    assert c.missing_incoming == 60.0
```

El helper `op()` del fichero pasa `**kw` al constructor, así que `collected_incoming=40.0` llega solo. Verifícalo leyendo `op()` (~L31); si no fuera así, añade `collected_incoming=kw.pop("collected_incoming", 0.0)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_match.py -k missing_incoming -v`
Expected: FAIL con `TypeError: __init__() got an unexpected keyword argument 'collected_incoming'`

- [ ] **Step 3: Write minimal implementation**

En la dataclass `OperationCandidate`, junto a `value_amount` / `delivered_amount` / `pending_amount`:

```python
    # Lo que el cliente ya pagó de su lado (Σ whatsapp_payment_allocations) y lo que le
    # falta. El lado saliente tiene su propio par (`delivered_amount`/`pending_amount`):
    # son cosas distintas y no se mezclan.
    collected_incoming: float = 0.0
    missing_incoming: float = 0.0
```

En `from_model`, añadir el parámetro y calcular:

```python
    @classmethod
    def from_model(
        cls,
        op: WhatsAppOperation,
        *,
        has_outgoing_payment: bool = False,
        has_incoming_payment: bool = False,
        collected_incoming: float = 0.0,
    ) -> "OperationCandidate":
```

y dentro del `return cls(...)`, después de `pending_amount=...`:

```python
            collected_incoming=collected_incoming,
            missing_incoming=round((op.from_amount or 0.0) - collected_incoming, 2),
```

Para que `missing_incoming` se calcule también cuando se construye la dataclass a mano (los tests), añadir un `__post_init__`:

```python
    def __post_init__(self) -> None:
        # `from_model` ya lo calcula; esto cubre la construcción directa (tests y el helper
        # `op()`), donde solo se pasa `collected_incoming`.
        if not self.missing_incoming:
            self.missing_incoming = round((self.from_amount or 0.0) - self.collected_incoming, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_operation_match.py -v`
Expected: PASS, los ~40 casos existentes incluidos.

- [ ] **Step 5: Poblar `collected_incoming` desde la BD**

En `_to_candidates`, la consulta de entrantes pasa de `EXISTS` a suma. Reemplazar el bloque `inc_taken = {...}` por:

```python
        collected: dict[int, float] = {}
        if op_ids:
            from app.models.whatsapp_payment import WhatsAppPaymentAllocation

            collected = {
                row[0]: float(row[1] or 0)
                for row in self.db.query(
                    WhatsAppPaymentAllocation.whatsapp_operation_id,
                    safunc.sum(WhatsAppPaymentAllocation.amount),
                )
                .filter(WhatsAppPaymentAllocation.whatsapp_operation_id.in_(op_ids))
                .group_by(WhatsAppPaymentAllocation.whatsapp_operation_id)
                .all()
            }
```

Importar `from sqlalchemy import func as safunc` arriba del módulo si no está.

`has_incoming_payment` deja de tener su propia consulta y se deriva:

```python
        return [
            OperationCandidate.from_model(
                o,
                has_outgoing_payment=o.id in out_taken,
                has_incoming_payment=collected.get(o.id, 0.0) > 0,
                collected_incoming=collected.get(o.id, 0.0),
            )
            for o in ops
        ]
```

> **Cuidado:** un entrante vinculado por FK (`whatsapp_operation_id`) SIEMPRE tiene su fila de allocation — la escribe `_upsert_allocation` en `set_operation`. Si al correr el diff de la Task 12 aparecen ops con FK y sin allocation (data vieja), `has_incoming_payment` cambiaría de valor y afectaría a `pick_auto_match`. Compruébalo antes de dar la tarea por buena con esta consulta contra prod:
> ```sql
> SELECT count(*) FROM whatsapp_incoming_payments p
> WHERE p.whatsapp_operation_id IS NOT NULL
>   AND NOT EXISTS (SELECT 1 FROM whatsapp_payment_allocations a
>                    WHERE a.incoming_payment_id = p.id
>                      AND a.whatsapp_operation_id = p.whatsapp_operation_id);
> ```
> Si devuelve > 0, mantén la consulta `inc_taken` original SOLO para `has_incoming_payment` y usa `collected` únicamente para `missing_incoming`.

- [ ] **Step 6: Run the whole suite**

Run: `pytest tests/test_operation_match.py tests/ -k "match or payment" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/operation_match_service.py tests/test_operation_match.py
git commit -m "feat(match): candidates carry how much of the incoming side is still missing"
```

---

## Task 2: Elegibilidad por faltante y clase de cobertura

**Files:**
- Modify: `app/services/operation_match_service.py` (constantes ~L40-73, `expected_amount` ~L259, `MatchScore` ~L215, `score_candidate` ~L289, `rank_candidates` ~L329, `pick_suggestion` ~L348)
- Test: `tests/test_operation_match.py`

**Interfaces:**
- Consumes: `OperationCandidate.missing_incoming` (Task 1).
- Produces: `incoming_coverage(missing: Optional[float], paid: Optional[float]) -> Optional[str]` devolviendo `"CLOSES" | "PARTIAL" | None`; `MatchScore.coverage: Optional[str]`; `INCOMING_WINDOW_HOURS: int = 72`.

- [ ] **Step 1: Write the failing tests**

```python
def test_incoming_closes_when_the_receipt_leaves_nothing_missing():
    from app.services.operation_match_service import incoming_coverage

    assert incoming_coverage(100.0, 100.0) == "CLOSES"
    assert incoming_coverage(100.5, 100.0) == "CLOSES"   # dentro del 1%


def test_incoming_is_partial_when_the_receipt_fits_inside_what_is_missing():
    from app.services.operation_match_service import incoming_coverage

    assert incoming_coverage(500.0, 100.0) == "PARTIAL"


def test_incoming_is_not_a_candidate_when_the_receipt_exceeds_what_is_missing():
    """Eso es saldo a favor, no una sugerencia."""
    from app.services.operation_match_service import incoming_coverage

    assert incoming_coverage(60.0, 100.0) is None
    assert incoming_coverage(0.0, 100.0) is None


def test_closing_beats_a_much_more_recent_partial():
    """La clase manda sobre el puntaje: el cierre viejo le gana al abono reciente."""
    cierra = op("cierra", 14757.0, from_amount=100.0, minutes_ago=300)
    abona = op("abona", 73785.0, from_amount=500.0, minutes_ago=10)
    ranked = rank_candidates([abona, cierra], criteria(100.0, currency="USDT"), "incoming", NOW)
    sug = pick_suggestion(ranked)
    assert sug is not None and sug.uuid == "cierra" and sug.confident


def test_a_fully_covered_operation_is_not_a_candidate():
    cubierta = op("cubierta", 14757.0, from_amount=100.0, collected_incoming=100.0)
    ranked = rank_candidates([cubierta], criteria(100.0, currency="USDT"), "incoming", NOW)
    assert pick_suggestion(ranked) is None


def test_two_closing_candidates_are_suggested_but_not_confident():
    a = op("a", 14757.0, from_amount=100.0, minutes_ago=3)
    b = op("b", 14757.0, from_amount=100.0, minutes_ago=3)
    ranked = rank_candidates([a, b], criteria(100.0, currency="USDT"), "incoming", NOW)
    sug = pick_suggestion(ranked)
    assert sug is not None and not sug.confident


def test_outgoing_ranking_is_untouched_by_coverage():
    """El lado saliente no conoce las clases: su `coverage` es None."""
    ranked = rank_candidates([op("a", 14757.0)], criteria(14757.0), "outgoing", NOW)
    assert ranked[0].coverage is None
    assert ranked[0].within_tolerance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_operation_match.py -k "coverage or closing or covered or two_closing or untouched" -v`
Expected: FAIL con `ImportError: cannot import name 'incoming_coverage'`

- [ ] **Step 3: Añadir la constante y la primitiva**

Junto a las demás constantes (~L40-73):

```python
#: Ventana dura del ranking del PANEL del lado entrante. El ranking no tenía ninguna: el
#: tiempo puntuaba pero nunca descartaba, así que una op de hace tres meses con el monto
#: exacto podía ganar. Medido en producción (98 vínculos): el p90 del desfase op→comprobante
#: es 32 min y el p99 es 23,8 h, así que 72 h es holgado a propósito — cubre al cliente que
#: paga el lunes lo que se cotizó el viernes. No lo lee el bot, que tiene su propia
#: `DEFAULT_WINDOW_HOURS`.
INCOMING_WINDOW_HOURS = 72
```

Y bajar la vida media, reemplazando la línea existente:

```python
#: Horas a las que la cercanía temporal vale la mitad. Era 6 h, que con el p90 real en 32 min
#: dejaba a una candidata de hace 5 horas puntuando 0,55 y empatándole a la correcta.
TIME_HALF_LIFE_HOURS = 1.0
```

La primitiva, junto a `expected_amount`:

```python
def incoming_coverage(missing: Optional[float], paid: Optional[float]) -> Optional[str]:
    """
    Qué le hace este comprobante a lo que la operación todavía tiene por cobrar.

    `CLOSES` la deja sin faltante; `PARTIAL` cubre una parte y deja resto. `None` significa
    que no es candidata: o no falta nada, o el comprobante se pasa de lo que falta — y eso
    último es saldo a favor del cliente, una decisión aparte que nadie debe tomar por inercia
    desde una sugerencia.
    """
    if not paid or paid <= 0 or missing is None or missing <= 0:
        return None
    if abs(missing - paid) <= paid * AMOUNT_TOLERANCE:
        return "CLOSES"
    if missing > paid:
        return "PARTIAL"
    return None
```

- [ ] **Step 4: `MatchScore` lleva la clase**

Añadir el campo al final de la dataclass (con default, para no romper las construcciones posicionales existentes):

```python
    #: Solo del lado entrante: "CLOSES" | "PARTIAL". `None` en salientes, que no tienen clases.
    coverage: Optional[str] = None
```

- [ ] **Step 5: `expected_amount` compara contra el faltante**

Reemplazar la rama entrante:

```python
    if table == "incoming":
        # Lo que le toca comparar no es el lado `from` entero sino lo que queda por cobrar:
        # un trato de 500 al que ya le entraron 400 lo cierra un comprobante de 100.
        return cand.missing_incoming, cand.from_currency
```

- [ ] **Step 6: `score_candidate` clasifica y puntúa**

Dentro de `score_candidate`, después de calcular `currency_matches` y antes del cálculo de `amount_score`, insertar la rama entrante:

```python
    if table == "incoming":
        coverage = incoming_coverage(exp_amount, paid)
        if coverage is None:
            return MatchScore(cand.uuid, None, None, currency_matches, 0.0, time_score, 0.0, False)
        # Fuera de la ventana no compite, aunque el monto cuadre al céntimo.
        if abs((reference - cand.created_at).total_seconds()) > INCOMING_WINDOW_HOURS * 3600:
            return MatchScore(cand.uuid, None, None, currency_matches, 0.0, time_score, 0.0, False)
        delta = exp_amount - paid
        relative = abs(delta) / paid
        if coverage == "CLOSES":
            amount_score = max(0.0, 1.0 - relative / AMOUNT_CUTOFF)
            score = AMOUNT_WEIGHT * amount_score + (1 - AMOUNT_WEIGHT) * time_score
        else:
            # En un abono el monto no dice nada (cualquier cifra que quepa es igual de
            # válida), así que lo único que ordena es el tiempo. Las dos escalas nunca se
            # comparan entre sí: `rank_candidates` ordena por clase ANTES que por puntaje.
            amount_score = 0.0
            score = time_score
        return MatchScore(
            uuid=cand.uuid,
            delta=delta,
            relative=relative,
            currency_matches=True,
            amount_score=amount_score,
            time_score=time_score,
            score=score,
            within_tolerance=coverage == "CLOSES",
            coverage=coverage,
        )
```

`cand.created_at` puede ser `None`: el guard existente arriba de `_time_score` ya devuelve `0.0`, pero aquí se resta. Protegerlo:

```python
        if cand.created_at is None:
            return MatchScore(cand.uuid, None, None, currency_matches, 0.0, 0.0, 0.0, False)
```

justo antes del chequeo de ventana.

- [ ] **Step 7: `rank_candidates` ordena por clase primero**

```python
_COVERAGE_RANK = {"CLOSES": 0, "PARTIAL": 1}


def rank_candidates(candidates, criteria, table, now):
    """
    Puntúa todas y devuelve de mejor a peor.

    Del lado ENTRANTE la clase manda sobre el puntaje: primero todo lo que CIERRA, después
    todo lo que ABONA. Un cierre exacto es una afirmación mucho más fuerte que una
    coincidencia horaria. Del lado saliente `coverage` es siempre None y el orden queda
    exactamente como estaba. Desempate, en ambos: la más reciente primero.
    """
    scores: list[tuple[MatchScore, Optional[datetime]]] = [
        (score_candidate(cand, criteria, table, now), cand.created_at) for cand in candidates
    ]
    scores.sort(
        key=lambda pair: (
            _COVERAGE_RANK.get(pair[0].coverage, 0),
            -pair[0].score,
            -(pair[1].timestamp() if pair[1] else 0),
        )
    )
    return [s for s, _ in scores]
```

- [ ] **Step 8: `pick_suggestion` respeta la clase**

```python
def pick_suggestion(scored: Sequence[MatchScore]) -> Optional[Suggestion]:
    """
    Política del FRONT: la mejor candidata, marcada como inequívoca solo si ninguna otra de
    SU MISMA CLASE queda igual de cerca. En la duda el operador elige a mano.
    """
    closing = [s for s in scored if s.coverage == "CLOSES"]
    partial = [s for s in scored if s.coverage == "PARTIAL"]
    # Salientes: no hay clases, se mantiene el criterio de siempre.
    plain = [s for s in scored if s.coverage is None and s.within_tolerance]

    eligible = sorted(closing or plain or partial, key=lambda s: s.score, reverse=True)
    if not eligible:
        return None
    best = eligible[0]
    second = eligible[1] if len(eligible) > 1 else None
    confident = second is None or (best.score - second.score) >= SUGGESTION_MARGIN
    return Suggestion(uuid=best.uuid, confident=confident)
```

- [ ] **Step 9: Run the tests**

Run: `pytest tests/test_operation_match.py -v`
Expected: PASS, incluidos los casos viejos. Si `test_ranking_prorates_a_partially_covered_operation` o `test_incoming_side_compares_against_from_amount` fallan, **no los edites para que pasen**: el segundo debe seguir verde porque sin allocations `missing_incoming == from_amount`. Si falla, hay un bug en Task 1.

- [ ] **Step 10: Commit**

```bash
git add app/services/operation_match_service.py tests/test_operation_match.py
git commit -m "feat(match): rank incoming candidates by coverage class and a hard 72h window"
```

---

## Task 3: El pool de candidatas se acota al cliente

**Files:**
- Modify: `app/services/operation_match_service.py` (`_client_phones_for` ~L570, `_operations_query` ~L590, `suggest_for_payments` ~L862)
- Test: `tests/test_operation_match_service_db.py` (crear si no existe; si ya hay un fichero con tests de servicio con BD, añadirlos ahí)

**Interfaces:**
- Consumes: `incoming_coverage`, `INCOMING_WINDOW_HOURS` (Task 2).
- Produces: `OperationMatchService._client_phones_for_many(phones: Sequence[str]) -> dict[str, list[str]]`; `_operations_query(..., phones: Optional[Sequence[str]] = None, created_between: Optional[tuple[datetime, datetime]] = None)`.

- [ ] **Step 1: Write the failing test**

```python
def test_suggestions_never_reach_another_clients_operation(db_session, make_client, make_operation, make_incoming_payment):
    """El caso José Bogao: su comprobante no puede engancharse a la op de Arianna."""
    bogao = make_client(phone="584267169499", display_name="Jose Bogao")
    arianna = make_client(phone="584128580852", display_name="Arianna")
    make_operation(client=arianna, from_amount=200.0, from_currency="ZELLE", to_amount=177192.0)
    pago = make_incoming_payment(client_phone=bogao.phone, amount=200.0, currency="ZELLE")

    items = OperationMatchService(db_session).suggest_for_payments([pago.id], "incoming")

    assert len(items) == 1
    assert items[0]["kind"] == "CREATE"
```

> Usa las factories que ya existan en `tests/conftest.py`. Léelo antes de escribir el test: si no hay `make_operation` / `make_incoming_payment`, copia el patrón del fichero de tests de servicio más cercano (busca con `grep -rln "def make_" tests/`). **No inventes factories nuevas si ya existen equivalentes.**

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_match_service_db.py -k another_clients -v`
Expected: FAIL — devuelve `kind` inexistente o sugiere la op de Arianna.

- [ ] **Step 3: Resolver los alias de todos los teléfonos de una vez**

```python
    def _client_phones_for_many(self, phones: Sequence[str]) -> dict[str, list[str]]:
        """
        `_client_phones_for` para un lote, en UNA consulta.

        Llamarla en bucle sobre una página de 50 comprobantes son 50 viajes a la base para
        resolver lo mismo: qué teléfonos son de un socio y por tanto alcanzan sus operaciones
        anónimas (`anon:partner:{user_id}`, ver `_apply_scenario` en whatsapp_quote_service).
        """
        from app.models.fund import FundGroupMember

        unicos = [p for p in dict.fromkeys(phones) if p]
        if not unicos:
            return {}
        socios: dict[str, list[int]] = {}
        for phone, user_id in (
            self.db.query(FundGroupMember.whatsapp_phone, FundGroupMember.user_id)
            .filter(FundGroupMember.whatsapp_phone.in_(unicos))
            .distinct()
            .all()
        ):
            socios.setdefault(phone, []).append(user_id)
        return {p: [p, *(f"anon:partner:{uid}" for uid in socios.get(p, []))] for p in unicos}
```

Y reescribir `_client_phones_for` para que sea su caso de uno, y no queden dos copias de la regla:

```python
    def _client_phones_for(self, phone: str) -> list[str]:
        """Bajo qué clientes pueden estar las ops de este teléfono. Ver `_client_phones_for_many`."""
        return self._client_phones_for_many([phone]).get(phone, [phone])
```

- [ ] **Step 4: `_operations_query` acepta varios teléfonos y una ventana**

En la firma, añadir `phones: Optional[Sequence[str]] = None` y `created_between: Optional[tuple[datetime, datetime]] = None`. En el cuerpo, junto al `if phone:` existente:

```python
        if phone or search or phones:
            q = q.join(WhatsAppClient, WhatsAppClient.id == WhatsAppOperation.client_id)
        if phone:
            q = q.filter(WhatsAppClient.phone.in_(self._client_phones_for(phone)))
        if phones:
            q = q.filter(WhatsAppClient.phone.in_(list(phones)))
        if created_between:
            desde, hasta = created_between
            q = q.filter(WhatsAppOperation.created_at >= desde)
            q = q.filter(WhatsAppOperation.created_at <= hasta)
```

Ojo: la línea del join hoy es `if phone or search:`. Sustitúyela por la de arriba, o el filtro por `phones` explota sin join.

- [ ] **Step 5: `suggest_for_payments` deja de cargar el mundo**

Reemplazar el bloque `candidates = self._load_operations(limit=limit)` y el bucle por:

```python
        ventana = timedelta(hours=INCOMING_WINDOW_HOURS)
        alias = self._client_phones_for_many([p.client_phone for p in payments])
        todos = sorted({t for lista in alias.values() for t in lista})
        fechas = [_aware(p.created_at) for p in payments if p.created_at]
        if not todos or not fechas:
            return []
        ops = self._load_operation_models(
            limit=MATCH_POOL_LIMIT,
            phones=todos,
            statuses=OPEN_STATUSES,
            created_between=(min(fechas) - ventana, max(fechas) + ventana),
        )
        candidates = self._to_candidates(ops)
        # Las candidatas se reparten por cliente en memoria: la consulta ya vino acotada al
        # lote entero, y volver a la base una vez por comprobante no aporta nada.
        por_telefono: dict[str, list[OperationCandidate]] = {}
        for cand, modelo in zip(candidates, ops):
            telefono = modelo.client.phone if modelo.client else None
            if telefono:
                por_telefono.setdefault(telefono, []).append(cand)
```

y dentro del bucle de `payments`, antes de puntuar:

```python
            propias = [
                c
                for t in alias.get(payment.client_phone, [])
                for c in por_telefono.get(t, [])
            ]
            scored = rank_candidates(propias, criteria, table, now)
```

> **`_to_candidates` y `_load_operation_models` devuelven listas paralelas.** El `zip` de arriba depende de eso. Verifícalo leyendo `_to_candidates`: construye la lista por comprensión sobre `ops`, en orden, así que el `zip` es correcto. Si alguien lo cambia, este bloque se rompe en silencio — por eso el test de la Task 3 usa dos clientes distintos.

Del lado `outgoing`, esta función debe seguir comportándose como hoy: envolver el filtrado nuevo en `if table == "incoming": ... else: candidates = self._load_operations(limit=limit)` y dejar el bucle original para salientes.

- [ ] **Step 6: Excluir lo que nunca puede tener comprobante entrante**

Dos clases de operación no pueden cerrarse jamás con un entrante, y ofrecerlas solo puede terminar en un vínculo falso (lo explica `backend/CLAUDE.md`):

- **Pares con `CurrencyPair.settles_in_cash`**: el cliente paga con billetes y nadie fotografía un billete.
- **Operaciones `VIA_PARTNER`**: el socio cobra al cliente en su propio WhatsApp, así que el comprobante entrante nunca llega al operador.

Primero el test:

```python
def test_a_cash_pair_operation_is_never_suggested_for_an_incoming_receipt(db_session, make_client, make_pair, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125")
    efectivo = make_pair(from_currency="USD", to_currency="VES", settles_in_cash=True)
    make_operation(client=cliente, currency_pair=efectivo, from_amount=100.0)
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0, currency="USD")

    items = OperationMatchService(db_session).suggest_for_payments([pago.id], "incoming")
    assert items == [] or items[0]["kind"] == "CREATE"


def test_a_via_partner_operation_is_never_suggested_for_an_incoming_receipt(db_session, make_client, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125")
    make_operation(client=cliente, from_amount=100.0, scenario="VIA_PARTNER")
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0)

    items = OperationMatchService(db_session).suggest_for_payments([pago.id], "incoming")
    assert items == [] or items[0]["kind"] == "CREATE"
```

Y el filtro, en `_operations_query`, bajo un parámetro nuevo `payable_by_receipt: bool = False` para que **solo** lo use el lado entrante:

```python
        if payable_by_receipt:
            # Ni un par en efectivo ni un trato VIA_PARTNER reciben nunca comprobante del
            # cliente: el uno se paga con billetes y en el otro cobra el socio en su propio
            # chat. Ofrecerlos como candidatos solo puede terminar en un vínculo falso.
            q = q.join(CurrencyPair, CurrencyPair.id == WhatsAppOperation.currency_pair_id)
            q = q.filter(
                or_(
                    CurrencyPair.settles_in_cash.is_(False),
                    CurrencyPair.settles_in_cash.is_(None),
                )
            )
            q = q.filter(
                WhatsAppOperation.scenario != WhatsAppOperationScenario.VIA_PARTNER
            )
```

Importar `CurrencyPair` dentro de la función, como ya se hace con `FundGroup` y `WhatsAppClient`. Pasar `payable_by_receipt=True` desde la rama entrante de `suggest_for_payments` (Step 5) y desde `rank_for_payment` cuando `scope == "client"` (Task 6).

> **Verifica que `settles_in_cash` existe en el modelo** antes de escribir esto: `grep -n "settles_in_cash" app/models/currency_pair.py`. Según la nota del proyecto la columna existe pero el flag **no está encendido en ningún par todavía** (los datos estaban sucios), así que el filtro hoy no descartará nada — y aun así tiene que estar, porque el día que se encienda el comportamiento debe ser el correcto sin tocar nada.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_operation_match.py tests/test_operation_match_service_db.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/services/operation_match_service.py tests/
git commit -m "feat(match): scope incoming suggestions to the client and its partner aliases"
```

---

## Task 4: Sugerir crear cuando el cliente no tiene ninguna

**Files:**
- Modify: `app/services/operation_match_service.py` (`suggest_for_payments`)
- Test: `tests/test_operation_match_service_db.py`

**Interfaces:**
- Consumes: el reparto por cliente de la Task 3.
- Produces: items con `kind: "CREATE"` y un `create_hint` con las claves `pair_uuid`, `pair_symbol`, `reason`, `rate`, `rate_at`, `from_amount`, `to_amount`, `from_currency`, `to_currency`.

- [ ] **Step 1: Write the failing test**

```python
def test_create_hint_uses_the_clients_preferred_pair(db_session, make_client, make_pair, make_rate, make_incoming_payment):
    par = make_pair(from_currency="ZELLE", to_currency="VES")
    make_rate(pair=par, rate=885.96)
    cliente = make_client(phone="584267169499", display_name="Jose Bogao", preferred_pair=par)
    pago = make_incoming_payment(client_phone=cliente.phone, amount=200.0, currency="ZELLE")

    items = OperationMatchService(db_session).suggest_for_payments([pago.id], "incoming")

    assert items[0]["kind"] == "CREATE"
    hint = items[0]["create_hint"]
    assert hint["reason"] == "preferred"
    assert hint["pair_symbol"] == "ZELLE/VES"
    assert hint["from_amount"] == 200.0
    assert hint["to_amount"] == pytest.approx(177192.0, rel=1e-6)


def test_create_hint_falls_back_to_the_pair_the_client_uses_most(db_session, make_client, make_pair, make_rate, make_operation, make_incoming_payment):
    par = make_pair(from_currency="ZELLE", to_currency="VES")
    make_rate(pair=par, rate=885.96)
    cliente = make_client(phone="584267169499", display_name="Jose Bogao")  # sin preferido
    make_operation(client=cliente, currency_pair=par, status="COMPLETED")
    pago = make_incoming_payment(client_phone=cliente.phone, amount=200.0, currency="ZELLE")

    hint = OperationMatchService(db_session).suggest_for_payments([pago.id], "incoming")[0]["create_hint"]
    assert hint["reason"] == "most_used"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_operation_match_service_db.py -k create_hint -v`
Expected: FAIL con `KeyError: 'create_hint'`

- [ ] **Step 3: Implementar el hint**

Método nuevo en `OperationMatchService`:

```python
    def _create_hint(self, payment, alias_phones: Sequence[str]) -> Optional[dict]:
        """
        Con qué par nacería la operación de este comprobante, y cuánto daría.

        Orden de preferencia: el par preferido del cliente → el que más usa en su historial →
        el que se deduce de la moneda del comprobante. Si ninguno resuelve se devuelve `None`
        y la tarjeta abre el formulario vacío; adivinar un par es peor que no proponerlo.

        La tasa se pide A LA FECHA DEL COMPROBANTE, no la de hoy: la bandeja se procesa días
        después y cotizar con la tasa de hoy un cambio del lunes reescribe el margen.
        """
        from app.models.currency_pair import CurrencyPair
        from app.models.whatsapp_client import WhatsAppClient
        from app.services.rate_service import RateService   # verifica el nombre real del módulo

        cliente = (
            self.db.query(WhatsAppClient)
            .filter(WhatsAppClient.phone.in_(list(alias_phones)))
            .first()
        )
        par, motivo = None, None
        if cliente is not None and cliente.preferred_pair_id:
            par = self.db.query(CurrencyPair).get(cliente.preferred_pair_id)
            motivo = "preferred"
        if par is None and cliente is not None:
            fila = (
                self.db.query(
                    WhatsAppOperation.currency_pair_id,
                    safunc.count(WhatsAppOperation.id).label("n"),
                )
                .filter(WhatsAppOperation.client_id == cliente.id)
                .group_by(WhatsAppOperation.currency_pair_id)
                .order_by(safunc.count(WhatsAppOperation.id).desc())
                .first()
            )
            if fila is not None:
                par = self.db.query(CurrencyPair).get(fila[0])
                motivo = "most_used"
        if par is None and payment.currency:
            par = (
                self.db.query(CurrencyPair)
                .join(CurrencyPair.from_currency)
                .filter(CurrencyPair.is_active.is_(True))
                .filter(CurrencyPair.from_currency.has(symbol=payment.currency))
                .first()
            )
            motivo = "currency" if par is not None else None
        if par is None:
            return None

        tasa = RateService(self.db).rate_for_pair_at(par, _aware(payment.created_at))
        if tasa is None:
            return None
        destino = (
            payment.amount / tasa.rate if tasa.inverse_percentage else payment.amount * tasa.rate
        )
        return {
            "pair_uuid": str(par.uuid),
            "pair_symbol": f"{par.from_currency.symbol}/{par.to_currency.symbol}",
            "reason": motivo,
            "rate": tasa.rate,
            "rate_at": tasa.created_at.date().isoformat() if tasa.created_at else None,
            "from_amount": payment.amount,
            "to_amount": round(destino, 2),
            "from_currency": par.from_currency.symbol,
            "to_currency": par.to_currency.symbol,
        }
```

> **Dos cosas que hay que verificar en el código antes de escribir esto, no asumirlas:**
> 1. **El nombre real del servicio de tasas y de su método «tasa a una fecha».** `CreateOperationForm.tsx` llama a `ratesService.getRateByPair(pairUuid, payment.created_at)`, así que el endpoint existe: busca su router con `grep -rn "getRateByPair\|rate-by-pair\|/rates/pair" app/routers/` y usa el servicio que hay detrás. **No escribas una consulta de tasas nueva.**
> 2. **La dirección.** `destino = amount / rate if inverse else amount * rate` es la convención del proyecto (`apply_rate` en `whatsapp_rate_resolver.py`, `applyRateConversion` en el front). Reusa `apply_rate` si es importable en vez de repetir la fórmula — dos copias de esta regla ya causaron un bug que desvió por `r²`.

- [ ] **Step 4: Emitir el item**

En el bucle de `suggest_for_payments`, cuando `pick_suggestion` devuelve `None`:

```python
            if suggestion is None:
                hint = self._create_hint(payment, alias.get(payment.client_phone, []))
                if hint is not None:
                    out.append({"payment_id": payment.id, "kind": "CREATE", "create_hint": hint})
                continue
```

y el item que ya existía gana `"kind": "LINK"`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_operation_match_service_db.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/operation_match_service.py tests/
git commit -m "feat(match): propose creating an operation when the client has none open"
```

---

## Task 5: El contrato dice quién, cuándo y qué cubre

**Files:**
- Modify: `app/schemas/operation_match.py` (junto a `SuggestionResponse` ~L99)
- Modify: `app/services/operation_match_service.py` (el `out.append({...})` de `suggest_for_payments`)
- Modify: `app/routers/payments.py:161-173`
- Test: `tests/test_operation_match_service_db.py`

**Interfaces:**
- Consumes: items de las Tasks 3 y 4.
- Produces: `PaymentSuggestionItem` y `CreateHint` (Pydantic), y el endpoint tipado con `response_model=PaymentSuggestionsResponse`.

- [ ] **Step 1: Write the failing test**

```python
def test_link_item_carries_the_criteria_the_operator_needs(db_session, make_client, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125", display_name="Nelson")
    op_ = make_operation(client=cliente, from_amount=100.0, from_currency="ZELLE", to_amount=88596.0)
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0, currency="ZELLE")

    item = OperationMatchService(db_session).suggest_for_payments([pago.id], "incoming")[0]

    assert item["kind"] == "LINK"
    assert item["coverage"] == "CLOSES"
    assert item["client_name"] == "Nelson"
    assert item["same_client"] is True
    assert item["missing_before"] == 100.0
    assert item["missing_after"] == 0.0
    assert item["status"] == op_.status.value
    assert item["hours_apart"] == pytest.approx(0.0, abs=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_match_service_db.py -k criteria_the_operator -v`
Expected: FAIL con `KeyError: 'coverage'`

- [ ] **Step 3: Enriquecer el item**

En `suggest_for_payments`, donde hoy se arma el dict, necesitas el modelo de la op (no solo la candidata) para el nombre del cliente. Ya lo tienes en `ops`; indexa `por_uuid_op = {str(o.uuid): o for o in ops}` junto a `by_uuid`, y:

```python
            modelo = por_uuid_op.get(str(cand.uuid))
            horas = (
                (_aware(payment.created_at) - cand.created_at).total_seconds() / 3600.0
                if cand.created_at and payment.created_at
                else None
            )
            out.append(
                {
                    "payment_id": payment.id,
                    "kind": "LINK",
                    "operation_uuid": str(cand.uuid),
                    "confident": suggestion.confident,
                    "coverage": best.coverage,
                    "client_name": (modelo.client.display_name if modelo and modelo.client else None),
                    "client_uuid": (str(modelo.client.uuid) if modelo and modelo.client else None),
                    "same_client": bool(
                        modelo and modelo.client
                        and modelo.client.phone in alias.get(payment.client_phone, [])
                    ),
                    "operation_created_at": cand.created_at.isoformat() if cand.created_at else None,
                    "hours_apart": round(horas, 2) if horas is not None else None,
                    "status": cand.status,
                    "expired": bool(modelo and modelo.expires_at and modelo.expires_at <= now),
                    "score": round(best.score, 4),
                    "delta": best.delta,
                    "from_amount": cand.from_amount,
                    "from_currency": cand.from_currency,
                    "to_amount": cand.to_amount,
                    "to_currency": cand.to_currency,
                    "missing_before": cand.missing_incoming,
                    "missing_after": round(max(0.0, cand.missing_incoming - (payment.amount or 0)), 2),
                }
            )
```

- [ ] **Step 4: Los schemas**

En `app/schemas/operation_match.py`, después de `SuggestionResponse`:

```python
class CreateHint(BaseModel):
    """Con qué par nacería la operación de este comprobante, y cuánto daría."""

    pair_uuid: Optional[str] = None
    pair_symbol: Optional[str] = None
    #: preferred | most_used | currency — por qué se eligió ese par. El front lo redacta.
    reason: Optional[str] = None
    rate: Optional[float] = None
    rate_at: Optional[str] = None
    from_amount: Optional[float] = None
    to_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_currency: Optional[str] = None


class PaymentSuggestionItem(BaseModel):
    payment_id: int
    #: LINK = hay una operación que la cubre. CREATE = el cliente no tiene ninguna abierta.
    kind: Literal["LINK", "CREATE"]
    operation_uuid: Optional[str] = None
    confident: bool = False
    #: CLOSES = deja la op sin faltante. PARTIAL = abona y queda resto.
    coverage: Optional[Literal["CLOSES", "PARTIAL"]] = None
    client_name: Optional[str] = None
    client_uuid: Optional[str] = None
    #: ¿La op es del mismo cliente que el chat del comprobante? En ámbar cuando es False.
    same_client: bool = True
    operation_created_at: Optional[str] = None
    #: Firmado: negativo significa que la operación nació DESPUÉS del comprobante.
    hours_apart: Optional[float] = None
    status: Optional[str] = None
    expired: bool = False
    score: Optional[float] = None
    delta: Optional[float] = None
    from_amount: Optional[float] = None
    from_currency: Optional[str] = None
    to_amount: Optional[float] = None
    to_currency: Optional[str] = None
    missing_before: Optional[float] = None
    missing_after: Optional[float] = None
    create_hint: Optional[CreateHint] = None


class PaymentSuggestionsResponse(BaseModel):
    items: list[PaymentSuggestionItem]
```

Importar `Literal` de `typing` si no está.

- [ ] **Step 5: Tipar el endpoint**

En `app/routers/payments.py`, añadir el import y el `response_model`:

```python
@router.post("/{table}/suggestions", response_model=PaymentSuggestionsResponse)
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/ -k "suggestion or match" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/schemas/operation_match.py app/services/operation_match_service.py app/routers/payments.py tests/
git commit -m "feat(api): suggestions carry client, timing, status and coverage"
```

---

## Task 6: El cajón se abre acotado al cliente

**Files:**
- Modify: `app/services/operation_match_service.py` (`rank_for_payment` ~L754)
- Modify: `app/routers/operations.py:101` (`rank_operations_for_payment`)
- Modify: `app/schemas/operation_match.py` (`OperationScoreResponse`)
- Test: `tests/test_operation_match_service_db.py`

**Interfaces:**
- Consumes: `_client_phones_for_many` (Task 3), `MatchScore.coverage` (Task 2).
- Produces: `rank_for_payment(..., scope: Literal["client", "all"] = "client")`.

- [ ] **Step 1: Write the failing test**

```python
def test_drawer_defaults_to_the_payments_own_client(db_session, make_client, make_operation, make_incoming_payment):
    bogao = make_client(phone="584267169499", display_name="Jose Bogao")
    arianna = make_client(phone="584128580852", display_name="Arianna")
    make_operation(client=arianna, from_amount=200.0, from_currency="ZELLE", to_amount=177192.0)
    pago = make_incoming_payment(client_phone=bogao.phone, amount=200.0, currency="ZELLE")

    page = OperationMatchService(db_session).rank_for_payment(pago.id, "incoming")
    assert page.total == 0

    todas = OperationMatchService(db_session).rank_for_payment(pago.id, "incoming", scope="all")
    assert todas.total == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_operation_match_service_db.py -k drawer_defaults -v`
Expected: FAIL con `TypeError: rank_for_payment() got an unexpected keyword argument 'scope'`

- [ ] **Step 3: Implementar el `scope`**

En la firma de `rank_for_payment`, añadir `scope: str = "client"`. En el cuerpo, donde hoy se arma `filters`:

```python
        # Por defecto el cajón enseña las operaciones DEL CLIENTE del comprobante (con sus
        # alias de socio) y solo las abiertas: ese es el universo real de lo que ese pago
        # puede cerrar. `scope="all"` es el botón «buscar en todos los clientes», que el
        # operador pulsa a sabiendas — y ahí `same_client` deja de ser siempre cierto.
        filters = dict(phone=phone, search=search, statuses=[status] if status else None)
        if scope == "client" and table == "incoming" and phone is None:
            filters["phones"] = self._client_phones_for_many([payment.client_phone]).get(
                payment.client_phone, []
            )
            filters["statuses"] = filters["statuses"] or list(OPEN_STATUSES)
```

- [ ] **Step 4: El router pasa el parámetro**

En `app/routers/operations.py`, en `rank_operations_for_payment`, añadir el query param:

```python
    scope: Literal["client", "all"] = Query("client", description="client = solo las del cliente del comprobante"),
```

y pasarlo a `rank_for_payment(..., scope=scope)`.

- [ ] **Step 5: Los items del cajón llevan la clase**

En `OperationScoreResponse` (`app/schemas/operation_match.py` ~L95), añadir:

```python
    coverage: Optional[str] = None
```

Como el router construye ese schema desde `MatchScore`, el campo viaja solo si se construye con `model_validate` sobre la dataclass; comprueba cómo se arma hoy (`grep -n "OperationScoreResponse(" app/routers/operations.py`) y añade `coverage=s.coverage` si es explícito.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/ -k "match or drawer" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/operation_match_service.py app/routers/operations.py app/schemas/operation_match.py tests/
git commit -m "feat(api): the link drawer opens scoped to the receipt's own client"
```

---

## Task 7: Un entrante vinculado mueve la operación a PENDING

**Files:**
- Modify: `app/services/whatsapp_payment_service.py` (`set_operation` ~L1977-2110)
- Test: `tests/test_payment_link_status.py` (crear)

**Interfaces:**
- Consumes: nada de las tareas anteriores — es independiente y puede hacerse en paralelo.
- Produces: `WhatsAppPaymentService._sync_status_from_incoming(op, actor) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_linking_an_incoming_moves_a_quote_to_pending(db_session, make_client, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125")
    op_ = make_operation(client=cliente, from_amount=100.0, status="QUOTED")
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0)

    WhatsAppPaymentService(db_session).set_operation("incoming", pago.id, op_.uuid)

    db_session.refresh(op_)
    assert op_.status.value == "PENDING"


def test_an_expired_quote_still_moves_to_pending(db_session, make_client, make_operation, make_incoming_payment):
    """El TTL protege «el cliente no acepta una tasa vieja». Un comprobante ya es dinero movido."""
    cliente = make_client(phone="584124640125")
    op_ = make_operation(
        client=cliente, from_amount=100.0, status="QUOTED",
        expires_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0)

    WhatsAppPaymentService(db_session).set_operation("incoming", pago.id, op_.uuid)

    db_session.refresh(op_)
    assert op_.status.value == "PENDING"


def test_unlinking_the_last_incoming_returns_the_operation_to_quoted(db_session, make_client, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125")
    op_ = make_operation(client=cliente, from_amount=100.0, status="QUOTED")
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0)
    svc = WhatsAppPaymentService(db_session)
    svc.set_operation("incoming", pago.id, op_.uuid)

    svc.set_operation("incoming", pago.id, None, orphan_action="keep")

    db_session.refresh(op_)
    assert op_.status.value == "QUOTED"


def test_a_completed_operation_is_not_touched(db_session, make_client, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125")
    op_ = make_operation(client=cliente, from_amount=100.0, status="COMPLETED")
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0)

    WhatsAppPaymentService(db_session).set_operation("incoming", pago.id, op_.uuid)

    db_session.refresh(op_)
    assert op_.status.value == "COMPLETED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_payment_link_status.py -v`
Expected: FAIL — la op se queda en `QUOTED`.

- [ ] **Step 3: Implementar el sync**

Método nuevo en `WhatsAppPaymentService`:

```python
    def _sync_status_from_incoming(self, op: Optional[WhatsAppOperation], actor: Optional[User]) -> None:
        """
        El respaldo del cliente mueve la cotización a PENDING, y quedarse sin comprobantes la
        devuelve a QUOTED.

        **El TTL no bloquea este paso**, por el mismo razonamiento que ya está escrito para el
        saliente en `whatsapp_quote_service.complete_operation`: la expiración protege «el
        cliente no acepta una tasa vieja», y un comprobante vinculado es dinero que YA se
        movió. Bloquearlo dejaba la operación colgada en QUOTED y vencida, fuera de todas las
        bandejas — que es exactamente lo que pasó con 10 operaciones.

        El paso a COMPLETED sigue siendo del lado saliente. Aquí no se toca.
        """
        if op is None or op.status not in (
            WhatsAppOperationStatus.QUOTED,
            WhatsAppOperationStatus.PENDING,
        ):
            return
        tiene_entrante = (
            self.db.query(WhatsAppIncomingPayment.id)
            .filter(WhatsAppIncomingPayment.whatsapp_operation_id == op.id)
            .first()
            is not None
        )
        ahora = datetime.now(timezone.utc)
        if tiene_entrante and op.status == WhatsAppOperationStatus.QUOTED:
            op.status = WhatsAppOperationStatus.PENDING
            op.approved_at = op.approved_at or ahora
            op.updated_at = ahora
        elif not tiene_entrante and op.status == WhatsAppOperationStatus.PENDING:
            op.status = WhatsAppOperationStatus.QUOTED
            op.approved_at = None
            op.updated_at = ahora
```

> **Cuidado con el camino de la vuelta.** `PENDING → QUOTED` solo debe dispararse cuando la op se queda sin entrantes *por esta desvinculación*. Una op `VIA_PARTNER` o de un par con `settles_in_cash` está en `PENDING` legítimamente **sin haber tenido nunca** un comprobante entrante (ver `backend/CLAUDE.md`), y devolverla a `QUOTED` sería una regresión grave. Por eso el `elif` tiene que ejecutarse **solo si la operación tenía un entrante antes de esta llamada**. Guarda ese dato al entrar en `set_operation` (`tenia_entrante = row.whatsapp_operation_id is not None and table == "incoming"`) y pásalo al método como argumento en vez de deducirlo.

- [ ] **Step 4: Llamarlo desde `set_operation`**

Junto a la llamada existente `if table == "outgoing" and op is not None: ... self._sync_status_from_delivery(...)`, añadir el caso entrante. Al desvincular, `op` es `None`, así que hay que capturar la op de origen **antes** de soltar el FK:

```python
        op_previa = None
        if table == "incoming" and row.whatsapp_operation_id is not None:
            op_previa = (
                self.db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.id == row.whatsapp_operation_id)
                .first()
            )
```

al principio del método, y al final, antes de `self._sync_fund_legs(...)`:

```python
        if table == "incoming":
            self._sync_status_from_incoming(op or op_previa, completing_user, tenia_entrante=op_previa is not None)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_payment_link_status.py -v && pytest tests/ -k payment -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/whatsapp_payment_service.py tests/test_payment_link_status.py
git commit -m "feat(payments): linking an incoming receipt moves the quote to pending"
```

---

## Task 8: La mudanza al vincular deja rastro

**Files:**
- Modify: `app/services/whatsapp_payment_service.py:2061-2064`
- Modify: `app/models/whatsapp_payment_transfer.py` (enum `PaymentTransferReason`)
- Modify: `app/services/whatsapp_payment_service.py:92-100` (`_TRANSFER_REASON_LABELS`)
- Test: `tests/test_payment_link_status.py`

**Interfaces:**
- Consumes: nada.
- Produces: `PaymentTransferReason.LINKED_TO_OPERATION`.

> **Desviación del spec, deliberada.** El spec dice «cuando el `client_phone` cambia, se escribe una fila». Pero `transfer_client` (mismo fichero) **no toca `client_phone` a propósito** — su docstring: *«`client_phone` se queda como está (el origen sigue encontrándose al buscar)»* — y muda `owner_client_id`. El codebase ya separa el hecho (dónde llegó el comprobante) de la opinión (de quién es el dinero). Vincular debe usar el mismo mecanismo, no pisar el hecho. El efecto visible para el operador es el mismo (la fila pasa a mostrar el cliente de la operación, vía `_owner_ref`), y además el comprobante sigue encontrándose buscando el chat de origen.

- [ ] **Step 1: Write the failing test**

```python
def test_linking_to_another_clients_operation_leaves_a_trail(db_session, make_client, make_operation, make_incoming_payment):
    bogao = make_client(phone="584267169499", display_name="Jose Bogao")
    arianna = make_client(phone="584128580852", display_name="Arianna")
    op_ = make_operation(client=arianna, from_amount=200.0)
    pago = make_incoming_payment(client_phone=bogao.phone, amount=200.0)

    WhatsAppPaymentService(db_session).set_operation("incoming", pago.id, op_.uuid)

    db_session.refresh(pago)
    # El chat de origen NO se pisa: sigue siendo un hecho observado.
    assert pago.client_phone == "584267169499"
    assert pago.owner_client_id == arianna.id
    fila = db_session.query(WhatsAppPaymentTransfer).filter_by(incoming_payment_id=pago.id).one()
    assert fila.reason == PaymentTransferReason.LINKED_TO_OPERATION
    assert fila.from_client_id == bogao.id
    assert fila.to_client_id == arianna.id


def test_linking_to_an_operation_of_the_same_client_leaves_no_trail(db_session, make_client, make_operation, make_incoming_payment):
    cliente = make_client(phone="584124640125", display_name="Nelson")
    op_ = make_operation(client=cliente, from_amount=100.0)
    pago = make_incoming_payment(client_phone=cliente.phone, amount=100.0)

    WhatsAppPaymentService(db_session).set_operation("incoming", pago.id, op_.uuid)

    assert db_session.query(WhatsAppPaymentTransfer).count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_payment_link_status.py -k trail -v`
Expected: FAIL con `AttributeError: LINKED_TO_OPERATION`

- [ ] **Step 3: El motivo nuevo**

En `PaymentTransferReason`:

```python
    #: Se vinculó a la operación de otro cliente, y vincular afirma de quién es el dinero.
    LINKED_TO_OPERATION = "LINKED_TO_OPERATION"
```

Y su etiqueta en `_TRANSFER_REASON_LABELS`:

```python
    "LINKED_TO_OPERATION": "vinculado a la operación de otro cliente",
```

> **El enum es una columna `SQLEnum` en Postgres.** Añadir un valor necesita migración: `alembic revision -m "add LINKED_TO_OPERATION transfer reason"` con `op.execute("ALTER TYPE paymenttransferreason ADD VALUE 'LINKED_TO_OPERATION'")`. Confirma el nombre real del tipo con `\dT+` en psql antes de escribirla, y recuerda que `ALTER TYPE ... ADD VALUE` **no corre dentro de una transacción** en Postgres < 12: si falla, usa `with op.get_context().autocommit_block():`.

- [ ] **Step 4: Sustituir la reescritura**

Reemplazar el bloque `elif operation_client_phone: row.client_phone = operation_client_phone` por:

```python
            # Vincular un comprobante a una operación afirma de quién es el dinero — pero eso
            # es una OPINIÓN, y va en `owner_client_id`, no en `client_phone`, que es el hecho
            # observado de en qué chat llegó. Es el mismo criterio de `transfer_client`, y por
            # eso deja el mismo rastro: sin él, el comprobante desaparecía del chat donde el
            # operador lo había visto, sin que nada lo explicara (caso #582, José Bogao).
            elif op.client is not None:
                origen = row.owner_client or (
                    self.db.query(WhatsAppClient)
                    .filter(WhatsAppClient.phone == payment_client_phone)
                    .first()
                )
                if origen is None or origen.id != op.client.id:
                    row.owner_client_id = op.client.id
                    self.db.add(
                        WhatsAppPaymentTransfer(
                            incoming_payment_id=row.id,
                            from_client_id=origen.id if origen else None,
                            from_client_phone=payment_client_phone,
                            from_client_name=origen.display_name if origen else None,
                            to_client_id=op.client.id,
                            reason=PaymentTransferReason.LINKED_TO_OPERATION,
                            created_by_user_id=completing_user.id if completing_user else None,
                        )
                    )
```

Comprueba los nombres exactos de las columnas en `app/models/whatsapp_payment_transfer.py` antes de escribir el constructor.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_payment_link_status.py -v && pytest tests/ -k "payment or transfer" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/models/whatsapp_payment_transfer.py app/services/whatsapp_payment_service.py alembic/versions/ tests/
git commit -m "fix(payments): linking no longer overwrites the chat a receipt arrived in"
```

---

## Task 9: Arrastre de las operaciones colgadas

**Files:**
- Create: `app/cli/backfill_incoming_quoted_to_pending.py`

**Interfaces:**
- Consumes: `_sync_status_from_incoming` (Task 7).
- Produces: nada que consuman otras tareas.

- [ ] **Step 1: Leer un CLI existente y copiar su forma**

Run: `cat app/cli/backfill_outgoing_settlements.py`
No inventes una estructura nueva: `--dry-run`, salida por `print`, sesión de BD como la de ese fichero.

- [ ] **Step 2: Escribir el CLI**

```python
"""
Las operaciones que se quedaron en QUOTED con su comprobante entrante ya vinculado.

Pasaba porque `set_operation` solo sincronizaba el estado en la rama de SALIENTES: un entrante
se enganchaba y la cotización seguía QUOTED y vencida, fuera de todas las bandejas. Arreglado
en el mismo cambio que trae este arrastre; esto es solo para las que ya quedaron así.

    docker compose exec -T backend python -m app.cli.backfill_incoming_quoted_to_pending --dry-run
"""

import argparse
from datetime import datetime, timezone

from app.database.connection import SessionLocal
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_payment import WhatsAppIncomingPayment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ops = (
            db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.status == WhatsAppOperationStatus.QUOTED)
            .filter(
                WhatsAppOperation.id.in_(
                    db.query(WhatsAppIncomingPayment.whatsapp_operation_id).filter(
                        WhatsAppIncomingPayment.whatsapp_operation_id.isnot(None)
                    )
                )
            )
            .all()
        )
        print(f"{len(ops)} operaciones en QUOTED con comprobante entrante")
        ahora = datetime.now(timezone.utc)
        for o in ops:
            print(f"  op {o.id} ({o.uuid}) {o.from_amount} → {o.to_amount}  cotizada {o.created_at}")
            if not args.dry_run:
                o.status = WhatsAppOperationStatus.PENDING
                o.approved_at = o.approved_at or ahora
                o.updated_at = ahora
        if args.dry_run:
            print("dry-run: no se escribió nada")
        else:
            db.commit()
            print(f"{len(ops)} operaciones pasadas a PENDING")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Probarlo en seco contra local**

Run: `python -m app.cli.backfill_incoming_quoted_to_pending --dry-run`
Expected: lista las ops, no escribe nada.

- [ ] **Step 4: Commit**

```bash
git add app/cli/backfill_incoming_quoted_to_pending.py
git commit -m "chore(cli): backfill operations stuck in QUOTED with an incoming receipt"
```

---

## Task 10: El front — el tipo y la fila

**Files:**
- Modify: `frontend/src/types/payment.ts:41-53`
- Modify: `frontend/src/app/admin/payments/_components/paymentRowData.ts:178`
- Test: `frontend/src/app/admin/payments/_components/paymentRowData.test.ts`

**Interfaces:**
- Consumes: el contrato de la Task 5.
- Produces: `describeSuggestion(s: PaymentSuggestion): string`.

- [ ] **Step 1: Write the failing test**

En `paymentRowData.test.ts`:

```ts
import { describeSuggestion } from './paymentRowData';

const base = {
  payment_id: 1, kind: 'LINK' as const, operation_uuid: 'u', confident: true,
  coverage: 'CLOSES' as const, client_name: 'Nelson', client_uuid: 'c', same_client: true,
  operation_created_at: '2026-09-07T15:10:39Z', hours_apart: 2.88, status: 'QUOTED',
  expired: true, score: 0.9, delta: 0, from_amount: 200, from_currency: 'ZELLE',
  to_amount: 177192, to_currency: 'VES', missing_before: 200, missing_after: 0,
  create_hint: null,
};

it('no repite el cliente cuando la operación es del mismo chat', () => {
  expect(describeSuggestion(base)).toBe('cierra · hace 2 h 53');
});

it('nombra al cliente cuando la operación es de otro', () => {
  expect(describeSuggestion({ ...base, same_client: false, client_name: 'Arianna' }))
    .toBe('Arianna · cierra · hace 2 h 53');
});

it('dice cuánto abona y cuánto queda', () => {
  expect(describeSuggestion({ ...base, coverage: 'PARTIAL', missing_before: 500, missing_after: 300, hours_apart: 0.2 }))
    .toBe('abona 200, quedan 300 · hace 12 min');
});

it('propone crear cuando no hay operación', () => {
  expect(describeSuggestion({
    ...base, kind: 'CREATE', coverage: null, operation_uuid: null,
    create_hint: { pair_symbol: 'ZELLE/VES', from_amount: 200, to_amount: 177192, reason: 'preferred', rate: 885.96, rate_at: '2026-09-07', pair_uuid: 'p', from_currency: 'ZELLE', to_currency: 'VES' },
  })).toBe('crear ZELLE/VES · 200 → 177.192');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/app/admin/payments/_components/paymentRowData.test.ts`
Expected: FAIL

- [ ] **Step 3: Actualizar el tipo**

Reemplazar `PaymentSuggestion` en `types/payment.ts` por el contrato de la Task 5, campo por campo, con los mismos nombres y nulabilidad. Añadir también:

```ts
export interface CreateHint {
  pair_uuid: string | null;
  pair_symbol: string | null;
  reason: 'preferred' | 'most_used' | 'currency' | null;
  rate: number | null;
  rate_at: string | null;
  from_amount: number | null;
  to_amount: number | null;
  from_currency: string | null;
  to_currency: string | null;
}
```

- [ ] **Step 4: Reescribir `describeSuggestion`**

```ts
/**
 * Lo que la fila necesita saber de un vistazo: qué le hace este comprobante a la operación y
 * hace cuánto se cotizó. El cliente solo se nombra cuando NO es el del chat del comprobante —
 * repetirlo siempre es ruido, y así el caso raro salta.
 */
export function describeSuggestion(s: PaymentSuggestion): string {
  if (s.kind === 'CREATE') {
    const h = s.create_hint;
    if (!h) return 'crear operación';
    const montos = [h.from_amount, h.to_amount]
      .filter((n): n is number => n != null)
      .map(formatNumber);
    return ['crear', h.pair_symbol, montos.length === 2 ? `· ${montos.join(' → ')}` : null]
      .filter(Boolean)
      .join(' ');
  }
  const clase =
    s.coverage === 'PARTIAL' && s.missing_before != null && s.missing_after != null
      ? `abona ${formatNumber(s.missing_before - s.missing_after)}, quedan ${formatNumber(s.missing_after)}`
      : 'cierra';
  return [s.same_client ? null : s.client_name, clase, describeElapsed(s.hours_apart)]
    .filter(Boolean)
    .join(' · ');
}

/** «hace 12 min» / «hace 2 h 53» / «hace 3 d». Negativo = la op nació después del comprobante. */
function describeElapsed(hours: number | null): string | null {
  if (hours == null) return null;
  const abs = Math.abs(hours);
  const prefijo = hours < 0 ? 'después' : 'hace';
  if (abs < 1) return `${prefijo} ${Math.round(abs * 60)} min`;
  if (abs < 24) {
    const h = Math.floor(abs);
    const m = Math.round((abs - h) * 60);
    return m ? `${prefijo} ${h} h ${m}` : `${prefijo} ${h} h`;
  }
  return `${prefijo} ${Math.round(abs / 24)} d`;
}
```

`formatNumber` ya está importado en el fichero.

- [ ] **Step 5: Pintar el ámbar en la fila**

En `PaymentRow.tsx:101-107` y en `PaymentItem.tsx`, añadir la clase condicional al chip:

```tsx
className={cn(
  'flex items-center gap-1 text-xs',
  !suggestion.same_client && 'text-amber-600 dark:text-amber-400',
)}
title={
  suggestion.same_client
    ? `Sugerida por el matcher${suggestion.confident ? '' : ' (hay otra candidata igual de cerca)'}`
    : `Ojo: la operación es de ${suggestion.client_name ?? 'otro cliente'}`
}
```

- [ ] **Step 6: Run tests and typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/payment.ts frontend/src/app/admin/payments/_components/
git commit -m "feat(payments-ui): the suggestion row states coverage, timing and whose operation it is"
```

---

## Task 11: El front — la tarjeta del cajón

**Files:**
- Modify: `frontend/src/app/admin/payments/_components/IncomingPaymentDrawer.tsx:481-517`

**Interfaces:**
- Consumes: `PaymentSuggestion`, `CreateHint`, `describeSuggestion` (Task 10).
- Produces: nada.

- [ ] **Step 1: La cara LINK**

Sustituir el cuerpo de la tarjeta por:

```tsx
{suggestion && suggestion.kind === 'LINK' && !p.operation_uuid ? (
  <div className={cn(
    'rounded-xl border bg-card p-3 shadow-sm',
    suggestion.same_client ? 'border-primary/40' : 'border-amber-500/60',
  )}>
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <span className="flex items-center gap-1 rounded bg-primary/10 px-2 py-0.5 text-[11px] font-bold text-primary">
        <Sparkles className="h-3 w-3" />
        Operación sugerida
      </span>
      <span className="text-[11px] text-muted-foreground">
        {suggestion.confident ? 'confianza alta' : 'hay otra candidata parecida'}
      </span>
    </div>

    {suggestion.client_name ? (
      <p className={cn(
        'text-[13px] font-semibold',
        suggestion.same_client ? 'text-foreground' : 'text-amber-700 dark:text-amber-400',
      )}>
        {suggestion.client_name}
        {suggestion.same_client ? null : ' — es de otro cliente'}
      </p>
    ) : null}

    <p className="text-[13px] text-foreground">
      {[suggestion.from_currency, suggestion.to_currency].filter(Boolean).join('/')}
      {' · '}
      {formatNumber(suggestion.from_amount ?? 0)} → {formatNumber(suggestion.to_amount ?? 0)}
    </p>

    <p className="mt-1 text-xs text-muted-foreground">
      {suggestion.operation_created_at
        ? `cotizada ${formatDateTime(suggestion.operation_created_at)}`
        : null}
      {suggestion.hours_apart != null ? ` — ${describeGap(suggestion.hours_apart)}` : null}
      {suggestion.expired ? ' · cotización vencida' : null}
    </p>

    <p className="mt-1 text-xs font-medium tabular-nums">
      {suggestion.coverage === 'CLOSES'
        ? `cierra el faltante: ${formatNumber(suggestion.missing_before ?? 0)} → 0`
        : `abona ${formatNumber((suggestion.missing_before ?? 0) - (suggestion.missing_after ?? 0))} · quedan ${formatNumber(suggestion.missing_after ?? 0)} por cubrir`}
    </p>

    <div className="mt-3 flex gap-2">
      <Button className="flex-1" onClick={linkSuggested} disabled={submitting}>
        Vincular a esta operación
      </Button>
      <Button variant="outline" onClick={() => setStep('operation')} disabled={submitting}>
        Elegir otra
      </Button>
    </div>
  </div>
) : null}
```

`describeGap(hours)` es `«2 h 53 antes del comprobante»` / `«12 min después del comprobante»`: escríbelo junto a `describeElapsed` en `paymentRowData.ts` y expórtalo. `formatDateTime` ya existe en el proyecto — búscalo con `grep -rn "export function formatDateTime" frontend/src/` y usa ese, no escribas otro.

- [ ] **Step 2: La cara CREATE**

Justo después del bloque anterior:

```tsx
{suggestion && suggestion.kind === 'CREATE' && !p.operation_uuid ? (
  <div className="rounded-xl border border-primary/40 bg-card p-3 shadow-sm">
    <div className="mb-2 flex items-center gap-2">
      <span className="flex items-center gap-1 rounded bg-primary/10 px-2 py-0.5 text-[11px] font-bold text-primary">
        <Sparkles className="h-3 w-3" />
        Sin operación para este pago
      </span>
    </div>
    {suggestion.create_hint ? (
      <>
        <p className="text-[13px] font-semibold text-foreground">
          Crear {suggestion.create_hint.pair_symbol} ·{' '}
          {formatNumber(suggestion.create_hint.from_amount ?? 0)} →{' '}
          {formatNumber(suggestion.create_hint.to_amount ?? 0)}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          tasa {formatNumber(suggestion.create_hint.rate ?? 0)}
          {suggestion.create_hint.rate_at ? ` del ${suggestion.create_hint.rate_at}` : null}
          {' · '}
          {HINT_REASON[suggestion.create_hint.reason ?? 'currency']}
        </p>
      </>
    ) : (
      <p className="text-[13px] text-muted-foreground">
        Este cliente no tiene ninguna operación abierta que cuadre.
      </p>
    )}
    <div className="mt-3 flex gap-2">
      <Button className="flex-1" onClick={() => setStep('operation')} disabled={submitting}>
        Crear esta operación
      </Button>
    </div>
  </div>
) : null}
```

con el diccionario junto a los imports del fichero:

```tsx
const HINT_REASON: Record<string, string> = {
  preferred: 'par preferido del cliente',
  most_used: 'el par que más usa',
  currency: 'deducido de la moneda del comprobante',
};
```

- [ ] **Step 3: Pasar el hint al formulario**

`setStep('operation')` abre `LinkOperationPanel`, que a su vez tiene la pestaña de crear. Pasa el `create_hint` hacia abajo como prop opcional para que `CreateOperationForm` lo use como valor inicial del par (`setPairUuid(hint.pair_uuid)`), en vez de esperar a que el `useEffect` del cliente lo resuelva. Lee `CreateOperationForm.tsx:196-221` para ver dónde se fija `pairUuid` hoy y respeta el patrón `setPairUuid((current) => current || preferred)`.

- [ ] **Step 4: Typecheck y build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: PASS

- [ ] **Step 5: Verificarlo en el navegador**

Levanta el front contra el backend local, entra a `/admin/payments`, abre un entrante sin vincular y comprueba las dos caras de la tarjeta. Si no tienes datos, siembra con `python -m app.cli.seed_payment_cases`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/admin/payments/_components/
git commit -m "feat(payments-ui): the suggestion card shows the criteria and offers creating"
```

---

## Task 12: Diff de sugerencias contra producción

**Files:**
- Create: `scripts/diff_suggestions.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: nada.

- [ ] **Step 1: Escribir el script**

```python
"""
Qué cambia la sugerencia nueva contra los comprobantes REALES de producción.

No se despliega nada sin correr esto. Se lanza contra una copia de la base de prod (o contra
prod en solo lectura) y responde tres preguntas:

  1. cuántas sugerencias de OTRO cliente desaparecen — el defecto que se está arreglando
  2. cuántas filas pasan a CREATE — debería acercarse al 18% de clientes no seguidos
  3. cuántas sugerencias CORRECTAS se pierden por la ventana de ±72 h — debería ser ~0,
     porque el p99 del desfase real es 23,8 h. Si NO es ~0, la ventana está mal elegida y se
     revisa ANTES de desplegar.

    docker compose exec -T backend python scripts/diff_suggestions.py
"""

from app.database.connection import SessionLocal
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.operation_match_service import OperationMatchService


def main() -> None:
    db = SessionLocal()
    try:
        ids = [
            r[0]
            for r in db.query(WhatsAppIncomingPayment.id)
            .filter(WhatsAppIncomingPayment.is_irrelevant.is_(False))
            .order_by(WhatsAppIncomingPayment.id)
            .all()
        ]
        svc = OperationMatchService(db)
        nuevas = {i["payment_id"]: i for i in svc.suggest_for_payments(ids, "incoming")}

        crear = sum(1 for i in nuevas.values() if i["kind"] == "CREATE")
        otro_cliente = sum(1 for i in nuevas.values() if not i.get("same_client", True))
        abonos = sum(1 for i in nuevas.values() if i.get("coverage") == "PARTIAL")

        print(f"comprobantes            {len(ids)}")
        print(f"con sugerencia LINK     {len(nuevas) - crear}")
        print(f"  de las que abonan     {abonos}")
        print(f"  de OTRO cliente       {otro_cliente}   (debe ser 0)")
        print(f"proponen CREATE         {crear}   ({crear * 100 // max(1, len(ids))}%)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Comparar contra la sugerencia vieja**

Antes de desplegar, correr el script en la rama `main` (matcher viejo, guardando la salida en `/tmp/antes.txt`) y en la rama nueva (`/tmp/despues.txt`), y diffear. Para el punto 3 —sugerencias correctas perdidas por la ventana— la comprobación concreta es esta consulta, que cuenta los vínculos YA hechos y correctos que la ventana habría descartado:

```sql
SELECT count(*) FROM whatsapp_incoming_payments p
JOIN whatsapp_operations o ON o.id = p.whatsapp_operation_id
WHERE abs(EXTRACT(EPOCH FROM (p.created_at - o.created_at))) / 3600.0 > 72;
```

Si devuelve más de un puñado, sube `INCOMING_WINDOW_HOURS` y vuelve a medir.

- [ ] **Step 3: Commit**

```bash
git add scripts/diff_suggestions.py
git commit -m "chore: diff old vs new incoming suggestions against production receipts"
```

---

## Fuera del plan (lo decide el operador)

La **reparación del caso 582** (devolver el comprobante a José Bogao, crear su operación y engancharle el saliente #5603) y qué hacer con la **op 4644 de Arianna** están en el spec, sección «Migración de datos». No son código y tocan contabilidad de un día ya cerrado: se ejecutan a mano, con la API de prod, y con el operador delante.
