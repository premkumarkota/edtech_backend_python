import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.database import engine

def migrate():
    print("Starting manual migration to add missing columns...")
    
    # 1. Add fcm_token to users table
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN fcm_token VARCHAR(500)"))
            conn.commit()
            print("Added 'fcm_token' column to 'users' table.")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("'fcm_token' column already exists in 'users' table.")
            else:
                print(f"Error adding 'fcm_token' to 'users': {e}")

    # 2. Add total_points to student_profiles table
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE student_profiles ADD COLUMN total_points INTEGER DEFAULT 0"))
            conn.commit()
            print("Added 'total_points' column to 'student_profiles' table.")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("'total_points' column already exists in 'student_profiles' table.")
            else:
                print(f"Error adding 'total_points' to 'student_profiles': {e}")
                
    print("Migration finished.")

if __name__ == "__main__":
    migrate()
