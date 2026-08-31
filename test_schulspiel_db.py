import sqlite3

def check_db():
    conn = sqlite3.connect('/home/emrichschule/SL-Office/instance/database.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(schulspiel_diagnostik)")
    info = cursor.fetchall()
    print("Columns in schulspiel_diagnostik:")
    for col in info:
        print(col[1])
        
    cursor.execute("PRAGMA table_info(schueler)")
    info = cursor.fetchall()
    print("\nColumns in schueler (checking for schulspiel_status):")
    if any(col[1] == 'schulspiel_status' for col in info):
        print("schulspiel_status exists!")
    else:
        print("schulspiel_status is MISSING!")
    conn.close()

if __name__ == '__main__':
    check_db()
