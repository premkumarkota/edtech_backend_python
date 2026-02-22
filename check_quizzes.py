from sqlalchemy import create_engine, text
from app.config import settings

def check():
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'quizzes'
        """))
        print("Current columns in 'quizzes' table:")
        found = False
        for row in result:
            found = True
            print(f"- {row[0]}: {row[1]}")
        if not found:
            print("Table 'quizzes' not found!")

if __name__ == "__main__":
    check()
