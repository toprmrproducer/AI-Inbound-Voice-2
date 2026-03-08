import db
import psycopg2

def migrate():
    try:
        conn = db.get_conn()
        with conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS notes TEXT;")
                cur.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();")
        print("SQL Migration applied successfully.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == '__main__':
    migrate()
