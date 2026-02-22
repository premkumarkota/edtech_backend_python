from sqlalchemy import create_engine, text
from app.config import settings

def fix():
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        print("Adding columns to 'quizzes' table...")
        try:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN syllabus_id INTEGER"))
            print("- Added syllabus_id")
        except Exception as e:
                print(f"- syllabus_id might already exist or error: {e}")
        
        try:
            conn.execute(text("ALTER TABLE quizzes ADD COLUMN chapter_id INTEGER"))
            print("- Added chapter_id")
        except Exception as e:
                print(f"- chapter_id might already exist or error: {e}")

        conn.execute(text("COMMIT"))
        print("Done.")

if __name__ == "__main__":
    fix()
