import db
import os
os.environ["DATABASE_URL"] = "postgresql://postgres:3VgdwPf4dpzZhueGT6EAvU8ekO2D1vXQxf6ezF8Xg7vG10QL3C7WwxKlF20fSWpZ@ycs80kcwoo048kkckoookog0:5432/postgres"

# Call init_db directly to ensure migrations run
try:
    db.init_db()
    print("INIT OK")
except Exception as e:
    print("INIT ERR:", e)

try:
    res = db.create_agent("test1", "Test", "hi-IN", "hi-IN", "rohan", "gpt-4o-mini", "Hello", "test")
    print("Agent:", res)
except Exception as e:
    print("Agent ERR:", e)
