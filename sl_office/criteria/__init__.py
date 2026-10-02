"""Frei anlegbare Kriterien für Diagnostik, Schulspiel und Schularzt.

Welche Punkte ein Bogen abfragt, legt jede Schule unter „Verwaltung →
Kriterien“ selbst fest. Fest bleiben nur die Angaben, an denen Abläufe hängen:
Gesamteindrücke, AO-SF-Verdacht, Rückstellung, Einladung zum Schulspiel,
Bemerkung und der eingescannte Bogen.
"""
from sl_office.criteria.models import Kriterium, KriteriumWert  # noqa: F401
