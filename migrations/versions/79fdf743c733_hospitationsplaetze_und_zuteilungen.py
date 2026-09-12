"""Hospitationsplaetze und Zuteilungen.

Eine Station kann sich in mehrere Plaetze aufteilen -- Klassen mit Raum fuer
die Unterrichtshospitation, Gruppen fuer die OGS. ``open_day_zuteilung`` haelt
fest, welche Familie an welcher Station auf welchem Platz sitzt; je Familie und
Station hoechstens einmal.

Wie schon in 30411aab72ae stehen hier nur die neuen Tabellen. Die
Autogenerierung hatte daneben aeltere Abweichungen zwischen Modell und Schema
gemeldet, die nicht aus dieser Aenderung stammen.

Revision ID: 79fdf743c733
Revises: 30411aab72ae
Create Date: 2026-09-12 10:06:39.613301

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '79fdf743c733'
down_revision = '30411aab72ae'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'open_day_platz',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('station_id', sa.Integer(), nullable=False),
        sa.Column('bezeichnung', sa.String(length=120), nullable=False),
        sa.Column('ort', sa.String(length=200), nullable=True),
        sa.Column('kapazitaet', sa.Integer(), nullable=True),
        sa.CheckConstraint('kapazitaet IS NULL OR kapazitaet > 0',
                           name='ck_open_day_platz_kapazitaet'),
        sa.ForeignKeyConstraint(['station_id'], ['open_day_station.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('station_id', 'bezeichnung', name='uq_open_day_platz'),
    )
    with op.batch_alter_table('open_day_platz', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_open_day_platz_station_id'),
                              ['station_id'], unique=False)

    op.create_table(
        'open_day_zuteilung',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('registration_id', sa.Integer(), nullable=False),
        sa.Column('station_id', sa.Integer(), nullable=False),
        sa.Column('platz_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['platz_id'], ['open_day_platz.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['registration_id'], ['open_day_registration.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['station_id'], ['open_day_station.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('registration_id', 'station_id', name='uq_open_day_zuteilung'),
    )
    with op.batch_alter_table('open_day_zuteilung', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_open_day_zuteilung_platz_id'),
                              ['platz_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_open_day_zuteilung_registration_id'),
                              ['registration_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_open_day_zuteilung_station_id'),
                              ['station_id'], unique=False)


def downgrade():
    with op.batch_alter_table('open_day_zuteilung', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_open_day_zuteilung_station_id'))
        batch_op.drop_index(batch_op.f('ix_open_day_zuteilung_registration_id'))
        batch_op.drop_index(batch_op.f('ix_open_day_zuteilung_platz_id'))
    op.drop_table('open_day_zuteilung')

    with op.batch_alter_table('open_day_platz', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_open_day_platz_station_id'))
    op.drop_table('open_day_platz')
