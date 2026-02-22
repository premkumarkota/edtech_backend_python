from sqlalchemy import create_engine, text
from app.config import settings

def check():
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_name = 'quiz_questions'
        """))
        print("Current columns in 'quiz_questions' table:")
        for row in result:
            print(f"- {row[0]}: {row[1]} (nullable: {row[2]})")

if __name__ == "__main__":
    check()
