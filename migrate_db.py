
import sqlite3

def migrate():
    conn = sqlite3.connect('/home/emrichschule/SL-Office/instance/database.db')
    cursor = conn.cursor()
    
    try:
        # Check if column exists
        cursor.execute("PRAGMA table_info(schueler)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if 'keine_freunde' not in columns:
            print("Adding column 'keine_freunde' to table 'schueler'...")
            cursor.execute("ALTER TABLE schueler ADD COLUMN keine_freunde BOOLEAN DEFAULT 0")
            conn.commit()
            print("Migration successful.")
        else:
            print("Column 'keine_freunde' already exists.")
            
    except Exception as e:
        print(f"Error during migration: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
