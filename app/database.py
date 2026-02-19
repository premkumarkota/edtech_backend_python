from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings


# Create database engine
try:
    engine = create_engine(settings.DATABASE_URL)
except Exception as e:
    print(f"CRITICAL WARNING: Failed to create engine with URL {settings.DATABASE_URL}. using fallback sqlite:///:memory:")
    engine = create_engine("sqlite:///:memory:")

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all models
Base = declarative_base()


def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
