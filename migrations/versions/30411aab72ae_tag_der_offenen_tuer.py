"""Tag der offenen Tuer: Veranstaltung, Stationen, Rueckmeldungen.

Nur die drei neuen Tabellen. Die Autogenerierung hatte daneben aeltere
Abweichungen zwischen Modell und Schema aufgesammelt (Eindeutigkeit eines
Index, Spaltentypen, eine fehlende Fremdschluesselangabe); die stammen nicht
aus dieser Aenderung und bleiben deshalb unangetastet.

Revision ID: 30411aab72ae
Revises: e1b6d05a7c94
Create Date: 2026-09-09 15:21:33.524592

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '30411aab72ae'
down_revision = 'e1b6d05a7c94'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'open_day_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('school_year', sa.Integer(), nullable=False),
        sa.Column('titel', sa.String(length=200), nullable=False),
        sa.Column('datum', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('anmeldeschluss', sa.Date(), nullable=True),
        sa.Column('ort', sa.String(length=200), nullable=True),
        sa.Column('hinweise', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','published','closed')",
                           name='ck_open_day_event_status'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('open_day_event', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_open_day_event_school_year'),
                              ['school_year'], unique=False)
        batch_op.create_index('uq_open_day_event_published_year', ['school_year'], unique=True,
                              sqlite_where=sa.text("status = 'published'"),
                              postgresql_where=sa.text("status = 'published'"))

    op.create_table(
        'open_day_station',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('gruppe', sa.Integer(), nullable=False),
        sa.Column('art', sa.String(length=20), nullable=False),
        sa.Column('beginn', sa.Time(), nullable=False),
        sa.Column('ende', sa.Time(), nullable=False),
        sa.Column('ort', sa.String(length=200), nullable=True),
        sa.CheckConstraint("art IN ('fuehrung','unterricht','ogs')",
                           name='ck_open_day_station_art'),
        sa.CheckConstraint('ende > beginn', name='ck_open_day_station_period'),
        sa.CheckConstraint('gruppe IN (1,2)', name='ck_open_day_station_gruppe'),
        sa.ForeignKeyConstraint(['event_id'], ['open_day_event.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'gruppe', 'art', name='uq_open_day_station'),
    )
    with op.batch_alter_table('open_day_station', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_open_day_station_event_id'),
                              ['event_id'], unique=False)

    op.create_table(
        'open_day_registration',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=False),
        sa.Column('schueler_id', sa.Integer(), nullable=False),
        sa.Column('parent_access_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('teilnahme', sa.Boolean(), nullable=False),
        sa.Column('wunsch_fuehrung', sa.Boolean(), nullable=False),
        sa.Column('wunsch_unterricht', sa.Boolean(), nullable=False),
        sa.Column('wunsch_ogs', sa.Boolean(), nullable=False),
        sa.Column('gruppe', sa.Integer(), nullable=True),
        sa.Column('bemerkung', sa.Text(), nullable=True),
        sa.Column('plan_gesendet_am', sa.DateTime(timezone=True), nullable=True),
        sa.Column('plan_signatur', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('gruppe IS NULL OR gruppe IN (1,2)',
                           name='ck_open_day_registration_gruppe'),
        sa.ForeignKeyConstraint(['event_id'], ['open_day_event.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_access_id'], ['parent_access.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['schueler_id'], ['schueler.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'schueler_id', name='uq_open_day_registration_child'),
    )
    with op.batch_alter_table('open_day_registration', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_open_day_registration_event_id'),
                              ['event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_open_day_registration_schueler_id'),
                              ['schueler_id'], unique=False)


def downgrade():
    with op.batch_alter_table('open_day_registration', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_open_day_registration_schueler_id'))
        batch_op.drop_index(batch_op.f('ix_open_day_registration_event_id'))
    op.drop_table('open_day_registration')

    with op.batch_alter_table('open_day_station', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_open_day_station_event_id'))
    op.drop_table('open_day_station')

    with op.batch_alter_table('open_day_event', schema=None) as batch_op:
        batch_op.drop_index('uq_open_day_event_published_year',
                            sqlite_where=sa.text("status = 'published'"),
                            postgresql_where=sa.text("status = 'published'"))
        batch_op.drop_index(batch_op.f('ix_open_day_event_school_year'))
    op.drop_table('open_day_event')
