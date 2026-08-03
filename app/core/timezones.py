"""
Zona horaria del negocio y traducción de días de calendario a instantes.

Todo lo que el operador ve —horas de comprobantes, fechas de movimientos— se muestra en
Caracas, así que los filtros por fecha tienen que interpretarse ahí también: elegir
"31 jul" debe traer lo del 31 de julio venezolano, no lo de un 31 de julio UTC que empieza
a las ocho de la noche del 30.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

#: Venezuela no aplica horario de verano, así que el offset fijo es exacto.
CARACAS_TZ = timezone(timedelta(hours=-4))


def day_bounds(
    date_from: Optional[date], date_to: Optional[date]
) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Días de calendario → rango semiabierto `[inicio, fin)` en hora de Caracas.

    El día final entra COMPLETO: el rango se cierra en la medianoche siguiente, y por eso
    quien lo consuma debe comparar con `<` y no con `<=`.

    Los endpoints reciben estas fechas como `date` y no como `datetime` a propósito:
    pydantic v2 rechaza `yyyy-mm-dd` —que es lo que manda el front— si el tipo es datetime.
    """
    start = (
        datetime.combine(date_from, time.min, tzinfo=CARACAS_TZ) if date_from else None
    )
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=CARACAS_TZ)
        if date_to
        else None
    )
    return start, end
