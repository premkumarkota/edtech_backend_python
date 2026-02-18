from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.user import User, UserRole


# OAuth2 scheme — token from Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/auth/login", auto_error=False)
oauth2_scheme_required = OAuth2PasswordBearer(tokenUrl="/api/admin/auth/login", auto_error=True)


def _decode_token(token: str, db: Session) -> User:
    """Decode JWT and return User object. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: no user ID"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme_required),
    db: Session = Depends(get_db)
) -> User:
    """Get current authenticated user (any role). Raises 401 if no token."""
    return _decode_token(token, db)


def get_current_admin(
    token: str = Depends(oauth2_scheme_required),
    db: Session = Depends(get_db)
) -> User:
    """Get current admin user. Raises 401/403 if not admin."""
    user = _decode_token(token, db)
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user


def get_current_student(
    token: str = Depends(oauth2_scheme_required),
    db: Session = Depends(get_db)
) -> User:
    """Get current student user. Raises 401/403 if not student."""
    user = _decode_token(token, db)
    if user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student access required"
        )
    return user


def get_current_teacher(
    token: str = Depends(oauth2_scheme_required),
    db: Session = Depends(get_db)
) -> User:
    """Get current teacher user. Raises 401/403 if not teacher."""
    user = _decode_token(token, db)
    if user.role != UserRole.TEACHER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher access required"
        )
    return user
