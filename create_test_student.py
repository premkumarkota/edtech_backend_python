from sqlalchemy.orm import Session
from app.database import engine, SessionLocal
from app.models.user import User, UserRole
from app.models.category import Category
from app.utils.security import hash_password, create_access_token

def create_test_data():
    db = SessionLocal()
    try:
        # 1. Ensure a category exists
        cat = db.query(Category).first()
        if not cat:
            cat = Category(name="Test Category", is_active=True)
            db.add(cat)
            db.commit()
            db.refresh(cat)
            print(f"Created Category: {cat.name} (ID: {cat.id})")
        
        # 2. Create Test Student
        email = "student@test.com"
        password = "password123"
        student = db.query(User).filter(User.email == email).first()
        
        if not student:
            student = User(
                email=email,
                name="Test Student",
                hashed_password=hash_password(password),
                role=UserRole.STUDENT,
                category_id=cat.id,
                is_active=True,
                onboarding_completed=True
            )
            db.add(student)
            db.commit()
            db.refresh(student)
            print(f"Created Student User: {email}")
        
        # 3. Generate Token (matching the format expected by our guard)
        token = create_access_token(
            data={"sub": str(student.id), "email": student.email, "role": student.role.value}
        )
        print("\n" + "="*50)
        print("TEST STUDENT CREDENTIALS")
        print(f"Email: {email}")
        print(f"Password: {password}")
        print(f"Category ID: {student.category_id}")
        print("-" * 50)
        print("YOUR ACCESS TOKEN (Paste this in Swagger 'Authorize'):")
        print(token)
        print("="*50 + "\n")

    finally:
        db.close()

if __name__ == "__main__":
    create_test_data()
