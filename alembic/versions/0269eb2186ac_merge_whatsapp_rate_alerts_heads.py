"""merge whatsapp + rate_alerts heads

Revision ID: 0269eb2186ac
Revises: 7ebc69db4dbb, p3q4r5s6t7u8
Create Date: 2026-05-25 02:31:39.558946

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0269eb2186ac'
down_revision: Union[str, None] = ('7ebc69db4dbb', 'p3q4r5s6t7u8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
