"""
Diagnóstico manual de los buzones: `python -m app.cli.check_mailboxes`.

Es lo que se corre al agregar una cuenta nueva o cuando llega el aviso
"No puedo leer el correo de X". No escribe nada en la BD.
"""

from app.core.config import settings
from app.services.bank_email_imap import MailboxUnavailable, fetch_recent_headers
from app.services.bank_email_parsers import (
    authentication_ok,
    find_template,
    parse_bank_email,
)


def main() -> None:
    boxes = settings.mailboxes_computed
    if not boxes:
        print("❌ ZELLE_MAILBOXES vacío: la confirmación por correo está apagada.")
        return

    for box in boxes:
        print(f"\n=== {box.label} <{box.email}> ===")
        try:
            headers = fetch_recent_headers(box)
        except MailboxUnavailable as e:
            print(f"❌ {e}")
            continue

        print(f"✅ Conectado. {len(headers)} correos en las últimas 24 h.")
        found = 0
        for raw in headers:
            parsed = parse_bank_email(raw, box.label)
            if parsed is None:
                continue
            found += 1
            template = find_template(raw.from_addr)
            ok = template is not None and authentication_ok(raw.auth_results, template.auth_domain)
            mark = "✅" if ok else "🚫 (falla autenticación)"
            print(
                f"  {mark} {parsed.received_at:%d/%m %H:%M}  ${parsed.amount}  "
                f"{parsed.sender_name}  [{parsed.bank}]"
            )
        if found == 0:
            print("  (ninguna notificación de pago reconocida en las últimas 24 h)")


if __name__ == "__main__":
    main()
