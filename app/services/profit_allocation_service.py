"""
Reparto del margen de una operación.

La operación dice lo que se le cobró al cliente (`applied_percentage`); el reparto dice quién
se queda con qué parte. Hoy el caso corriente es uno solo —el fondo que atendió la operación,
por su porcentaje por defecto (8% cobrado, 7% al fondo)— y ese 7% se divide entre los socios
del fondo según su `profit_share_percentage` (Zelle: 50/50).

El modelo admite más de un destino porque el negocio ya los tiene: una ZELLE→BRL al 10% que
deja 8% en el fondo Zelle y 2% en el de Brasil, o 7% al fondo y 2% de vuelta al cliente que
hizo de intermediario. La suma no tiene por qué dar el margen cobrado: lo que sobra queda sin
asignar y lo que falta es una diferencia negativa que el operador acepta.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.fund import FundGroup, FundGroupMember
from app.models.profit_allocation import (
    OperationProfitAllocation,
    ProfitAllocationDestination,
)
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation


class ProfitAllocationService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- lectura ----------

    def allocations(self, op: WhatsAppOperation) -> list[OperationProfitAllocation]:
        return list(op.profit_allocations or [])

    def fund_percentage(self, op: WhatsAppOperation) -> float:
        """Cuánto del margen se queda el negocio (la suma de los destinos de tipo fondo)."""
        return sum(
            float(a.percentage or 0)
            for a in self.allocations(op)
            if a.destination_type == ProfitAllocationDestination.FUND
        )

    def unallocated_percentage(self, op: WhatsAppOperation) -> float:
        """
        Lo cobrado menos lo repartido. Positivo = margen sin destino; negativo = se repartió
        más de lo que se cobró (el caso que el operador aprueba).
        """
        charged = float(op.applied_percentage or 0)
        assigned = sum(float(a.percentage or 0) for a in self.allocations(op))
        return round(charged - assigned, 4)

    # ---------- escritura ----------

    def ensure_defaults(self, op: WhatsAppOperation) -> list[OperationProfitAllocation]:
        """
        Reparto por defecto de una operación que aún no lo tiene: todo al fondo que la
        atendió, por el porcentaje configurado en ese fondo. Nunca más de lo cobrado —dar más
        que el margen es una decisión explícita, no un default.

        Una operación sin fondo no reparte: su ganancia sigue siendo el margen cobrado, sin
        atribuir a nadie (es como se contabilizaba hasta ahora).
        """
        existing = self.allocations(op)
        if existing:
            return existing
        if op.fund_group_id is None:
            return []

        charged = float(op.applied_percentage or 0)
        if charged <= 0:
            return []

        group = self.db.query(FundGroup).filter(FundGroup.id == op.fund_group_id).first()
        if group is None:
            return []

        default = group.default_profit_percentage
        percentage = charged if default is None else min(float(default), charged)
        if percentage <= 0:
            return []

        allocation = OperationProfitAllocation(
            whatsapp_operation_id=op.id,
            destination_type=ProfitAllocationDestination.FUND,
            fund_group_id=group.id,
            percentage=percentage,
        )
        self.db.add(allocation)
        self.db.flush()
        self.db.refresh(op)
        return self.allocations(op)

    def set_allocations(
        self,
        op: WhatsAppOperation,
        specs: list[dict],
        actor: Optional[User] = None,
    ) -> list[OperationProfitAllocation]:
        """
        Reemplaza el reparto de una operación. Cada spec trae `destination_type`,
        `percentage` y el destino (`fund_group_uuid` o `client_uuid`).

        Repartir más de lo cobrado se permite —el operador lo acepta a sabiendas— y queda
        firmado en las filas nuevas.
        """
        rows: list[OperationProfitAllocation] = []
        now = datetime.now(timezone.utc)
        over_allocated = sum(float(s.get("percentage") or 0) for s in specs) > float(
            op.applied_percentage or 0
        )

        for spec in specs:
            destination = ProfitAllocationDestination(str(spec["destination_type"]).upper())
            percentage = float(spec["percentage"])
            if percentage <= 0:
                raise ValueError("Un destino con 0% no reparte nada: quítalo del reparto")

            fund_group_id = None
            client_id = None
            if destination == ProfitAllocationDestination.FUND:
                group = (
                    self.db.query(FundGroup)
                    .filter(FundGroup.uuid == str(spec["fund_group_uuid"]))
                    .first()
                )
                if group is None:
                    raise ValueError(f"Fondo {spec.get('fund_group_uuid')} no encontrado")
                fund_group_id = group.id
            else:
                client = (
                    self.db.query(WhatsAppClient)
                    .filter(WhatsAppClient.uuid == str(spec["client_uuid"]))
                    .first()
                )
                if client is None:
                    raise ValueError(f"Cliente {spec.get('client_uuid')} no encontrado")
                client_id = client.id

            rows.append(
                OperationProfitAllocation(
                    whatsapp_operation_id=op.id,
                    destination_type=destination,
                    fund_group_id=fund_group_id,
                    whatsapp_client_id=client_id,
                    percentage=percentage,
                    notes=spec.get("notes"),
                    approved_by_user_id=actor.id if (over_allocated and actor) else None,
                    approved_at=now if over_allocated else None,
                )
            )

        for old in self.allocations(op):
            self.db.delete(old)
        self.db.flush()
        for row in rows:
            self.db.add(row)
        self.db.flush()
        self.db.refresh(op)
        return self.allocations(op)

    def sync_amounts(self, op: WhatsAppOperation, value_usdt: Optional[float]) -> None:
        """Pone en cada destino cuánto le toca en USDT, según el valor actual de la operación."""
        base = float(value_usdt or 0)
        for allocation in self.allocations(op):
            allocation.amount_usdt = round(base * float(allocation.percentage or 0) / 100, 6)

    # ---------- reparto entre socios ----------

    def member_shares(self, op: WhatsAppOperation) -> list[tuple[User, float]]:
        """
        Qué porcentaje del VALOR le toca a cada socio: por cada fondo del reparto, su
        porcentaje repartido entre los miembros según su parte del fondo.

        Un fondo sin partes configuradas no genera reparto por socio: la ganancia queda en la
        transacción sin atribuir, que es mejor que inventarle un dueño.
        """
        shares: dict[int, tuple[User, float]] = {}
        for allocation in self.allocations(op):
            if allocation.destination_type != ProfitAllocationDestination.FUND:
                continue
            members = (
                self.db.query(FundGroupMember)
                .filter(FundGroupMember.group_id == allocation.fund_group_id)
                .all()
            )
            for member in members:
                share = float(member.profit_share_percentage or 0)
                if share <= 0 or member.user is None:
                    continue
                percentage = float(allocation.percentage) * share / 100
                current = shares.get(member.user_id)
                shares[member.user_id] = (
                    member.user,
                    (current[1] if current else 0) + percentage,
                )
        return [(user, round(pct, 6)) for user, pct in shares.values()]
