import sqlite3

DB_FILE = "pipeline.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS processed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def is_event_processed(event_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def mark_event_processed(event_id, job_id, person_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO processed_events (event_id, job_id, person_id) VALUES (?, ?, ?)",
        (event_id, job_id, person_id)
    )
    conn.commit()
    conn.close()
