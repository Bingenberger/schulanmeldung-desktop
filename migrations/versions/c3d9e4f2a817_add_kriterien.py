"""Frei anlegbare Kriterien für Diagnostik, Schulspiel und Schularzt

Die Beobachtungspunkte standen bisher als feste Spalten in ``diagnostik``,
``schulspiel_diagnostik`` und ``schulaerztliche_untersuchung``. Diese Spalten
bleiben stehen; beim ersten Öffnen eines Bogens legt die Anwendung den
vorbelegten Katalog an und übernimmt die Werte daraus
(sl_office.criteria.service.ensure_catalog).
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d9e4f2a817"
down_revision = "b5e0c7d1a923"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kriterium",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bogen", sa.String(length=20), nullable=False),
        sa.Column("gruppe", sa.String(length=120), nullable=False),
        sa.Column("bezeichnung", sa.String(length=200), nullable=False),
        sa.Column("kurz", sa.String(length=60), nullable=False),
        sa.Column("typ", sa.String(length=20), nullable=False),
        sa.Column("optionen", sa.Text(), nullable=False),
        sa.Column("pflicht", sa.Boolean(), nullable=False),
        sa.Column("in_wertung", sa.Boolean(), nullable=False),
        sa.Column("foerderhinweis", sa.Boolean(), nullable=False),
        sa.Column("reihenfolge", sa.Integer(), nullable=False),
        sa.Column("aktiv", sa.Boolean(), nullable=False),
        sa.Column("altfeld", sa.String(length=60), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kriterium_bogen", "kriterium", ["bogen"])
    op.create_table(
        "kriterium_wert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kriterium_id", sa.Integer(), nullable=False),
        sa.Column("schueler_id", sa.Integer(), nullable=False),
        sa.Column("wert", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["kriterium_id"], ["kriterium.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["schueler_id"], ["schueler.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kriterium_id", "schueler_id", name="uq_kriterium_wert_kind"),
    )
    op.create_index("ix_kriterium_wert_kriterium_id", "kriterium_wert", ["kriterium_id"])
    op.create_index("ix_kriterium_wert_schueler_id", "kriterium_wert", ["schueler_id"])
    with op.batch_alter_table("schulspiel_diagnostik") as batch:
        batch.add_column(sa.Column("gesamtwert_max", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("schulspiel_diagnostik") as batch:
        batch.drop_column("gesamtwert_max")
    op.drop_index("ix_kriterium_wert_schueler_id", table_name="kriterium_wert")
    op.drop_index("ix_kriterium_wert_kriterium_id", table_name="kriterium_wert")
    op.drop_table("kriterium_wert")
    op.drop_index("ix_kriterium_bogen", table_name="kriterium")
    op.drop_table("kriterium")
