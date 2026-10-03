"""Gespeicherter Wortlaut der Elternbriefe."""

from models import db, utcnow


class Elternbrief(db.Model):
    """Redaktionell gepflegter Text des Elternanschreibens.

    Ohne Zeile gilt der Wortlaut der Schulvorlage aus
    :data:`sl_office.briefe.letters.DEFAULT_TEXT`; "Zurücksetzen"
    loescht die Zeile wieder. ``key`` laesst Platz fuer weitere Schreiben.
    """
    __tablename__ = "elternbrief"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), nullable=False, unique=True)
    titel = db.Column(db.Text, nullable=False)
    text = db.Column(db.Text, nullable=False)
    gruss = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow,
                           onupdate=utcnow)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
