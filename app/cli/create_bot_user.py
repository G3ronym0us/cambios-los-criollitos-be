"""
Seed del service user que el bot de WhatsApp "encarna" para satisfacer
Transaction.user_id (FK NOT NULL). Idempotente.

El email se toma de settings.BOT_SERVICE_USER_EMAIL (.env). El bot nunca
inicia sesión con este usuario: se autentica con X-Bot-Token. La contraseña
es aleatoria sólo para satisfacer el hash NOT NULL.

Uso:
    python -m app.cli.create_bot_user            # crear / asegurar
    docker compose exec backend python -m app.cli.create_bot_user
"""

import secrets
import sys

from app.core.config import settings
from app.core.security import get_password_hash
from app.database.connection import SessionLocal
from app.enums.user_roles import UserRole
from app.models.user import User


def create_bot_user() -> User:
    email = getattr(settings, "BOT_SERVICE_USER_EMAIL", None)
    if not email:
        raise ValueError("BOT_SERVICE_USER_EMAIL no está configurado en el .env")

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            changed = False
            if not existing.is_active:
                existing.is_active = True
                changed = True
            if not existing.is_verified:
                existing.is_verified = True
                changed = True
            if changed:
                db.commit()
                print(f"✅ Bot service user actualizado (active/verified): {email}")
            else:
                print(f"✅ Bot service user ya existe: {email} (id={existing.id})")
            return existing

        username = email.split("@")[0]
        bot_user = User(
            username=username,
            email=email,
            full_name="WhatsApp Bot (service account)",
            phone_number=None,
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            role=UserRole.USER,
            is_active=True,
            is_verified=True,
            login_count=0,
            failed_login_attempts=0,
        )
        db.add(bot_user)
        db.commit()
        db.refresh(bot_user)
        print(f"✅ Bot service user creado: {email} (id={bot_user.id}, role=USER)")
        return bot_user
    finally:
        db.close()


if __name__ == "__main__":
    try:
        create_bot_user()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Error creando bot service user: {exc}")
        sys.exit(1)
