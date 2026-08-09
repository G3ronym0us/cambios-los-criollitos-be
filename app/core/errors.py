"""
Traducción de los errores no controlados a una respuesta que el panel pueda leer.
"""

import logging

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class ServerErrorsAsJSON:
    """
    Convierte cualquier excepción sin atrapar en un 500 con cuerpo JSON.

    Starlette responde a esas excepciones desde su capa más externa, por fuera del
    middleware de CORS, así que la respuesta sale sin `Access-Control-Allow-Origin`: el
    navegador la bloquea y el `fetch` del panel falla como si el servidor no existiera. El
    operador ve "Error de conexión al servidor" cuando lo que hubo fue un error del
    servidor, y con ese mensaje no se puede depurar nada.

    Va montado POR DENTRO del CORS (se agrega antes que él en `main.py`) para que su
    respuesta salga por ahí y llegue con las cabeceras puestas. Es ASGI puro y no
    `BaseHTTPMiddleware` a propósito: el stream de notificaciones (SSE) atraviesa esta capa
    y no debe quedar bufferado.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def watched_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watched_send)
        except Exception:
            logger.exception(
                "Error no controlado en %s %s", scope.get("method"), scope.get("path")
            )
            # Con la respuesta ya empezada (un SSE a mitad de camino) no hay nada que
            # reemplazar: se deja subir para que Starlette corte la conexión.
            if started:
                raise
            response = JSONResponse(
                {"detail": "Error interno del servidor"}, status_code=500
            )
            await response(scope, receive, send)
