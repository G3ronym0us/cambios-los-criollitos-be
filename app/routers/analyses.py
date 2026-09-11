"""
Router "Análisis" de cara al operador: qué leyó el bot en cada mensaje y qué dedujo.

Reemplaza el ir y venir de capturas de pantalla. Cuando una cotización sale mal, acá está el
texto exacto que llegó, la ventana de mensajes que el analizador tuvo en cuenta, lo que
dedujo de ella, y la operación que salió — sin depender de que alguien recuerde el mensaje ni
lo fotografíe.

Es de sólo lectura salvo por la etiqueta, que es la corrección a mano de los casos que el
join con la operación no alcanza a resolver.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_moderator_user
from app.database.connection import get_db
from app.models.user import User
from app.models.whatsapp_message_analysis import WhatsAppMessageAnalysis
from app.services.analysis_corpus_service import VERDICTS, AnalysisCorpusService
from app.services.analysis_digest_service import AnalysisDigestService
from app.services.analysis_log_service import AnalysisLogService

router = APIRouter(prefix="/analyses", tags=["Analyses"])


class AnalysisLabel(BaseModel):
    """La lectura correcta, con la misma forma que `output`."""
    label: dict
    source: str = "manual"


@router.get("")
def list_analyses(
    days: int = Query(7, ge=1, le=365),
    only_pending: bool = Query(
        False,
        description="Sólo lo que parecía una operación y no produjo ninguna: la cola de revisión",
    ),
    untracked: Optional[bool] = Query(None, description="Filtrar por números no trackeados"),
    phone: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Texto contenido en algún mensaje"),
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """
    El listado que se revisa desde el panel, del más reciente al más viejo.

    Se devuelve la ventana completa de mensajes y no sólo el último: leer una fila sin ella
    lleva a conclusiones equivocadas, porque el analizador decidió mirando todo el conjunto.
    """
    service = AnalysisCorpusService(db)
    rows = service.rows_since(days)

    # El filtrado se hace en Python y no en SQL a propósito: el veredicto es derivado y no
    # existe como columna, así que filtrar por él en la base exigiría materializarlo — y
    # entonces envejecería mal cada vez que cambie el criterio.
    if untracked is not None:
        rows = [r for r in rows if bool((r.context or {}).get("untracked")) == untracked]
    if phone:
        rows = [r for r in rows if phone in (r.client_phone or "")]
    if search:
        needle = search.lower()
        rows = [r for r in rows if any(needle in (m or "").lower() for m in (r.messages or []))]

    ops = service.operations_for(rows)
    items = [service.enrich(r, ops.get(r.wa_message_id) if r.wa_message_id else None) for r in rows]

    if only_pending:
        items = [
            it
            for it in items
            if it["operation"] is None and (it["context"] or {}).get("looks_transactional")
        ]

    items.reverse()  # más reciente primero, que es como se revisa
    total = len(items)
    return {"items": items[skip : skip + limit], "total": total, "skip": skip, "limit": limit}


@router.get("/stats")
def analyses_stats(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Recuento por veredicto y cuántos quedan por revisar."""
    service = AnalysisCorpusService(db)
    rows = service.rows_since(days)
    ops = service.operations_for(rows)

    por_veredicto: dict[str, int] = {}
    for row in rows:
        verdict = service.verdict(row, ops.get(row.wa_message_id) if row.wa_message_id else None)
        por_veredicto[verdict] = por_veredicto.get(verdict, 0) + 1

    return {
        "days": days,
        "total": len(rows),
        "by_verdict": por_veredicto,
        "verdict_meanings": VERDICTS,
        "pending_review": len(AnalysisDigestService(db).pending(hours=days * 24)),
    }


@router.get("/digest")
def analyses_digest(
    hours: int = Query(24, ge=1, le=720),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """El mismo resumen que sale por WhatsApp una vez al día, para verlo bajo demanda."""
    return {"hours": hours, "text": AnalysisDigestService(db).build_message(hours)}


@router.patch("/{analysis_uuid}/label")
def label_analysis(
    analysis_uuid: UUID,
    payload: AnalysisLabel,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """
    Corrige a mano lo que el join con la operación no resuelve.

    La etiqueta manda sobre lo derivado al armar el dataset: si alguien miró la fila, su
    lectura vale más que la inferencia.
    """
    row = AnalysisLogService(db).set_label(analysis_uuid, payload.label, payload.source)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado"
        )
    return row.dict()
