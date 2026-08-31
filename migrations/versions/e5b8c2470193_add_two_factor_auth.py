"""add two-factor authentication and login lockout

The application is going to be reachable from the internet, so a password
alone must not grant a session.
"""
from alembic import op
import sqlalchemy as sa

revision = "e5b8c2470193"
down_revision = "d3a17f60c948"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("totp_secret", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("totp_last_counter", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "recovery_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("recovery_code") as batch_op:
        batch_op.create_index(batch_op.f("ix_recovery_code_user_id"), ["user_id"], unique=False)


def downgrade():
    with op.batch_alter_table("recovery_code") as batch_op:
        batch_op.drop_index(batch_op.f("ix_recovery_code_user_id"))
    op.drop_table("recovery_code")
    with op.batch_alter_table("user") as batch_op:
        for name in ("last_login_at", "locked_until", "failed_logins",
                     "totp_last_counter", "totp_confirmed_at", "totp_secret"):
            batch_op.drop_column(name)
