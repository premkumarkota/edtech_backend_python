from sqlalchemy import create_engine, text
from app.config import settings

def check_schemas():
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_name = 'users'
        """))
        print("Found 'users' tables in these schemas:")
        for row in result:
            print(f"- Schema: {row[0]}, Table: {row[1]}")
        
        # Check current search path
        path = conn.execute(text("SHOW search_path")).scalar()
        print(f"Current search_path: {path}")

if __name__ == "__main__":
    check_schemas()
