
from flask import Flask, current_app, render_template, request, redirect, flash, url_for, send_from_directory, make_response
from models import db, Schueler, User, login_manager, Diagnostik, SchulaerztlicheUntersuchung, GlobalSettings, AOSF, Rueckstellung, KitaBericht, SchulspielDiagnostik
from forms import SchuelerForm, DiagnostikForm, SchularztForm, AOSFProzessForm, RueckstellungProzessForm, FreundeForm, KitaBerichtForm, SchulspielForm, BulkBetreuungForm, BulkFoerderkursForm
import datetime
from flask_login import login_required, current_user
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
import os
import sqlite3
import io
import pandas as pd
from flask import Flask, render_template, request, redirect, flash, url_for, send_from_directory, make_response, send_file
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.colors import black, lightgrey
from config import load_config
from security import init_security
from sl_office import school_year
from document_service import find_managed_document
from migrations_ext import migrate
from sl_office.auth import auth_bp
from sl_office.auth.routes import install_first_run_redirect
from sl_office.admin import admin_bp
from sl_office.students import students_bp
from sl_office.appointments import appointments_bp
from sl_office.maintenance import register_cli as register_maintenance_cli
from sl_office import features
from sl_office.criteria import service as criteria
from sl_office.criteria.routes import criteria_bp
import sl_office.criteria.models  # noqa: F401, E402
# Import new domain models so SQLAlchemy and Alembic include their metadata.
import sl_office.appointments.models  # noqa: F401, E402
import sl_office.briefe.models  # noqa: F401, E402
import sl_office.school_profile  # noqa: F401, E402
from sl_office.authorization import role_required
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.services.student_deletion import delete_student


_route_definitions = []


def route(rule, **options):
    """Collect routes until create_app can bind them to an app instance."""
    def decorator(view_function):
        _route_definitions.append((rule, options, view_function))
        return view_function
    return decorator

# --- SCHÜLER ROUTEN ---

@route('/')
@login_required
def index():
    # Statistiken berechnen
    count_total = Schueler.query.count()
    
    # Für Diagnostik (Abgeschlossen) und Schulspiel (Ja)
    # Join wäre sauberer, aber hier loop/filter ok für kleine Mengen oder query filter
    count_schulspiel = 0
    # count_missing_diag = 0
    # count_missing_arzt = 0
    
    # Einfacher via SQL count
    # Status ist direkt am Schueler gespeichert
    count_missing_diag = Schueler.query.filter(Schueler.diag_status != 'Abgeschlossen').count()
    count_missing_arzt = Schueler.query.filter(Schueler.arzt_status != 'Abgeschlossen').count()
    
    # Schulspiel ist in Diagnostik Tabelle
    # Wir zählen Diagnostiken wo schulspiel = True
    count_schulspiel = Diagnostik.query.filter_by(schulspiel=True).count()
    
    # Missing Schulspiel Diagnostik
    # All students where diagnostik.schulspiel == True and schueler.schulspiel_status != 'Abgeschlossen'
    schulspiel_required_diagnostics = Diagnostik.query.filter_by(schulspiel=True).all()
    count_missing_schulspiel = 0
    for d in schulspiel_required_diagnostics:
        student = Schueler.query.get(d.schueler_id)
        if student and student.schulspiel_status != 'Abgeschlossen':
            count_missing_schulspiel += 1

    stats = {
        'count_total': count_total,
        'count_schulspiel': count_schulspiel,
        'count_missing_schulspiel': count_missing_schulspiel,
        'count_missing_diag': count_missing_diag,
        'count_missing_arzt': count_missing_arzt
    }

    # AO-SF Statistiken
    # 1. Fälle (Status Fall oder Abgeschlossen in AOSF Tabelle)
    count_aosf_fall = AOSF.query.filter(AOSF.status.in_(['Fall', 'Abgeschlossen'])).count()
    
    # 2. Verdacht
    # Dazu gehören:
    # a) AOSF Einträge mit Status 'Verdacht'
    # b) Diagnostik.aosf_verdacht = True (wenn noch kein AOSF Eintrag existiert oder dieser Verdacht ist)
    # c) Schularzt.aosf_verdacht = True (...)
    
    # Simpler Ansatz: Alle Schüler ID sammeln, die Verdacht haben
    suspected_ids = set()
    
    # Aus AOSF Tabelle (Nur Verdacht)
    aosf_verdacht_entries = AOSF.query.filter_by(status='Verdacht').all()
    for e in aosf_verdacht_entries:
        suspected_ids.add(e.schueler_id)
        
    # Aus Diagnostik
    diag_verdacht = Diagnostik.query.filter_by(aosf_verdacht=True).all()
    for d in diag_verdacht:
        # Check ob schon ein "Fall" existiert, dann zählt es nicht mehr als bloßer Verdacht
        existing_case = AOSF.query.filter_by(schueler_id=d.schueler_id).filter(AOSF.status.in_(['Fall', 'Abgeschlossen'])).first()
        if not existing_case:
            suspected_ids.add(d.schueler_id)
            
    # Aus Schularzt
    arzt_verdacht = SchulaerztlicheUntersuchung.query.filter_by(aosf_verdacht=True).all()
    for a in arzt_verdacht:
        existing_case = AOSF.query.filter_by(schueler_id=a.schueler_id).filter(AOSF.status.in_(['Fall', 'Abgeschlossen'])).first()
        if not existing_case:
            suspected_ids.add(a.schueler_id)
            
    count_aosf_verdacht = len(suspected_ids)
    
    stats['count_aosf_fall'] = count_aosf_fall
    stats['count_aosf_verdacht'] = count_aosf_verdacht

    # Rückstellung Statistiken
    # Beschlossen (fallen aus der Statistik raus)
    count_rst_beschlossen = Rueckstellung.query.filter_by(status='Beschlossen').count()
    
    # Empfohlen / Prozess läuft
    # 1. Existierende Prozesse (nicht Beschlossen/Abgelehnt)
    rst_running_ids = set()
    running_entries = Rueckstellung.query.filter(Rueckstellung.status.in_(['Empfohlen', 'Prozess läuft'])).all()
    for r in running_entries:
        rst_running_ids.add(r.schueler_id)
        
    # 2. Nur Flag in Diagnostik (noch kein Prozess Eintrag)
    diag_empfohlen = Diagnostik.query.filter_by(rueckstellung_empfohlen=True).all()
    for d in diag_empfohlen:
        # Check ob Prozess existiert (egal welcher Status, denn wenn Prozess existiert, zählt dessen Status)
        existing_rst = Rueckstellung.query.filter_by(schueler_id=d.schueler_id).first()
        if not existing_rst:
            rst_running_ids.add(d.schueler_id)
            
    stats['count_rst_beschlossen'] = count_rst_beschlossen
    stats['count_rst_empfohlen'] = len(rst_running_ids)
    
    # Bereinige Gesamtstatistik
    # Schüler mit beschlossener Rückstellung zählen NICHT zur Gesamtanzahl für die Schuleinschreibung
    stats['count_total'] = count_total - count_rst_beschlossen
    
    return render_template('hub.html', stats=stats)



@route('/schueler/<int:id>/aosf', methods=['GET', 'POST'])
@login_required
@role_required(['Administrator', 'Schulleitung', 'Foerderlehrkraft', 'Sekretariat'])
def aosf_prozess(id):
    schueler = Schueler.query.get_or_404(id)
    aosf_entry = AOSF.query.filter_by(schueler_id=id).first()
    
    # Initialize form
    if aosf_entry:
        form = AOSFProzessForm(obj=aosf_entry)
        # Manuell select feld setzen falls nötig
    else:
        form = AOSFProzessForm()
    
    # Populate User Choices
    users = User.query.all()
    form.leitung_user_id.choices = [(u.id, f"{u.username} ({u.role})") for u in users]
    # Add empty choice
    form.leitung_user_id.choices.insert(0, (0, '-- Bitte wählen --'))

    if form.validate_on_submit():
        if not aosf_entry:
            aosf_entry = AOSF(schueler_id=id)
            db.session.add(aosf_entry)
        
        form.populate_obj(aosf_entry)
        
        
        # Handle '0' selection for user
        if form.leitung_user_id.data == 0:
            aosf_entry.leitung_user_id = None
        
        # --- File Uploads ---
        def save_aosf_file(file_data, prefix):
            if file_data:
                filename = secure_filename(f"aosf_{id}_{prefix}_{file_data.filename}")
                file_data.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                return filename
            return None

        if form.bericht_medizin.data:
            f = save_aosf_file(form.bericht_medizin.data, "medizin")
            if f: aosf_entry.bericht_medizin_dateiname = f

        if form.bericht_therapie.data:
            f = save_aosf_file(form.bericht_therapie.data, "therapie")
            if f: aosf_entry.bericht_therapie_dateiname = f
            
        if form.antrag_datei.data:
            f = save_aosf_file(form.antrag_datei.data, "antrag")
            if f: aosf_entry.antrag_dateiname = f

        # --- Status Logic assumes linear progression ---
        if aosf_entry.konferenz_ergebnis == 'Verfahren einleiten':
            aosf_entry.status = 'Fall'
            
        if aosf_entry.konferenz_ergebnis == 'Kein Verfahren':
            aosf_entry.status = 'kein Fall'
            
        # 2. Fall -> Abgeschlossen (Wenn Schulamt entschieden hat)
        if aosf_entry.schulamt_entscheidung in ['Stattgegeben', 'Abgelehnt']:
            aosf_entry.status = 'Abgeschlossen'
            
        try:
            db.session.commit()
            flash('AO-SF Prozessdaten aktualisiert.')
            return redirect(url_for('aosf_prozess', id=id))
        except Exception as e:
            db.session.rollback()
            flash(f'Fehler beim Speichern: {e}')

    return render_template('aosf_prozess.html', schueler=schueler, form=form, aosf=aosf_entry)


@route('/schueler/<int:id>/diagnostik', methods=['GET', 'POST'])
@login_required
def diagnostik(id):
    schueler = Schueler.query.get_or_404(id)
    diagnostik_entry = Diagnostik.query.filter_by(schueler_id=id).first()
    form = DiagnostikForm(obj=diagnostik_entry)
    werte = criteria.werte(id, 'diagnostik')

    if form.validate_on_submit():
        try:
            criteria.speichern(id, 'diagnostik', request.form)
        except criteria.Eingabefehler as fehler:
            flash("Bitte prüfen: " + "; ".join(fehler.fehler), 'error')
            werte = criteria.formular_werte('diagnostik', request.form)
        else:
            if not diagnostik_entry:
                diagnostik_entry = Diagnostik(schueler_id=id)
                db.session.add(diagnostik_entry)
            form.populate_obj(diagnostik_entry)
            diagnostik_entry.schulspiel = bool(form.schulspiel.data)
            schueler.diag_status = 'Abgeschlossen'
            if form.pdf_datei.data:
                file = form.pdf_datei.data
                filename = secure_filename(f"schueler_{id}_{file.filename}")
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                diagnostik_entry.pdf_dateiname = filename
            try:
                db.session.commit()
                flash('Diagnostik erfolgreich gespeichert!')
                return redirect(url_for('index'))
            except Exception as e:
                db.session.rollback()
                flash(f'Fehler beim Speichern: {e}')
    elif form.errors:
        flash(f"Fehler bei der Validierung: {form.errors}", 'error')
        werte = criteria.formular_werte('diagnostik', request.form)

    return render_template('diagnostik.html', form=form, schueler=schueler, diagnostik=diagnostik_entry,
                           gruppen=criteria.gruppiert(criteria.kriterien('diagnostik')), werte=werte)

@route('/schueler/<int:id>/schulspiel', methods=['GET', 'POST'])
@login_required
def schulspiel(id):
    schueler = Schueler.query.get_or_404(id)
    schulspiel_entry = SchulspielDiagnostik.query.filter_by(schueler_id=id).first()
    form = SchulspielForm(obj=schulspiel_entry)
    werte = criteria.werte(id, 'schulspiel')

    if form.validate_on_submit():
        try:
            criteria.speichern(id, 'schulspiel', request.form)
        except criteria.Eingabefehler as fehler:
            flash("Bitte prüfen: " + "; ".join(fehler.fehler), 'error')
            werte = criteria.formular_werte('schulspiel', request.form)
        else:
            if not schulspiel_entry:
                schulspiel_entry = SchulspielDiagnostik(schueler_id=id)
                db.session.add(schulspiel_entry)
            form.populate_obj(schulspiel_entry)
            db.session.flush()
            # Gesamtwert und erreichbarer Höchstwert hängen am Kriterienkatalog.
            summe, _, hoechstwert = criteria.wertung(id, 'schulspiel')
            schulspiel_entry.gesamtwert = summe
            schulspiel_entry.gesamtwert_max = hoechstwert
            if form.pdf_datei.data:
                file = form.pdf_datei.data
                filename = secure_filename(f"schulspiel_{id}_{file.filename}")
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                schulspiel_entry.pdf_dateiname = filename
            schueler.schulspiel_status = 'Abgeschlossen'
            try:
                db.session.commit()
                flash('Schulspiel-Diagnostik erfolgreich gespeichert!')
                return redirect(url_for('index'))
            except Exception as e:
                db.session.rollback()
                flash(f'Fehler beim Speichern: {e}')
    elif form.errors:
        flash(f"Fehler bei der Validierung: {form.errors}", 'error')
        werte = criteria.formular_werte('schulspiel', request.form)

    return render_template('schulspiel.html', form=form, schueler=schueler, schulspiel=schulspiel_entry,
                           gruppen=criteria.gruppiert(criteria.kriterien('schulspiel')), werte=werte)


@route('/schueler/<int:id>/freunde', methods=['GET', 'POST'])
@login_required
def freunde_bearbeiten(id):
    schueler = Schueler.query.get_or_404(id)
    form = FreundeForm(obj=schueler)
    
    # Populate Choices
    # Exclude current student
    all_schueler = Schueler.query.filter(Schueler.id != id).order_by(Schueler.nachname, Schueler.vorname).all()
    choices = [(s.id, f"{s.nachname}, {s.vorname} ({s.geburtsdatum.strftime('%d.%m.%Y') if s.geburtsdatum else ''})") for s in all_schueler]
    choices.insert(0, (0, '-- Keine Auswahl --'))
    
    form.freund1.choices = choices
    form.freund2.choices = choices
    
    # Handle GET request for select fields (because obj=schueler might not populate FKs directly to SelectField if names differ or coercion issues, usually it works if field name matches model attr)
    if request.method == 'GET':
        form.freund1.data = schueler.freund1_id if schueler.freund1_id else 0
        form.freund2.data = schueler.freund2_id if schueler.freund2_id else 0
        form.bemerkung.data = schueler.freunde_bemerkung

    if form.validate_on_submit():
        # Manual population because 0 needs to be None
        schueler.freund1_id = form.freund1.data if form.freund1.data != 0 else None
        schueler.freund1_negativ = form.freund1_negativ.data
        
        schueler.freund2_id = form.freund2.data if form.freund2.data != 0 else None
        schueler.freund2_negativ = form.freund2_negativ.data

        schueler.keine_freunde = form.keine_freunde.data
        
        schueler.freunde_bemerkung = form.bemerkung.data
        
        try:
            db.session.commit()
            flash(f'Freundeswünsche für {schueler.vorname} {schueler.nachname} gespeichert.')
            return redirect(url_for('students.list_students'))
        except Exception as e:
            db.session.rollback()
            flash(f'Fehler beim Speichern: {e}', 'error')
            
    return render_template('freunde.html', form=form, schueler=schueler)


@route('/schueler/<int:id>/schularzt', methods=['GET', 'POST'])
@login_required
def schularzt(id):
    schueler = Schueler.query.get_or_404(id)
    untersuchung = SchulaerztlicheUntersuchung.query.filter_by(schueler_id=id).first()
    form = SchularztForm(obj=untersuchung)
    werte = criteria.werte(id, 'schularzt')

    if form.validate_on_submit():
        try:
            criteria.speichern(id, 'schularzt', request.form)
        except criteria.Eingabefehler as fehler:
            flash("Bitte prüfen: " + "; ".join(fehler.fehler), 'error')
            werte = criteria.formular_werte('schularzt', request.form)
        else:
            if not untersuchung:
                untersuchung = SchulaerztlicheUntersuchung(schueler_id=id)
                db.session.add(untersuchung)
            form.populate_obj(untersuchung)
            if form.pdf_datei.data:
                file = form.pdf_datei.data
                base, ext = os.path.splitext(secure_filename(file.filename))
                filename = f"schularzt_{id}_{base}{ext}"
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                untersuchung.pdf_dateiname = filename
            schueler.arzt_status = 'Abgeschlossen'
            try:
                db.session.commit()
                flash('Schulärztliche Untersuchung gespeichert!')
                return redirect(url_for('index'))
            except Exception as e:
                db.session.rollback()
                flash(f'Fehler beim Speichern: {e}')
    elif form.errors:
        werte = criteria.formular_werte('schularzt', request.form)

    return render_template('schularzt.html', form=form, schueler=schueler, untersuchung=untersuchung,
                           gruppen=criteria.gruppiert(criteria.kriterien('schularzt')), werte=werte)

@route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    # Do not expose arbitrary files merely because their name is known.
    if find_managed_document(filename) is None:
        from flask import abort
        abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@route('/schueler/<int:id>/rueckstellung', methods=['GET', 'POST'])
@login_required
@role_required(['Administrator', 'Schulleitung', 'Foerderlehrkraft', 'Sekretariat'])
def rueckstellung_prozess(id):
    schueler = Schueler.query.get_or_404(id)
    rueckstellung_entry = Rueckstellung.query.filter_by(schueler_id=id).first()
    
    if rueckstellung_entry:
        form = RueckstellungProzessForm(obj=rueckstellung_entry)
    else:
        form = RueckstellungProzessForm()
        
    # Populate User Choices
    users = User.query.all()
    form.leitung_user_id.choices = [(u.id, f"{u.username} ({u.role})") for u in users]
    form.leitung_user_id.choices.insert(0, (0, '-- Bitte wählen --'))
    
    if form.validate_on_submit():
        if not rueckstellung_entry:
            rueckstellung_entry = Rueckstellung(schueler_id=id)
            db.session.add(rueckstellung_entry)
            
        form.populate_obj(rueckstellung_entry)
        
        if form.leitung_user_id.data == 0:
            rueckstellung_entry.leitung_user_id = None
            
        # File Handling
        def save_rst_file(file_data, prefix):
            if file_data:
                filename = secure_filename(f"rst_{id}_{prefix}_{file_data.filename}")
                file_data.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                return filename
            return None

        if form.bericht_medizin.data:
            f = save_rst_file(form.bericht_medizin.data, "medizin")
            if f: rueckstellung_entry.bericht_medizin_dateiname = f

        if form.bericht_therapie.data:
            f = save_rst_file(form.bericht_therapie.data, "therapie")
            if f: rueckstellung_entry.bericht_therapie_dateiname = f
            
        if form.elternschreiben_datei.data:
            f = save_rst_file(form.elternschreiben_datei.data, "eltern")
            if f: rueckstellung_entry.elternschreiben_dateiname = f
            
        # Status Logic
        if rueckstellung_entry.konferenz_ergebnis == 'Stattgegeben':
            rueckstellung_entry.status = 'Beschlossen'
        elif rueckstellung_entry.konferenz_ergebnis == 'Abgelehnt':
            rueckstellung_entry.status = 'Abgelehnt'
        elif rueckstellung_entry.status == 'Empfohlen':
             # Wenn gespeichert wird, aber noch keine Entscheidung, dann "Prozess läuft"
             rueckstellung_entry.status = 'Prozess läuft'
             
        try:
            db.session.commit()
            flash('Rückstellungs-Prozessdaten aktualisiert.')
            return redirect(url_for('rueckstellung_prozess', id=id))
        except Exception as e:
            db.session.rollback()
            flash(f'Fehler beim Speichern: {e}')
            
    return render_template('rueckstellung_prozess.html', schueler=schueler, form=form, rueckstellung=rueckstellung_entry)


@route('/schueler/<int:id>/kita_bericht', methods=['GET', 'POST'])
@login_required
def kita_bericht(id):
    schueler = Schueler.query.get_or_404(id)
    bericht = KitaBericht.query.filter_by(schueler_id=id).first()
    
    if bericht:
        form = KitaBerichtForm(obj=bericht)
    else:
        form = KitaBerichtForm()
        
    if form.validate_on_submit():
        if not bericht:
            bericht = KitaBericht(schueler_id=id)
            db.session.add(bericht)
            
        form.populate_obj(bericht)
        
        try:
            db.session.commit()
            flash('Kita-Bericht gespeichert.')
            return redirect(url_for('students.list_students'))
        except Exception as e:
            db.session.rollback()
            flash(f'Fehler beim Speichern: {e}', 'error')
            
    return render_template('kita_bericht.html', form=form, schueler=schueler)

@route('/export/pdf/cards')
@login_required
def export_cards():
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Dimensions (Slightly reduced to ensure print safety)
    box_width = 19 * cm
    box_height = 9 * cm
    margin_left = (width - box_width) / 2
    
    # y positions for 3 cards
    y_positions = [
        height - 1.5*cm - 9*cm, 
        height - 1.5*cm - 18.2*cm,
        height - 1.5*cm - 27.5*cm
    ]
    
    # Colors
    col_green_dark = (0.2, 0.6, 0.2)
    col_green_light = (0.6, 0.9, 0.6)
    col_orange = (1.0, 0.8, 0.2)
    col_red = (0.9, 0.3, 0.3)
    col_grey = (0.9, 0.9, 0.9)
    col_blue_light = (0.8, 0.9, 1.0)
    col_yellow_light = (1.0, 1.0, 0.8)
    
    # Sex Colors (Light pastel)
    col_sex_m = (0.85, 0.92, 1.0) # Light Blue
    col_sex_w = (1.0, 0.70, 0.80) # Stronger Light Pink
    col_sex_d = (0.85, 1.0, 0.85) # Light Green
    col_sex_u = (1.0, 1.0, 1.0)   # White
    
    SCORE_COLORS = {
        3: col_green_dark, 
        2: col_green_light, 
        1: col_orange, 
        0: col_red
    }
    SCORE_TEXT = {3: '++', 2: '+', 1: 'o', 0: '-', None: '-'}

    def draw_badge(c, x, y, text, bg_color, width=1.2*cm, height=0.6*cm, text_size=10, bold=True):
        c.saveState()
        if bg_color:
            c.setFillColorRGB(*bg_color)
            c.roundRect(x, y, width, height, 2, stroke=0, fill=1)
        
        c.setFillColor(black)
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, text_size)
        
        # Center text roughly
        text_width = c.stringWidth(str(text), font, text_size)
        text_x = x + (width - text_width) / 2
        text_y = y + (height - text_size) / 2 + 1.5 # minor visual adjust
        
        c.drawString(text_x, text_y, str(text))
        c.restoreState()

    # Fetch students
    schueler_list = Schueler.query.order_by(Schueler.nachname, Schueler.vorname).all()
    
    for i, s in enumerate(schueler_list):
        if i > 0 and i % 3 == 0:
            c.showPage()
            
        pos_idx = i % 3
        y_base = y_positions[pos_idx] # Bottom of the box
        
        # Draw Card Frame
        c.setStrokeColor(black)
        c.setLineWidth(1)
        
        # Header Background
        header_height = 1.6*cm
        header_y_bottom = y_base + box_height - header_height
        
        bg_col = col_sex_u
        if s.geschlecht == 'm': bg_col = col_sex_m
        elif s.geschlecht == 'w': bg_col = col_sex_w
        elif s.geschlecht == 'd': bg_col = col_sex_d
        
        c.setFillColorRGB(*bg_col)
        c.rect(margin_left, header_y_bottom, box_width, header_height, stroke=1, fill=1)
        
        # Body Background (None/White)
        c.setFillColorRGB(1,1,1)
        c.rect(margin_left, y_base, box_width, box_height - header_height, stroke=1, fill=0)
        
        c.setFillColor(black)
        
        # --- Header ---
        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin_left + 0.5*cm, y_base + box_height - 1.2*cm, f"{s.nachname}, {s.vorname}")
        
        c.setFont("Helvetica", 11)
        dob_str = s.geburtsdatum.strftime('%d.%m.%Y') if s.geburtsdatum else '-'
        c.drawString(margin_left + 10*cm, y_base + box_height - 1.2*cm, f"Geb: {dob_str}")
        c.drawString(margin_left + 14*cm, y_base + box_height - 1.2*cm, f"Kita: {s.kita or '-'}")
        
        # Separator
        c.setLineWidth(0.5)
        c.line(margin_left + 0.5*cm, y_base + box_height - 1.6*cm, margin_left + box_width - 0.5*cm, y_base + box_height - 1.6*cm)
        
        # --- Content ---
        line_start_y = y_base + box_height - 2.5*cm
        line_height = 0.9*cm
        
        # Row 1: Diagnostik
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin_left + 0.5*cm, line_start_y, "Diagnostik:")
        
        diag = s.diagnostik
        # Kognitiv Badge
        dk = diag.gesamteindruck_kognitiv if diag else None
        draw_badge(c, margin_left + 4*cm, line_start_y - 0.1*cm, "Kognitiv: " + SCORE_TEXT.get(dk, '-'), SCORE_COLORS.get(dk, col_grey), width=3.5*cm)
        
        # Verhalten Badge
        dv = diag.gesamteindruck_verhalten if diag else None
        draw_badge(c, margin_left + 8*cm, line_start_y - 0.1*cm, "Verhalten: " + SCORE_TEXT.get(dv, '-'), SCORE_COLORS.get(dv, col_grey), width=3.5*cm)

        # Schulspiel Badge
        schulspiel = s.schulspiel_diagnostik
        ds = schulspiel.gesamttendenz if schulspiel else None
        draw_badge(c, margin_left + 12*cm, line_start_y - 0.1*cm, "Schulspiel: " + SCORE_TEXT.get(ds, '-'), SCORE_COLORS.get(ds, col_grey), width=3.8*cm)

        # Row 2: Schularzt & Kita (Combined Line or separate?)
        # Let's put Schularzt and Kita on same line or next line. 
        # Line 2: Schularzt
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin_left + 0.5*cm, line_start_y - line_height, "Schularzt:")
        
        arzt = s.schularzt_untersuchung
        da = arzt.gesamteinschaetzung if arzt else None
        draw_badge(c, margin_left + 4*cm, line_start_y - line_height - 0.1*cm, SCORE_TEXT.get(da, '-'), SCORE_COLORS.get(da, col_grey), width=1.5*cm)
        
        # Add Kita to Line 2 (Right side)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin_left + 7*cm, line_start_y - line_height, "Kita:")
        
        kita = s.kita_bericht
        # Kita Cog
        kc = kita.kognitiv if kita else None
        draw_badge(c, margin_left + 8.5*cm, line_start_y - line_height - 0.1*cm, "K: " + SCORE_TEXT.get(kc, '-'), SCORE_COLORS.get(kc, col_grey), width=1.5*cm)
        # Kita Ver
        kv = kita.verhalten if kita else None
        draw_badge(c, margin_left + 10.2*cm, line_start_y - line_height - 0.1*cm, "V: " + SCORE_TEXT.get(kv, '-'), SCORE_COLORS.get(kv, col_grey), width=1.5*cm)

        # Row 3: Status
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin_left + 0.5*cm, line_start_y - 2*line_height, "Status:")
        
        bx = 4*cm
        if s.kann_kind:
            draw_badge(c, margin_left + bx, line_start_y - 2*line_height - 0.1*cm, "Kann-Kind", col_blue_light, width=2.5*cm)
            bx += 2.7*cm
        if s.has_aosf_verdacht:
            draw_badge(c, margin_left + bx, line_start_y - 2*line_height - 0.1*cm, "AO-SF", col_orange, width=2.0*cm)
            bx += 2.2*cm
        if s.has_rueckstellung_empfohlen:
            draw_badge(c, margin_left + bx, line_start_y - 2*line_height - 0.1*cm, "Rückstellung", col_grey, width=2.8*cm)
            bx += 3.0*cm
        if s.betreuung == 'OGS':
            draw_badge(c, margin_left + bx, line_start_y - 2*line_height - 0.1*cm, "OGS", (0.1, 0.43, 0.1), width=1.6*cm)
        elif s.betreuung == 'ÜMI':
            draw_badge(c, margin_left + bx, line_start_y - 2*line_height - 0.1*cm, "ÜMI", (0.42, 0.05, 0.68), width=1.6*cm)
        elif s.betreuung == 'Abholung':
            draw_badge(c, margin_left + bx, line_start_y - 2*line_height - 0.1*cm, "Abholung", col_grey, width=2.2*cm)
            
        # Row 4: Förderung (New)
        needs = criteria.foerderhinweise(s.id) if arzt else []

        if needs:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin_left + 0.5*cm, line_start_y - 3*line_height + 0.2*cm, "Förderung:")
            bx = 3.2*cm
            c.setFont("Helvetica", 9)
            # Draw as comma separated string to save space? Or badges? 
            # Badges might be too much if many. String is safer.
            needs_str = ", ".join(needs)
            # Truncate if too long?
            c.drawString(margin_left + bx, line_start_y - 3*line_height + 0.2*cm, needs_str)


        # Separator for Friends
        friend_div_y = line_start_y - 2.8*line_height
        c.setLineWidth(0.5)
        c.line(margin_left + 0.5*cm, friend_div_y, margin_left + box_width - 0.5*cm, friend_div_y)
        
        # --- Friends ---
        friend_start_y = friend_div_y - 0.6*cm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(margin_left + 0.5*cm, friend_start_y, "Freunde:")
        
        c.setFont("Helvetica", 11)
        f1 = s.freund1
        f1_neg = s.freund1_negativ
        t1 = f"1. {f1.vorname} {f1.nachname}" if f1 else "1. -"
        if f1 and f1_neg:
             c.setFillColorRGB(0.8, 0, 0) # Red text for negative
             t1 += " (NICHT)"
        c.drawString(margin_left + 3*cm, friend_start_y, t1)
        c.setFillColor(black)
        
        f2 = s.freund2
        f2_neg = s.freund2_negativ
        t2 = f"2. {f2.vorname} {f2.nachname}" if f2 else "2. -"
        
        # Same line or next line? Next line is safer
        if f2 and f2_neg:
             c.setFillColorRGB(0.8, 0, 0)
             t2 += " (NICHT)"
        c.drawString(margin_left + 10*cm, friend_start_y, t2)
        c.setFillColor(black)
        
        # Comments (very small at bottom)
        note_y = friend_start_y - 0.6*cm
        if s.freunde_bemerkung:
            c.setFont("Helvetica-Oblique", 9)
            note = s.freunde_bemerkung.replace('\n', ' ')
            if len(note) > 110: note = note[:110] + "..."
            c.drawString(margin_left + 0.5*cm, note_y, f"Note: {note}")
            note_y -= 0.5*cm
            
        # Kita Bemerkung (Below Friends Note)
        if kita and kita.bemerkung:
             c.setFont("Helvetica-Oblique", 9)
             c.setFillColor(black)
             k_note = kita.bemerkung.replace('\n', ' ')
             if len(k_note) > 110: k_note = k_note[:110] + "..."
             c.drawString(margin_left + 0.5*cm, note_y, f"Kita: {k_note}")

    c.save()
    buffer.seek(0)
    
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=schueler_karten.pdf'
    return response

# --- KLASSENZUSAMMENSETZUNG ---

def get_klassen_names(settings):
    """Gibt Liste der Klassennamen zurück, z.B. ['1a','1b','1c']."""
    letters = 'abcde'
    n = settings.anzahl_klassen if settings else 3
    return [f'1{letters[i]}' for i in range(n)]


@route('/klassen', methods=['GET'])
@login_required
def klassenzusammensetzung():
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)
    alle_schueler = Schueler.query.order_by(Schueler.nachname, Schueler.vorname).all()

    # Gruppen bilden
    gruppen = {k: [] for k in klassen_namen}
    gruppen[''] = []  # nicht zugeteilt
    for s in alle_schueler:
        key = s.klasse if s.klasse in klassen_namen else ''
        gruppen[key].append(s)

    # Statistik pro Klasse
    def klasse_stats(schueler_list):
        m = sum(1 for s in schueler_list if s.geschlecht == 'm')
        w = sum(1 for s in schueler_list if s.geschlecht == 'w')
        kann = sum(1 for s in schueler_list if s.kann_kind)
        aosf = sum(1 for s in schueler_list if s.has_aosf_verdacht)
        rst = sum(1 for s in schueler_list if s.has_rueckstellung_empfohlen)
        ogs = sum(1 for s in schueler_list if s.betreuung == 'OGS')
        uemi = sum(1 for s in schueler_list if s.betreuung == 'ÜMI')
        abholung = sum(1 for s in schueler_list if s.betreuung == 'Abholung')
        return {'gesamt': len(schueler_list), 'm': m, 'w': w, 'kann': kann,
                'aosf': aosf, 'rst': rst, 'ogs': ogs, 'uemi': uemi, 'abholung': abholung}

    stats = {k: klasse_stats(v) for k, v in gruppen.items()}

    return render_template('klassen.html',
                           klassen_namen=klassen_namen,
                           gruppen=gruppen,
                           stats=stats)


@route('/api/klassen/assign', methods=['POST'])
@login_required
def klassen_assign():
    """AJAX-Endpunkt: speichert Klassenzuweisung für ein Kind."""
    from flask import jsonify
    data = request.get_json()
    schueler_id = data.get('schueler_id')
    klasse = data.get('klasse', '').strip()

    s = Schueler.query.get(schueler_id)
    if not s:
        return jsonify({'ok': False, 'error': 'Schüler nicht gefunden'}), 404

    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)
    s.klasse = klasse if klasse in klassen_namen else None
    db.session.commit()
    return jsonify({'ok': True})


@route('/export/pdf/klassenmappe')
@login_required
def export_klassenmappe():
    """Exportiert die Klassenmappe: für jeden Schüler genau 2 A4-Seiten."""
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle
    from reportlab.lib import colors as rl_colors

    klasse_filter = request.args.get('klasse')
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)

    if klasse_filter and klasse_filter in klassen_namen:
        schueler_list = Schueler.query.filter_by(klasse=klasse_filter).order_by(Schueler.nachname, Schueler.vorname).all()
    else:
        schueler_list = Schueler.query.filter(Schueler.klasse.in_(klassen_namen)).order_by(Schueler.klasse, Schueler.nachname).all()

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    SCORE_TEXT = {3: '++', 2: '+', 1: 'o', 0: '-', None: '-'}
    SCORE_RGB = {3: (0.2, 0.6, 0.2), 2: (0.6, 0.9, 0.6), 1: (1.0, 0.75, 0.1), 0: (0.9, 0.3, 0.3), None: (0.85, 0.85, 0.85)}

    margin = 1.8 * cm
    inner_w = page_w - 2 * margin

    def draw_header_bar(c, schueler, page_num):
        """Zeichnet die Kopfzeile beider Seiten."""
        c.setFillColorRGB(0.0, 0.33, 0.65)
        c.rect(margin, page_h - margin - 1.4*cm, inner_w, 1.4*cm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont('Helvetica-Bold', 14)
        name = f"{schueler.nachname}, {schueler.vorname}"
        c.drawString(margin + 0.3*cm, page_h - margin - 0.95*cm, name)
        c.setFont('Helvetica', 10)
        dob = schueler.geburtsdatum.strftime('%d.%m.%Y') if schueler.geburtsdatum else '-'
        klasse_str = f"Klasse {schueler.klasse}" if schueler.klasse else ''
        right_text = f"Geb: {dob}  |  Kita: {schueler.kita or '-'}  |  {klasse_str}  |  Seite {page_num}/2"
        tw = c.stringWidth(right_text, 'Helvetica', 10)
        c.drawString(margin + inner_w - tw - 0.3*cm, page_h - margin - 0.95*cm, right_text)

    def draw_section_title(c, y, title):
        c.setFillColorRGB(0.93, 0.93, 0.93)
        c.rect(margin, y - 0.05*cm, inner_w, 0.6*cm, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(margin + 0.3*cm, y + 0.08*cm, title)
        return y - 0.7*cm

    def draw_badge(c, x, y, text, rgb, w=1.5*cm, h=0.55*cm):
        c.setFillColorRGB(*rgb)
        c.roundRect(x, y, w, h, 2, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont('Helvetica-Bold', 9)
        tw = c.stringWidth(text, 'Helvetica-Bold', 9)
        c.drawString(x + (w - tw) / 2, y + 0.14*cm, text)

    def draw_text_block(c, x, y, text, font='Helvetica-Oblique', size=8, max_w=None, line_h=None, color=(0.3, 0.3, 0.3)):
        """Zeichnet mehrzeiligen Text mit Zeilenumbruch und Wortwrapping."""
        if not text:
            return y
        if max_w is None:
            max_w = inner_w - 0.4*cm
        if line_h is None:
            line_h = size * 0.045 * cm  # ~pt to cm
        c.setFont(font, size)
        c.setFillColorRGB(*color)
        for paragraph in text.split('\n'):
            paragraph = paragraph.strip()
            words = paragraph.split(' ') if paragraph else ['']
            line = ''
            for word in words:
                test = (line + ' ' + word).strip()
                if c.stringWidth(test, font, size) <= max_w:
                    line = test
                else:
                    if line:
                        c.drawString(x, y, line)
                        y -= line_h
                    line = word
            if line:
                c.drawString(x, y, line)
                y -= line_h
        c.setFillColorRGB(0, 0, 0)
        return y

    def draw_two_col_items(c, y, items_values, col_labels=None):
        """Zeichnet Items in 2 Spalten mit Score-Badge. items_values: list of (label, value)."""
        col_w = inner_w / 2 - 0.3*cm
        row_h = 0.52*cm
        for idx, (label, val) in enumerate(items_values):
            col = idx % 2
            x = margin + col * (inner_w / 2 + 0.3*cm)
            if col == 0 and idx > 0:
                y -= row_h
            if idx == 0:
                y -= row_h
            # Alternating row shade
            if (idx // 2) % 2 == 0:
                c.setFillColorRGB(0.97, 0.97, 0.97)
                c.rect(x, y - 0.05*cm, col_w, row_h, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
            c.setFont('Helvetica', 9)
            c.drawString(x + 0.2*cm, y + 0.1*cm, label)
            if isinstance(val, str):
                # Kriterien, die keine Skala sind (Auswahl, Text, ...), als Text.
                c.setFont('Helvetica-Bold', 8)
                c.drawRightString(x + col_w - 0.2*cm, y + 0.1*cm, val or '-')
                continue
            badge_x = x + col_w - 1.6*cm
            draw_badge(c, badge_x, y - 0.03*cm, SCORE_TEXT[val], SCORE_RGB[val], w=1.4*cm, h=0.52*cm)
        # After last row
        if items_values:
            last_col = (len(items_values) - 1) % 2
            if last_col == 0:  # odd number of items, last item in left col
                y -= row_h
        return y - 0.2*cm

    # ===== DECKBLATT =====
    def draw_deckblatt(c, sl, klasse_label):
        total = len(sl)
        titel = f"Klassenmappe – Klasse {klasse_label}" if klasse_label else "Gesamtübersicht – Einschulungsjahrgang"

        # --- Statistiken berechnen ---
        m = sum(1 for s in sl if s.geschlecht == 'm')
        w = sum(1 for s in sl if s.geschlecht == 'w')
        d_other = total - m - w

        ogs      = sum(1 for s in sl if s.betreuung == 'OGS')
        uemi     = sum(1 for s in sl if s.betreuung == 'ÜMI')
        abholung = sum(1 for s in sl if s.betreuung == 'Abholung')
        betr_unbek = total - ogs - uemi - abholung

        kann = sum(1 for s in sl if s.kann_kind)
        aosf = sum(1 for s in sl if s.has_aosf_verdacht)
        rst  = sum(1 for s in sl if s.has_rueckstellung_empfohlen)

        def score_dist(values):
            d = {3: 0, 2: 0, 1: 0, 0: 0, None: 0}
            for v in values:
                d[v if v in d else None] += 1
            return d

        diag_kog = score_dist([s.diagnostik.gesamteindruck_kognitiv if s.diagnostik else None for s in sl])
        diag_ver = score_dist([s.diagnostik.gesamteindruck_verhalten if s.diagnostik else None for s in sl])
        diag_erfasst = sum(1 for s in sl if s.diagnostik)

        spiel_list = [s for s in sl if s.schulspiel_diagnostik]
        spiel_scores = score_dist([s.schulspiel_diagnostik.gesamttendenz for s in spiel_list])

        arzt_list = [s for s in sl if s.schularzt_untersuchung]
        arzt_scores = score_dist([s.schularzt_untersuchung.gesamteinschaetzung if s.schularzt_untersuchung else None for s in sl])

        # --- Zeichenhilfen ---
        SCORE_COLORS_BAR = {
            3: (0.18, 0.55, 0.18),
            2: (0.55, 0.82, 0.55),
            1: (1.0,  0.72, 0.1),
            0: (0.88, 0.28, 0.28),
            None: (0.82, 0.82, 0.82),
        }

        def pct(n): return f"{round(100*n/total)}%" if total else "0%"

        def stat_box(c, x, y, w, h, label, value, sub='', bg=(0.95, 0.95, 0.95)):
            c.setFillColorRGB(*bg)
            c.roundRect(x, y, w, h, 4, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
            c.setFont('Helvetica-Bold', 18)
            tw = c.stringWidth(str(value), 'Helvetica-Bold', 18)
            c.drawString(x + (w - tw) / 2, y + h * 0.45, str(value))
            c.setFont('Helvetica', 8)
            lw = c.stringWidth(label, 'Helvetica', 8)
            c.drawString(x + (w - lw) / 2, y + h * 0.75, label)
            if sub:
                c.setFont('Helvetica', 7)
                c.setFillColorRGB(0.4, 0.4, 0.4)
                sw = c.stringWidth(sub, 'Helvetica', 7)
                c.drawString(x + (w - sw) / 2, y + h * 0.2, sub)
                c.setFillColorRGB(0, 0, 0)

        def stacked_bar(c, x, y, bar_w, bar_h, dist, scores_order, show_none=True):
            """Zeichnet einen horizontal gestapelten Balken."""
            n = sum(dist[k] for k in scores_order) + (dist.get(None, 0) if show_none else 0)
            if n == 0:
                c.setFillColorRGB(0.9, 0.9, 0.9)
                c.rect(x, y, bar_w, bar_h, fill=1, stroke=0)
                return
            cx = x
            order = list(scores_order) + ([None] if show_none else [])
            for key in order:
                count = dist.get(key, 0)
                if count == 0:
                    continue
                seg_w = bar_w * count / n
                c.setFillColorRGB(*SCORE_COLORS_BAR[key])
                c.rect(cx, y, seg_w, bar_h, fill=1, stroke=0)
                # Label im Segment wenn breit genug
                label_text = f"{SCORE_TEXT[key]}: {count}"
                lw = c.stringWidth(label_text, 'Helvetica', 7)
                if seg_w > lw + 0.3*cm:
                    c.setFillColorRGB(0, 0, 0)
                    c.setFont('Helvetica', 7)
                    c.drawString(cx + (seg_w - lw)/2, y + (bar_h - 7)/2, label_text)
                cx += seg_w
            c.setStrokeColorRGB(0.5, 0.5, 0.5)
            c.setLineWidth(0.3)
            c.rect(x, y, bar_w, bar_h, fill=0, stroke=1)
            c.setFillColorRGB(0, 0, 0)

        def legend_row(c, x, y, dist, scores_order, show_none=True):
            """Legende unter dem Balken."""
            c.setFont('Helvetica', 8)
            bx = x
            order = list(scores_order) + ([None] if show_none else [])
            for key in order:
                count = dist.get(key, 0)
                label = f"{SCORE_TEXT[key]}: {count}"
                # mini farbiges Quadrat
                c.setFillColorRGB(*SCORE_COLORS_BAR[key])
                c.rect(bx, y + 1, 0.35*cm, 0.35*cm, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(bx + 0.42*cm, y + 2, label)
                bx += c.stringWidth(label, 'Helvetica', 8) + 0.7*cm

        # --- Seite aufbauen ---
        # Blauer Kopf
        c.setFillColorRGB(0.0, 0.33, 0.65)
        c.rect(margin, page_h - margin - 2.2*cm, inner_w, 2.2*cm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont('Helvetica-Bold', 18)
        c.drawString(margin + 0.4*cm, page_h - margin - 1.3*cm, titel)
        c.setFont('Helvetica', 10)
        datum_str = datetime.datetime.now().strftime('%d.%m.%Y')
        sub_right = f"Erstellt am: {datum_str}  |  {total} Kinder"
        tw = c.stringWidth(sub_right, 'Helvetica', 10)
        c.drawString(margin + inner_w - tw - 0.4*cm, page_h - margin - 1.9*cm, sub_right)

        y = page_h - margin - 2.2*cm - 0.5*cm
        box_h = 1.8*cm
        gap = 0.3*cm

        # --- Grunddaten ---
        y = draw_section_title(c, y, 'Grunddaten')
        n_boxes = 4
        bw = (inner_w - gap * (n_boxes - 1)) / n_boxes
        boxes = [
            ('Kinder gesamt', total, '', (0.88, 0.93, 1.0)),
            ('Jungen', m, pct(m), (0.80, 0.90, 1.0)),
            ('Mädchen', w, pct(w), (1.0, 0.85, 0.90)),
            ('Divers/Unbek.', d_other, pct(d_other), (0.92, 1.0, 0.92)),
        ]
        for i, (lbl, val, sub, bg) in enumerate(boxes):
            stat_box(c, margin + i*(bw+gap), y - box_h, bw, box_h, lbl, val, sub, bg)
        y -= box_h + 0.5*cm

        # --- Betreuung ---
        y = draw_section_title(c, y, 'Betreuung nach Unterricht')
        boxes2 = [
            ('OGS', ogs, pct(ogs), (0.75, 0.93, 0.75)),
            ('ÜMI', uemi, pct(uemi), (0.88, 0.78, 1.0)),
            ('Abholung', abholung, pct(abholung), (0.95, 0.95, 0.95)),
            ('Unbekannt', betr_unbek, pct(betr_unbek), (0.93, 0.93, 0.93)),
        ]
        for i, (lbl, val, sub, bg) in enumerate(boxes2):
            stat_box(c, margin + i*(bw+gap), y - box_h, bw, box_h, lbl, val, sub, bg)
        y -= box_h + 0.5*cm

        # --- Besondere Merkmale ---
        y = draw_section_title(c, y, 'Besondere Merkmale')
        bw3 = (inner_w - gap * 2) / 3
        boxes3 = [
            ('Kann-Kinder', kann, pct(kann), (0.80, 0.93, 1.0)),
            ('AO-SF Verdacht/Verfahren', aosf, pct(aosf), (1.0, 0.93, 0.75)),
            ('Rückstellung', rst, pct(rst), (0.93, 0.93, 0.93)),
        ]
        for i, (lbl, val, sub, bg) in enumerate(boxes3):
            stat_box(c, margin + i*(bw3+gap), y - box_h, bw3, box_h, lbl, val, sub, bg)
        y -= box_h + 0.5*cm

        # --- Pädagogische Diagnostik ---
        y = draw_section_title(c, y, f'Pädagogische Diagnostik  ({diag_erfasst} von {total} erfasst)')
        bar_w = inner_w - 2.8*cm
        bar_h = 0.55*cm
        lbl_x = margin
        bar_x = margin + 2.8*cm

        for row_label, dist in [('Kognitiv', diag_kog), ('Verhalten', diag_ver)]:
            y -= 0.3*cm
            c.setFont('Helvetica-Bold', 9)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(lbl_x, y - bar_h/2 + 1, row_label)
            stacked_bar(c, bar_x, y - bar_h, bar_w, bar_h, dist, [3, 2, 1, 0])
            y -= bar_h + 0.2*cm
            legend_row(c, bar_x, y - 0.4*cm, dist, [3, 2, 1, 0])
            y -= 0.55*cm
        y -= 0.2*cm

        # --- Schulspiel ---
        y = draw_section_title(c, y, f'Schulspiel  ({len(spiel_list)} von {total} teilgenommen)')
        y -= 0.3*cm
        stacked_bar(c, bar_x, y - bar_h, bar_w, bar_h, spiel_scores, [3, 2, 1, 0], show_none=False)
        y -= bar_h + 0.2*cm
        legend_row(c, bar_x, y - 0.4*cm, spiel_scores, [3, 2, 1, 0], show_none=False)
        y -= 0.7*cm

        # --- Schularzt ---
        y = draw_section_title(c, y, f'Schulärztliche Untersuchung  ({len(arzt_list)} von {total} untersucht)')
        y -= 0.3*cm
        stacked_bar(c, bar_x, y - bar_h, bar_w, bar_h, arzt_scores, [3, 2, 1, 0])
        y -= bar_h + 0.2*cm
        legend_row(c, bar_x, y - 0.4*cm, arzt_scores, [3, 2, 1, 0])
        y -= 0.7*cm

        # --- Kita-Berichte ---
        kita_list = [s for s in sl if s.kita_bericht]
        kita_kog = score_dist([s.kita_bericht.kognitiv for s in kita_list])
        kita_ver = score_dist([s.kita_bericht.verhalten for s in kita_list])
        y = draw_section_title(c, y, f'Kita-Berichte  ({len(kita_list)} von {total} vorhanden)')
        for row_label, dist in [('Kognitiv', kita_kog), ('Verhalten', kita_ver)]:
            y -= 0.3*cm
            c.setFont('Helvetica-Bold', 9)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(lbl_x, y - bar_h/2 + 1, row_label)
            stacked_bar(c, bar_x, y - bar_h, bar_w, bar_h, dist, [3, 2, 1, 0])
            y -= bar_h + 0.2*cm
            legend_row(c, bar_x, y - 0.4*cm, dist, [3, 2, 1, 0])
            y -= 0.55*cm

        # --- Förderkurse ---
        y -= 0.3*cm
        fk_counts = {
            'LRS':     sum(1 for s in sl if s.foerderkurs_lrs),
            'Mathe':   sum(1 for s in sl if s.foerderkurs_mathe),
            'Sport':   sum(1 for s in sl if s.foerderkurs_sport),
            'Deutsch': sum(1 for s in sl if s.foerderkurs_deutsch),
        }
        fk_colors = {
            'LRS':     (0.71, 0.27, 0.11),
            'Mathe':   (0.04, 0.32, 0.60),
            'Sport':   (0.17, 0.48, 0.17),
            'Deutsch': (0.42, 0.05, 0.68),
        }
        y = draw_section_title(c, y, 'Förderkurse')
        bw4 = (inner_w - gap * 3) / 4
        for i, (name, count) in enumerate(fk_counts.items()):
            stat_box(c, margin + i*(bw4+gap), y - box_h, bw4, box_h,
                     name, count, pct(count),
                     tuple(v + 0.35*(1-v) for v in fk_colors[name]))
        y -= box_h + 0.3*cm

        # Deckblatt-Seite abschließen, dann leere Seite für Duplexdruck
        c.showPage()
        c.showPage()

    draw_deckblatt(c, schueler_list, klasse_filter)

    for schueler in schueler_list:
        diag = schueler.diagnostik
        arzt = schueler.schularzt_untersuchung
        spiel = schueler.schulspiel_diagnostik
        kita = schueler.kita_bericht

        # ========== SEITE 1: Stammdaten + Diagnostik + Schulspiel ==========
        draw_header_bar(c, schueler, 1)
        y = page_h - margin - 1.4*cm - 0.4*cm

        # Status-Badges Zeile
        bx = margin
        if schueler.kann_kind:
            draw_badge(c, bx, y - 0.6*cm, 'Kann-Kind', (0.8, 0.9, 1.0), w=2.2*cm)
            bx += 2.4*cm
        if schueler.has_aosf_verdacht:
            draw_badge(c, bx, y - 0.6*cm, 'AO-SF', (1.0, 0.8, 0.2), w=1.8*cm)
            bx += 2.0*cm
        if schueler.has_rueckstellung_empfohlen:
            draw_badge(c, bx, y - 0.6*cm, 'Rückstellung', (0.85, 0.85, 0.85), w=2.8*cm)
        y -= 1.0*cm

        # --- Pädagogische Diagnostik ---
        y = draw_section_title(c, y, 'Pädagogische Diagnostik')

        diag_items = [(k.kurzname, criteria.anzeigetext(k, wert) if k.typ != 'skala' else wert)
                      for k, wert in criteria.eintraege(schueler.id, 'diagnostik')]
        y = draw_two_col_items(c, y, diag_items)

        # Gesamteindruck
        y -= 0.1*cm
        c.setFont('Helvetica-Bold', 9)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(margin + 0.2*cm, y, 'Gesamteindruck:')
        gk = diag.gesamteindruck_kognitiv if diag else None
        gv = diag.gesamteindruck_verhalten if diag else None
        draw_badge(c, margin + 3.5*cm, y - 0.08*cm, f'Kognitiv: {SCORE_TEXT[gk]}', SCORE_RGB[gk], w=3.2*cm)
        draw_badge(c, margin + 7.0*cm, y - 0.08*cm, f'Verhalten: {SCORE_TEXT[gv]}', SCORE_RGB[gv], w=3.2*cm)
        y -= 0.8*cm

        # Bemerkung Diagnostik
        if diag and diag.bemerkung:
            y = draw_text_block(c, margin + 0.2*cm, y, f'Notiz: {diag.bemerkung}')
            y -= 0.15*cm

        # --- Schulspiel ---
        if spiel or (diag and diag.schulspiel):
            y = draw_section_title(c, y, 'Schulspiel-Diagnostik')
            spiel_items = [(k.kurzname, criteria.anzeigetext(k, wert) if k.typ != 'skala' else wert)
                           for k, wert in criteria.eintraege(schueler.id, 'schulspiel')]
            y = draw_two_col_items(c, y, spiel_items)
            if spiel:
                c.setFont('Helvetica-Bold', 9)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(margin + 0.2*cm, y, 'Gesamttendenz:')
                gt = spiel.gesamttendenz
                draw_badge(c, margin + 3.5*cm, y - 0.08*cm, SCORE_TEXT[gt], SCORE_RGB[gt], w=1.4*cm)
                gw_txt = f'Gesamtwert: {spiel.gesamtwert}/{spiel.hoechstwert}'
                c.setFont('Helvetica', 9)
                c.drawString(margin + 5.5*cm, y, gw_txt)
                y -= 0.8*cm

        c.showPage()

        # ========== SEITE 2: Schularzt + Kita + Freunde + Prozesse ==========
        draw_header_bar(c, schueler, 2)
        y = page_h - margin - 1.4*cm - 0.4*cm

        # --- Schulärztliche Untersuchung ---
        y = draw_section_title(c, y, 'Schulärztliche Untersuchung')

        def info_row(c, y, label, value, x_offset=0.2*cm):
            c.setFont('Helvetica-Bold', 9)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(margin + x_offset, y, f'{label}:')
            c.setFont('Helvetica', 9)
            lw = c.stringWidth(f'{label}: ', 'Helvetica-Bold', 9)
            c.drawString(margin + x_offset + lw, y, str(value) if value is not None else '-')
            return y - 0.52*cm

        if arzt:
            # Befund: alle Kriterien des Schularzt-Bogens außer den Förderhinweisen.
            pairs = [(k.kurzname, criteria.anzeigetext(k, wert) or '-')
                     for k, wert in criteria.eintraege(schueler.id, 'schularzt')
                     if not k.foerderhinweis]
            for idx, (lbl, val) in enumerate(pairs):
                col = idx % 2
                x = margin + col * (inner_w / 2 + 0.2*cm)
                if col == 0 and idx > 0:
                    y -= 0.52*cm
                if idx == 0:
                    y -= 0.52*cm
                c.setFont('Helvetica-Bold', 9)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(x + 0.2*cm, y, f'{lbl}:')
                lw = c.stringWidth(f'{lbl}: ', 'Helvetica-Bold', 9)
                c.setFont('Helvetica', 9)
                c.drawString(x + 0.2*cm + lw, y, val)
            y -= 0.7*cm

            # Gesamteinschätzung
            c.setFont('Helvetica-Bold', 9)
            c.drawString(margin + 0.2*cm, y, 'Gesamteinschätzung:')
            draw_badge(c, margin + 4.5*cm, y - 0.06*cm, SCORE_TEXT[arzt.gesamteinschaetzung], SCORE_RGB[arzt.gesamteinschaetzung], w=1.4*cm)
            y -= 0.7*cm

            # Förderempfehlungen
            foerder = criteria.foerderhinweise(schueler.id)
            if foerder:
                c.setFont('Helvetica-Bold', 9)
                c.drawString(margin + 0.2*cm, y, 'Förderempfehlungen:')
                bx2 = margin + 4.5*cm
                for fitem in foerder:
                    fw = c.stringWidth(fitem, 'Helvetica-Bold', 8) + 0.4*cm
                    draw_badge(c, bx2, y - 0.05*cm, fitem, (0.95, 0.85, 0.6), w=fw, h=0.5*cm)
                    bx2 += fw + 0.15*cm
                    if bx2 > margin + inner_w - 1*cm:
                        y -= 0.55*cm
                        bx2 = margin + 4.5*cm
                y -= 0.7*cm

            if arzt.bemerkung:
                y = draw_text_block(c, margin + 0.2*cm, y, f'Notiz: {arzt.bemerkung}')
                y -= 0.2*cm
        else:
            c.setFont('Helvetica-Oblique', 9)
            c.setFillColorRGB(0.5, 0.5, 0.5)
            c.drawString(margin + 0.2*cm, y - 0.4*cm, 'Keine schulärztliche Untersuchung erfasst.')
            c.setFillColorRGB(0, 0, 0)
            y -= 1.0*cm

        y -= 0.3*cm

        # --- Kita-Bericht ---
        y = draw_section_title(c, y, 'Kita-Bericht')
        if kita:
            c.setFont('Helvetica-Bold', 9)
            c.drawString(margin + 0.2*cm, y - 0.45*cm, 'Kognitiv:')
            draw_badge(c, margin + 2.5*cm, y - 0.52*cm, SCORE_TEXT[kita.kognitiv], SCORE_RGB[kita.kognitiv], w=1.4*cm)
            c.setFont('Helvetica-Bold', 9)
            c.drawString(margin + 4.5*cm, y - 0.45*cm, 'Verhalten:')
            draw_badge(c, margin + 7.0*cm, y - 0.52*cm, SCORE_TEXT[kita.verhalten], SCORE_RGB[kita.verhalten], w=1.4*cm)
            y -= 1.0*cm
            if kita.bemerkung:
                y = draw_text_block(c, margin + 0.2*cm, y, f'Notiz: {kita.bemerkung}')
                y -= 0.2*cm
        else:
            c.setFont('Helvetica-Oblique', 9)
            c.setFillColorRGB(0.5, 0.5, 0.5)
            c.drawString(margin + 0.2*cm, y - 0.4*cm, 'Kein Kita-Bericht erfasst.')
            c.setFillColorRGB(0, 0, 0)
            y -= 1.0*cm

        y -= 0.3*cm

        # --- Freundschaftswünsche ---
        y = draw_section_title(c, y, 'Freundschaftswünsche')
        y -= 0.45*cm
        f1 = schueler.freund1
        f2 = schueler.freund2

        def freund_str(f, negativ):
            if not f:
                return '-'
            name = f'{f.vorname} {f.nachname}'
            suffix = ' (NICHT)' if negativ else ''
            in_class = f' → Klasse {f.klasse}' if f.klasse else ' → nicht zugeteilt'
            return name + suffix + in_class

        def freund_color(f, negativ, schueler_klasse):
            if not f: return (0.5, 0.5, 0.5)
            if negativ:
                # Red if in same class
                return (0.9, 0.3, 0.3) if f.klasse and f.klasse == schueler_klasse else (0.2, 0.6, 0.2)
            else:
                return (0.2, 0.6, 0.2) if f.klasse and f.klasse == schueler_klasse else (0.9, 0.6, 0.1)

        c.setFont('Helvetica-Bold', 9)
        c.drawString(margin + 0.2*cm, y, 'Wunsch 1:')
        c.setFont('Helvetica', 9)
        f1_txt = freund_str(f1, schueler.freund1_negativ)
        c.setFillColorRGB(*freund_color(f1, schueler.freund1_negativ, schueler.klasse))
        c.drawString(margin + 2.5*cm, y, f1_txt)
        c.setFillColorRGB(0, 0, 0)
        y -= 0.52*cm

        c.setFont('Helvetica-Bold', 9)
        c.drawString(margin + 0.2*cm, y, 'Wunsch 2:')
        c.setFont('Helvetica', 9)
        f2_txt = freund_str(f2, schueler.freund2_negativ)
        c.setFillColorRGB(*freund_color(f2, schueler.freund2_negativ, schueler.klasse))
        c.drawString(margin + 2.5*cm, y, f2_txt)
        c.setFillColorRGB(0, 0, 0)
        y -= 0.52*cm

        if schueler.freunde_bemerkung:
            y = draw_text_block(c, margin + 0.2*cm, y, f'Notiz: {schueler.freunde_bemerkung}')
            y -= 0.2*cm

        y -= 0.3*cm

        # --- AO-SF / Rückstellung ---
        if schueler.has_aosf_verdacht or schueler.has_rueckstellung_empfohlen:
            y = draw_section_title(c, y, 'Besondere Verfahren')
            y -= 0.45*cm
            if schueler.aosf_prozess:
                ap = schueler.aosf_prozess
                c.setFont('Helvetica-Bold', 9)
                c.drawString(margin + 0.2*cm, y, f'AO-SF: Status {ap.status}')
                c.setFont('Helvetica', 9)
                c.drawString(margin + 5*cm, y, f'Konferenz: {ap.konferenz_ergebnis or "-"}  |  Schulamt: {ap.schulamt_entscheidung or "-"}')
                y -= 0.55*cm
            elif schueler.has_aosf_verdacht:
                c.setFont('Helvetica', 9)
                c.drawString(margin + 0.2*cm, y, 'AO-SF: Verdacht (noch kein Prozess gestartet)')
                y -= 0.55*cm

            if schueler.rueckstellung_prozess:
                rp = schueler.rueckstellung_prozess
                c.setFont('Helvetica-Bold', 9)
                c.drawString(margin + 0.2*cm, y, f'Rückstellung: Status {rp.status}')
                c.setFont('Helvetica', 9)
                c.drawString(margin + 5*cm, y, f'Konferenz: {rp.konferenz_ergebnis or "-"}')
                y -= 0.55*cm
            elif schueler.has_rueckstellung_empfohlen:
                c.setFont('Helvetica', 9)
                c.drawString(margin + 0.2*cm, y, 'Rückstellung: empfohlen (noch kein Prozess)')
                y -= 0.55*cm

        c.showPage()

    c.save()
    buffer.seek(0)

    klasse_label = f'_Klasse_{klasse_filter}' if klasse_filter else '_Alle'
    fname = f'Klassenmappe{klasse_label}_{datetime.datetime.now().strftime("%Y%m%d")}.pdf'
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename={fname}'
    return response


FOERDERKURSE = [
    ('foerderkurs_lrs',     'LRS',     '#b5451b'),
    ('foerderkurs_mathe',   'Mathe',   '#0a5299'),
    ('foerderkurs_sport',   'Sport',   '#2a7a2a'),
    ('foerderkurs_deutsch', 'Deutsch', '#6a0dad'),
]

@route('/foerderkurse/einzel', methods=['GET', 'POST'])
@login_required
def foerderkurse_einzel():
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_namen(settings) if False else get_klassen_names(settings)

    # Gleiche Reihenfolge wie Bulk: Klassen alphabetisch, dann ohne Klasse
    alle = []
    for k in klassen_namen:
        alle += Schueler.query.filter_by(klasse=k).order_by(Schueler.nachname, Schueler.vorname).all()
    alle += Schueler.query.filter(
        (Schueler.klasse == None) | (~Schueler.klasse.in_(klassen_namen))
    ).order_by(Schueler.nachname, Schueler.vorname).all()

    total = len(alle)
    if total == 0:
        flash('Keine Schüler vorhanden.')
        return redirect(url_for('index'))

    try:
        idx = int(request.args.get('idx', 0))
    except ValueError:
        idx = 0
    idx = max(0, min(idx, total - 1))

    s = alle[idx]
    form = BulkFoerderkursForm()

    if form.validate_on_submit():
        for field, *_ in FOERDERKURSE:
            setattr(s, field, field in request.form)
        db.session.commit()
        next_idx = idx + 1
        if next_idx >= total:
            flash('Alle Kinder bearbeitet.')
            return redirect(url_for('foerderkurse_bulk'))
        return redirect(url_for('foerderkurse_einzel', idx=next_idx))

    return render_template('foerderkurse_einzel.html',
                           form=form, s=s, idx=idx, total=total,
                           kurse=FOERDERKURSE,
                           diag_gruppen=criteria.eintraege_gruppiert(s.id, 'diagnostik'),
                           spiel_gruppen=criteria.eintraege_gruppiert(s.id, 'schulspiel'),
                           foerderhinweise=criteria.foerderhinweise(s.id))


@route('/foerderkurse/bulk', methods=['GET', 'POST'])
@login_required
def foerderkurse_bulk():
    form = BulkFoerderkursForm()
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)

    if form.validate_on_submit():
        alle = Schueler.query.all()
        for s in alle:
            for field, *_ in FOERDERKURSE:
                setattr(s, field, f'{field}_{s.id}' in request.form)
        db.session.commit()
        flash('Förderkurse gespeichert.')
        return redirect(url_for('foerderkurse_bulk'))

    gruppen = {}
    for k in klassen_namen:
        schueler = Schueler.query.filter_by(klasse=k).order_by(Schueler.nachname, Schueler.vorname).all()
        if schueler:
            gruppen[k] = schueler
    ohne = Schueler.query.filter(
        (Schueler.klasse == None) | (~Schueler.klasse.in_(klassen_namen))
    ).order_by(Schueler.nachname, Schueler.vorname).all()
    if ohne:
        gruppen['–'] = ohne

    return render_template('foerderkurse_bulk.html', form=form, gruppen=gruppen, kurse=FOERDERKURSE)


@route('/export/pdf/foerderkurse')
@login_required
def export_foerderkurse():
    """Klassenliste mit Ankreuzfeldern je Förderkurs – eine Seite pro Klasse."""
    from reportlab.lib.colors import HexColor

    klasse_filter = request.args.get('klasse')
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)

    if klasse_filter and klasse_filter in klassen_namen:
        gruppen = [(klasse_filter,
                    Schueler.query.filter_by(klasse=klasse_filter)
                    .order_by(Schueler.nachname, Schueler.vorname).all())]
    else:
        gruppen = []
        for kname in klassen_namen:
            sl = Schueler.query.filter_by(klasse=kname).order_by(Schueler.nachname, Schueler.vorname).all()
            if sl:
                gruppen.append((kname, sl))
        ohne = Schueler.query.filter(
            (Schueler.klasse == None) | (~Schueler.klasse.in_(klassen_namen))
        ).order_by(Schueler.nachname, Schueler.vorname).all()
        if ohne:
            gruppen.append((None, ohne))

    gruppen = [(k, sl) for k, sl in gruppen if sl]
    if not gruppen:
        flash('Keine Schüler für den Export vorhanden.')
        return redirect(url_for('foerderkurse_bulk'))

    kurse = [(field, label, HexColor(hexcol)) for field, label, hexcol in FOERDERKURSE]

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    page_w, page_h = A4

    margin = 1.5 * cm
    inner_w = page_w - 2 * margin

    nr_w = 0.8 * cm
    geb_w = 2.2 * cm
    kurs_w = 2.1 * cm
    name_w = inner_w - nr_w - geb_w - kurs_w * len(kurse)

    row_h = 0.62 * cm
    head_h = 0.75 * cm
    box = 0.40 * cm

    jahr = settings.einschulungsjahr if settings else ''
    stand = datetime.datetime.now().strftime('%d.%m.%Y')

    def kurs_x(i):
        """Linke Kante der i-ten Kursspalte."""
        return margin + nr_w + name_w + geb_w + i * kurs_w

    def draw_check(cx, cy, color):
        """Zeichnet ein Häkchen mittig um (cx, cy)."""
        c.setStrokeColor(color)
        c.setLineWidth(1.6)
        c.setLineCap(1)
        c.setLineJoin(1)
        p = c.beginPath()
        p.moveTo(cx - box * 0.28, cy + box * 0.02)
        p.lineTo(cx - box * 0.06, cy - box * 0.22)
        p.lineTo(cx + box * 0.30, cy + box * 0.26)
        c.drawPath(p, stroke=1, fill=0)
        c.setLineWidth(1)

    def draw_page_head(klasse, anzahl, seite, seiten):
        """Titelbalken + Tabellenkopf. Gibt das y der ersten Datenzeile zurück."""
        top = page_h - margin
        c.setFillColor(HexColor('#0a5299'))
        c.rect(margin, top - 1.3 * cm, inner_w, 1.3 * cm, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont('Helvetica-Bold', 14)
        titel = f'Förderkurse – Klasse {klasse}' if klasse else 'Förderkurse – ohne Klassenzuordnung'
        c.drawString(margin + 0.3 * cm, top - 0.85 * cm, titel)
        c.setFont('Helvetica', 9)
        rechts = f'Einschulungsjahr {jahr}  |  {anzahl} Kinder'
        if seiten > 1:
            rechts += f'  |  Seite {seite}/{seiten}'
        tw = c.stringWidth(rechts, 'Helvetica', 9)
        c.drawString(margin + inner_w - tw - 0.3 * cm, top - 0.85 * cm, rechts)

        # Tabellenkopf
        y = top - 1.3 * cm - 0.5 * cm - head_h
        c.setFillColorRGB(0.93, 0.93, 0.93)
        c.rect(margin, y, inner_w, head_h, fill=1, stroke=0)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(margin + 0.15 * cm, y + 0.25 * cm, 'Nr.')
        c.drawString(margin + nr_w + 0.15 * cm, y + 0.25 * cm, 'Name, Vorname')
        c.drawString(margin + nr_w + name_w + 0.15 * cm, y + 0.25 * cm, 'Geburtstag')
        for i, (_field, label, color) in enumerate(kurse):
            bx = kurs_x(i)
            c.setFillColor(color)
            c.roundRect(bx + 0.15 * cm, y + 0.13 * cm, kurs_w - 0.3 * cm, head_h - 0.26 * cm, 3, fill=1, stroke=0)
            c.setFillColorRGB(1, 1, 1)
            c.setFont('Helvetica-Bold', 9)
            lw = c.stringWidth(label, 'Helvetica-Bold', 9)
            c.drawString(bx + (kurs_w - lw) / 2, y + 0.25 * cm, label)
        c.setFillColorRGB(0, 0, 0)
        return y

    def draw_row(y, nr, s):
        """Zeichnet eine Schülerzeile; y ist die Oberkante."""
        y -= row_h
        if nr % 2 == 1:
            c.setFillColorRGB(0.965, 0.965, 0.965)
            c.rect(margin, y, inner_w, row_h, fill=1, stroke=0)

        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.setFont('Helvetica', 8)
        c.drawString(margin + 0.15 * cm, y + 0.2 * cm, str(nr))

        c.setFillColorRGB(0, 0, 0)
        c.setFont('Helvetica-Bold', 9.5)
        name = f'{s.nachname}, {s.vorname}'
        max_name_w = name_w - 0.3 * cm
        if c.stringWidth(name, 'Helvetica-Bold', 9.5) > max_name_w:
            while name and c.stringWidth(name + '…', 'Helvetica-Bold', 9.5) > max_name_w:
                name = name[:-1]
            name += '…'
        c.drawString(margin + nr_w + 0.15 * cm, y + 0.19 * cm, name)

        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.setFont('Helvetica', 8.5)
        c.drawString(margin + nr_w + name_w + 0.15 * cm, y + 0.2 * cm,
                     s.geburtsdatum.strftime('%d.%m.%Y') if s.geburtsdatum else '')

        for i, (field, _label, color) in enumerate(kurse):
            bx = kurs_x(i) + (kurs_w - box) / 2
            by = y + (row_h - box) / 2
            gesetzt = bool(getattr(s, field))
            if gesetzt:
                c.setFillColor(color)
                c.setFillAlpha(0.12)
                c.rect(bx, by, box, box, fill=1, stroke=0)
                c.setFillAlpha(1)
            c.setStrokeColor(color if gesetzt else HexColor('#9aa0a6'))
            c.setLineWidth(0.8)
            c.rect(bx, by, box, box, fill=0, stroke=1)
            if gesetzt:
                draw_check(bx + box / 2, by + box / 2, color)

        c.setStrokeColorRGB(0.88, 0.88, 0.88)
        c.setLineWidth(0.3)
        c.line(margin, y, margin + inner_w, y)
        c.setFillColorRGB(0, 0, 0)
        return y

    def draw_summary(y, schueler_liste):
        """Zusammenfassung je Kurs unter der Tabelle."""
        y -= 0.9 * cm
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(margin, y + 0.15 * cm, 'Teilnehmer je Kurs:')
        x = margin + 3.4 * cm
        for field, label, color in kurse:
            anzahl = sum(1 for s in schueler_liste if getattr(s, field))
            text = f'{label}: {anzahl}'
            c.setFillColor(color)
            c.rect(x, y + 0.12 * cm, 0.3 * cm, 0.3 * cm, fill=1, stroke=0)
            c.setFillColorRGB(0.2, 0.2, 0.2)
            c.setFont('Helvetica', 9)
            c.drawString(x + 0.45 * cm, y + 0.15 * cm, text)
            x += 0.45 * cm + c.stringWidth(text, 'Helvetica', 9) + 0.8 * cm
        c.setFillColorRGB(0, 0, 0)
        return y

    def draw_footer():
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.setFont('Helvetica', 7.5)
        c.drawString(margin, margin - 0.3 * cm, f'SL-Office  |  Stand: {stand}')
        c.setFillColorRGB(0, 0, 0)

    # Wie viele Zeilen passen auf eine Seite?
    y_first = page_h - margin - 1.3 * cm - 0.5 * cm - head_h
    y_limit = margin + 1.6 * cm
    rows_per_page = max(1, int((y_first - y_limit) // row_h))

    for klasse, schueler_liste in gruppen:
        seiten = max(1, -(-len(schueler_liste) // rows_per_page))
        for seite in range(seiten):
            teil = schueler_liste[seite * rows_per_page:(seite + 1) * rows_per_page]
            y = draw_page_head(klasse, len(schueler_liste), seite + 1, seiten)
            for offset, s in enumerate(teil):
                y = draw_row(y, seite * rows_per_page + offset + 1, s)
            if seite == seiten - 1:
                draw_summary(y, schueler_liste)
            draw_footer()
            c.showPage()

    c.save()
    buffer.seek(0)

    label = f'_Klasse_{klasse_filter}' if klasse_filter and klasse_filter in klassen_namen else ''
    fname = f'Foerderkurse{label}_{datetime.datetime.now().strftime("%Y%m%d")}.pdf'
    response = make_response(buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename={fname}'
    return response

@route('/betreuung/bulk', methods=['GET', 'POST'])
@login_required
def betreuung_bulk():
    form = BulkBetreuungForm()
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)

    if form.validate_on_submit():
        alle = Schueler.query.all()
        for s in alle:
            val = request.form.get(f'betreuung_{s.id}', '').strip()
            if val in ('OGS', 'ÜMI', 'Abholung', ''):
                s.betreuung = val if val else None
        db.session.commit()
        flash('Betreuungsformen gespeichert.')
        return redirect(url_for('betreuung_bulk'))

    # Schüler gruppiert nach Klasse, dann alphabetisch
    gruppen = {}
    for k in klassen_namen:
        schueler = Schueler.query.filter_by(klasse=k).order_by(Schueler.nachname, Schueler.vorname).all()
        if schueler:
            gruppen[k] = schueler
    ohne = Schueler.query.filter(
        (Schueler.klasse == None) | (~Schueler.klasse.in_(klassen_namen))
    ).order_by(Schueler.nachname, Schueler.vorname).all()
    if ohne:
        gruppen['–'] = ohne

    return render_template('betreuung_bulk.html', form=form, gruppen=gruppen)


@route('/export/excel/klassenlisten')
@login_required
def export_klassenlisten():
    """Excel-Export aller Klassenlisten."""
    settings = GlobalSettings.query.first()
    klassen_namen = get_klassen_names(settings)

    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='openpyxl')

    for kname in klassen_namen:
        schueler_list = Schueler.query.filter_by(klasse=kname).order_by(Schueler.nachname, Schueler.vorname).all()
        data = []
        for s in schueler_list:
            row = {
                'Name': s.nachname, 'Vorname': s.vorname,
                'Geburtsdatum': s.geburtsdatum.strftime('%d.%m.%Y') if s.geburtsdatum else '',
                'Geschlecht': s.geschlecht, 'Kita': s.kita or '',
                'Kann-Kind': 'Ja' if s.kann_kind else 'Nein',
                'AO-SF': 'Ja' if s.has_aosf_verdacht else 'Nein',
                'Rückstellung': 'Ja' if s.has_rueckstellung_empfohlen else 'Nein',
            }
            data.append(row)
        df = pd.DataFrame(data) if data else pd.DataFrame(columns=['Name','Vorname','Geburtsdatum','Geschlecht','Kita','Kann-Kind','AO-SF','Rückstellung'])
        df.to_excel(writer, index=False, sheet_name=f'Klasse {kname}')
        ws = writer.sheets[f'Klasse {kname}']
        for col_cells in ws.columns:
            length = max((len(str(cell.value or '')) for cell in col_cells), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = length + 2

    writer.close()
    output.seek(0)
    fname = f'Klassenlisten_{datetime.datetime.now().strftime("%Y%m%d")}.xlsx'
    return send_file(output, download_name=fname, as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def create_app(environment=None, config_overrides=None):
    """Build an isolated SL-Office application instance."""
    flask_app = Flask(__name__)
    load_config(flask_app, environment)
    if config_overrides:
        flask_app.config.update(config_overrides)

    # Hinter nginx stehen Schema, Host und Client-Adresse in den
    # X-Forwarded-*-Kopfzeilen. Ohne das baut url_for(_external=True) die
    # Aktivierungslinks der Elternbriefe als http:// mit falschem Host.
    # Nur auswerten, wenn wirklich ein Proxy davorsteht -- sonst könnte ein
    # Client die Kopfzeilen selbst mitschicken.
    proxies = flask_app.config.get("TRUSTED_PROXIES", 0)
    if proxies:
        flask_app.wsgi_app = ProxyFix(
            flask_app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies, x_port=proxies,
        )

    db.init_app(flask_app)
    migrate.init_app(flask_app, db)
    login_manager.init_app(flask_app)
    login_manager.login_view = 'auth.login'
    init_security(flask_app)
    flask_app.register_blueprint(auth_bp)
    flask_app.register_blueprint(admin_bp)
    flask_app.register_blueprint(students_bp)
    flask_app.register_blueprint(appointments_bp)
    flask_app.register_blueprint(criteria_bp)
    features.install_template_context(flask_app)
    features.install_module_guard(flask_app)
    install_first_run_redirect(flask_app)
    register_maintenance_cli(flask_app)

    # The year scope is registered once on the shared Session class; the
    # read-only guard is per application.
    if not getattr(school_year, "_scope_installed", False):
        school_year.install(flask_app)
        school_year._scope_installed = True
    school_year.install_readonly_guard(flask_app)
    school_year.install_template_context(flask_app)

    # Woran desktop.py eine schon laufende Instanz erkennt; ohne Anmeldung.
    flask_app.add_url_rule(
        "/lebenszeichen", "lebenszeichen",
        lambda: ("SL-Office", 200, {"Content-Type": "text/plain; charset=utf-8"}))

    for rule, options, view_function in _route_definitions:
        endpoint = options.get('endpoint', view_function.__name__)
        rule_options = {key: value for key, value in options.items() if key != 'endpoint'}
        flask_app.add_url_rule(rule, endpoint, view_function, **rule_options)

    if flask_app.config.get('AUTO_CREATE_DB'):
        with flask_app.app_context():
            db.create_all()

    return flask_app


# Backwards-compatible WSGI entry point for ``gunicorn app:app``.
app = create_app()


if __name__ == '__main__':
    app.run(host='127.0.0.1', debug=False, port=5001)
