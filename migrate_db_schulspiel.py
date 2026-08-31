import sqlite3

def migrate():
    conn = sqlite3.connect('/home/emrichschule/SL-Office/instance/database.db')
    cursor = conn.cursor()
    
    try:
        print("Creating table 'schulspiel_diagnostik'...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schulspiel_diagnostik (
                id INTEGER PRIMARY KEY,
                schueler_id INTEGER NOT NULL,
                
                aufgabenverstaendnis INTEGER,
                konzentration INTEGER,
                anstrengungsbereitschaft INTEGER,
                merkfaehigkeit INTEGER,
                ausdauer INTEGER,
                selbstbewusstsein INTEGER,
                kontaktfaehigkeit INTEGER,
                regelverhalten INTEGER,
                versteht_anweisungen INTEGER,
                ausdruck_altersangemessen INTEGER,
                vollstaendige_saetze INTEGER,
                richtige_verbformen INTEGER,
                richtige_artikel INTEGER,
                konzept_von_schrift INTEGER,
                schreibt_eigenen_namen INTEGER,
                koerperkoordination INTEGER,
                fingerkoordination INTEGER,
                farben_und_formen INTEGER,
                figur_grund_wahrnehmung INTEGER,
                mengeninvarianz INTEGER,
                kognition INTEGER,
                raum_lage_beziehung INTEGER,
                auditive_wahrnehmung INTEGER,
                silben_segmentieren INTEGER,
                reime_erkennen INTEGER,
                
                gesamtwert INTEGER,
                pdf_dateiname VARCHAR(255),
                bemerkung TEXT,
                
                FOREIGN KEY(schueler_id) REFERENCES schueler(id)
            )
        """)
        
        # Add schulspiel_status to schueler if not exists
        cursor.execute("PRAGMA table_info(schueler)")
        columns = [info[1] for info in cursor.fetchall()]
        if 'schulspiel_status' not in columns:
            print("Adding 'schulspiel_status' to 'schueler' table...")
            cursor.execute("ALTER TABLE schueler ADD COLUMN schulspiel_status VARCHAR(20) DEFAULT 'Offen'")
            
        conn.commit()
        print("Migration successful.")
            
    except Exception as e:
        print(f"Error during migration: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
