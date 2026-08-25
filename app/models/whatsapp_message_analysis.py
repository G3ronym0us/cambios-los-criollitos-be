"""
Bitácora del analizador de mensajes: qué leyó el bot y qué dedujo.

Es el corpus. Hasta ahora el bot parseaba el mensaje del cliente con la heurística de
`claude.ts` y tiraba el texto: la operación guarda el RESULTADO (monto, monedas, tasa) pero
nunca el mensaje que lo produjo, así que no había forma de medir cuántas veces la heurística
acertó ni con qué entrenar algo que la reemplace. Los únicos casos etiquetados eran los que
se escribían a mano en `src/test-cases/*.json` después de cada bug.

Cada fila es UNA corrida del analizador: la ventana de mensajes que vio, el contexto en que
la vio y lo que dedujo. No participa en ninguna decisión del bot — se escribe y se olvida.
Por eso el endpoint que la alimenta es fire-and-forget: si esto falla, la cotización sigue.

**Dónde aparece la etiqueta.** No se guarda ninguna al escribir, porque en ese momento nadie
sabe todavía si la lectura fue correcta: la verdad aparece DESPUÉS, en lo que el operador
hizo con la operación. El puente es `wa_message_id`, que ya mapea a operación por
`whatsapp_operation_messages`:

- op COMPLETED                              → la heurística acertó
- op cancelada `superseded_by_correction`   → leyó mal; la op que la reemplazó trae el bueno
- sin operación con intención QUOTE + monto → dedujo una cotización que nunca nació
- sin operación con intención CHAT          → el caso aburrido y correcto

`app/cli/export_analysis_corpus.py` hace ese join y emite el JSONL. `label` queda para
corregir a mano lo que el join no resuelve.

**Datos personales.** Guarda texto crudo de clientes. `app/cli/purge_analysis_log.py` borra
lo viejo; por defecto respeta las filas ya etiquetadas, que son el dataset revisado.
"""

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppMessageAnalysis(UUIDMixin, Base):
    __tablename__ = "whatsapp_message_analyses"

    id = Column(Integer, primary_key=True, index=True)

    # Id serializado del ÚLTIMO mensaje de la ventana — el que disparó el análisis. Es la
    # llave para reencontrar la operación que salió de aquí. Nullable porque no todo mensaje
    # trae id utilizable, y SIN unique: un mensaje editado se reanaliza y merece otra fila.
    wa_message_id = Column(String(255), nullable=True, index=True)
    client_phone = Column(String(64), nullable=False, index=True)

    # La ventana exacta que recibió el analizador: array de strings, el más viejo primero.
    # Es el input reproducible; sin él la fila no sirve para entrenar ni para un test.
    messages = Column(JSONB, nullable=False)
    # Quién produjo `output`. Cuando la cascada conviva con la heurística, dos filas del
    # mismo mensaje se distinguen por aquí (y su desacuerdo es la señal a mirar).
    analyzer = Column(String(32), nullable=False, server_default="heuristic-v1")
    # El AnalysisResult tal cual: intent, monedas, monto, amountSide, paymentInfo, etc.
    output = Column(JSONB, nullable=False)
    # Estado del bot al analizar (campos que la ficha esperaba, si el mensaje venía completo).
    # Sin esto varios casos son irreproducibles: la misma frase se lee distinto según qué
    # campo estuviera pendiente.
    context = Column(JSONB, nullable=True)
    # Par por defecto del cliente EN ESE MOMENTO. Lo rellena el backend al recibir la fila:
    # el bot tendría que pagar un request extra por mensaje para saberlo, y leerlo después
    # daría el par de hoy, no el de entonces.
    default_pair_symbol = Column(String(20), nullable=True)

    # Verdad corregida a mano, con la misma forma que `output`, para lo que el join con la
    # operación no alcanza a decidir.
    label = Column(JSONB, nullable=True)
    label_source = Column(String(16), nullable=True)  # operator | manual
    labeled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def dict(self):
        return {
            "uuid": str(self.uuid),
            "wa_message_id": self.wa_message_id,
            "client_phone": self.client_phone,
            "messages": self.messages,
            "analyzer": self.analyzer,
            "output": self.output,
            "context": self.context,
            "default_pair_symbol": self.default_pair_symbol,
            "label": self.label,
            "label_source": self.label_source,
            "labeled_at": self.labeled_at.isoformat() if self.labeled_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return (
            f"<WhatsAppMessageAnalysis({self.client_phone} "
            f"{(self.output or {}).get('intent')} via {self.analyzer})>"
        )
