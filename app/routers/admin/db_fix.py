
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import get_db

router = APIRouter()

@router.get("/fix-db-schema")
def fix_database_schema(db: Session = Depends(get_db)):
    """
    Temporary endpoint to manually run migration SQL commands.
    This adds the missing columns to the 'users' table.
    """
    try:
        # 1. Ensure categories table exists (create_all usually handles this, but just in case)
        # Note: We rely on create_all for new tables, so we focus on ALTER here.

        # 2. Add 'category_id' column to 'users' table if not exists
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES categories(id);"))
        
        # 3. Add 'is_verified' column to 'users' table if not exists
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;"))
        
        # 4. Add 'dob', 'age', 'school_college', 'location' if missing (from previous student updates)
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS dob VARCHAR(50);"))
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS age INTEGER;"))
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS school_college VARCHAR(200);"))
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS location VARCHAR(200);"))

        # 5. Add 'document_url' if missing (from teacher updates)
        db.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS document_url VARCHAR(500);"))

        db.commit()
        return {"message": "Database schema fixed successfully! Added missing columns."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")
