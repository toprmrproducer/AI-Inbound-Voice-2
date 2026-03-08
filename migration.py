import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    print("No DATABASE_URL found in .env")
    exit(1)

conn = psycopg2.connect(db_url)
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("""
    UPDATE agents
    SET llmmodel = 'gpt-4.1-mini',
        max_tokens = 400,
        temperature = 0.4
    WHERE llmmodel = 'gpt-4o-mini' OR llmmodel IS NULL;
    """)
    print("Migration successful")
conn.close()
