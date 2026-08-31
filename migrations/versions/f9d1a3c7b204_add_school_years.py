"""add enrolment years and assign existing students

Each enrolment year is worked on separately. Existing students all belong to
the year that was configured globally so far.
"""
from alembic import op
import sqlalchemy as sa

revision = "f9d1a3c7b204"
down_revision = "e5b8c2470193"
branch_labels = None
depends_on = None

FALLBACK_YEAR = 2026


def upgrade():
    connection = op.get_bind()
    current = connection.execute(
        sa.text("SELECT einschulungsjahr FROM global_settings LIMIT 1")
    ).scalar() or FALLBACK_YEAR

    op.create_table(
        "einschulungsjahr",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jahr", sa.Integer(), nullable=False),
        sa.Column("ist_aktuell", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gesperrt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("angelegt_am", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jahr"),
    )
    with op.batch_alter_table("einschulungsjahr") as batch_op:
        batch_op.create_index(batch_op.f("ix_einschulungsjahr_jahr"), ["jahr"], unique=False)

    connection.execute(
        sa.text("INSERT INTO einschulungsjahr (jahr, ist_aktuell, gesperrt, angelegt_am) "
                "VALUES (:jahr, 1, 0, CURRENT_TIMESTAMP)"),
        {"jahr": current},
    )

    # Add nullable first, backfill, then tighten -- SQLite cannot add a NOT NULL
    # column without a default to a populated table.
    with op.batch_alter_table("schueler") as batch_op:
        batch_op.add_column(sa.Column("einschulungsjahr", sa.Integer(), nullable=True))
    connection.execute(sa.text("UPDATE schueler SET einschulungsjahr = :jahr"), {"jahr": current})
    with op.batch_alter_table("schueler") as batch_op:
        batch_op.alter_column("einschulungsjahr", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(batch_op.f("ix_schueler_einschulungsjahr"), ["einschulungsjahr"], unique=False)


def downgrade():
    with op.batch_alter_table("schueler") as batch_op:
        batch_op.drop_index(batch_op.f("ix_schueler_einschulungsjahr"))
        batch_op.drop_column("einschulungsjahr")
    with op.batch_alter_table("einschulungsjahr") as batch_op:
        batch_op.drop_index(batch_op.f("ix_einschulungsjahr_jahr"))
    op.drop_table("einschulungsjahr")
