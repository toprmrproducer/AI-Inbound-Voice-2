import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

sql = """
ALTER TABLE agents ADD COLUMN IF NOT EXISTS subtitle TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS openinggreeting TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS systemprompt TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS temperature DOUBLE PRECISION DEFAULT 0.3;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_tokens INTEGER DEFAULT 250;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS maxturns INTEGER DEFAULT 20;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS sttprovider TEXT DEFAULT 'sarvam';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS sttlanguage TEXT DEFAULT 'hi-IN';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS llmprovider TEXT DEFAULT 'openai';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS llmmodel TEXT DEFAULT 'gpt-4o-mini';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS ttsprovider TEXT DEFAULT 'sarvam';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS ttsvoice TEXT DEFAULT 'rohan';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS ttslanguage TEXT DEFAULT 'hi-IN';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS firstline TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS agentinstructions TEXT DEFAULT '';
"""

print("Connecting to DB...")
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()
cur.execute(sql)
conn.commit()
print("Migration completed successfully.")
cur.close()
conn.close()
