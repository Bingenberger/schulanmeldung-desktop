"""Resolve stored files through their owning domain records.

This compatibility service protects the legacy filename-based download route.
The later Document model can replace these lookups without changing the route.
"""

from dataclasses import dataclass

from models import AOSF, Diagnostik, Rueckstellung, SchulaerztlicheUntersuchung, SchulspielDiagnostik


@dataclass(frozen=True)
class ManagedDocument:
    filename: str
    category: str
    student_id: int


DOCUMENT_FIELDS = (
    (Diagnostik, "pdf_dateiname", "diagnostik"),
    (SchulspielDiagnostik, "pdf_dateiname", "schulspiel"),
    (SchulaerztlicheUntersuchung, "pdf_dateiname", "schularzt"),
    (AOSF, "bericht_medizin_dateiname", "aosf_medizin"),
    (AOSF, "bericht_therapie_dateiname", "aosf_therapie"),
    (AOSF, "antrag_dateiname", "aosf_antrag"),
    (Rueckstellung, "bericht_medizin_dateiname", "rueckstellung_medizin"),
    (Rueckstellung, "bericht_therapie_dateiname", "rueckstellung_therapie"),
    (Rueckstellung, "elternschreiben_dateiname", "rueckstellung_eltern"),
)


def find_managed_document(filename: str) -> ManagedDocument | None:
    """Return ownership metadata only when a database record references a file."""
    for model, field_name, category in DOCUMENT_FIELDS:
        field = getattr(model, field_name)
        owner = model.query.filter(field == filename).first()
        if owner is not None:
            return ManagedDocument(filename, category, owner.schueler_id)
    return None
