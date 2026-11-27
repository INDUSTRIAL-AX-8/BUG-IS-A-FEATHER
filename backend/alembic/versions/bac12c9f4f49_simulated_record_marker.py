"""simulated record marker

Revision ID: bac12c9f4f49
Revises: 40fc832634f0
Create Date: 2026-11-27 19:13:04.673762

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bac12c9f4f49'
down_revision: Union[str, Sequence[str], None] = '40fc832634f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 표를 다시 만들지 않고 ALTER TABLE ADD/DROP COLUMN 으로 바꾼다.
# batch 모드는 SQLite 에서 표를 복사·삭제·이름 변경하는데, 외래키를 켜 둔 상태에서 다른 표가 참조하는
# 행(LOT ← 공정 처리·계보 등)이 있으면 삭제 단계가 FOREIGN KEY constraint failed 로 실패한다 (데이터 있는 DB).
# SQLite 는 기본값이 있는 NOT NULL 열 추가(3.x)와 열 삭제(3.35+)를 직접 지원한다.


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('equipment_event', sa.Column('is_simulated', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('lot', sa.Column('is_simulated', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('lot', 'is_simulated')
    op.drop_column('equipment_event', 'is_simulated')
