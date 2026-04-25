import enum
from sqlalchemy import Column, Integer, String, Text, Numeric, DateTime, ForeignKey, Enum, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class TeacherStatus(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TeacherProfile(Base):
    """
    Extended teacher-specific profile info.
    1:1 with users where role = 'teacher'.

    Tracks:
      - Verification status (pending/approved/rejected)
      - Rich Practo-style profile (education, experience, subjects, languages)
      - Proposed rate (what teacher wants) vs approved rate (in teacher_rates table)
    """
    __tablename__ = "teacher_profiles"

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                              unique=True, nullable=False)

    # ── Basic profile ─────────────────────────────────────────────────────────
    institution_name = Column(String(200), nullable=True)   # Primary institution
    location         = Column(String(200), nullable=True)
    bio              = Column(Text, nullable=True)           # About the teacher
    experience_years = Column(Integer, nullable=True)        # Total years (summary)
    document_url     = Column(String(500), nullable=True)    # Verification PDF/image

    # ── Rich profile (Practo-style) ───────────────────────────────────────────
    # education: [{ "degree": "B.Tech", "institution": "IIT Delhi", "year_of_passing": 2010 }]
    education    = Column(JSONB, nullable=True, default=list)

    # experience: [{ "role": "Professor", "institution": "IIT Bombay",
    #                "from_year": 2015, "to_year": null, "is_current": true }]
    experience   = Column(JSONB, nullable=True, default=list)

    # subjects: ["Mathematics", "Physics"]
    subjects     = Column(JSONB, nullable=True, default=list)

    # languages: ["English", "Hindi", "Tamil"]
    languages    = Column(JSONB, nullable=True, default=list)

    achievements = Column(Text, nullable=True)  # Awards, publications, notable mentions

    # ── Rate negotiation ──────────────────────────────────────────────────────
    # Teacher proposes during onboarding. Admin approves (stored in teacher_rates).
    proposed_rate_per_hour = Column(Numeric(8, 2), nullable=True)

    # ── Verification ──────────────────────────────────────────────────────────
    status = Column(
        Enum(TeacherStatus, values_callable=lambda x: [e.value for e in x]),
        default=TeacherStatus.PENDING.value,
        nullable=False,
    )
    reviewed_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at      = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # ── Instant sessions ─────────────────────────────────────────────────────
    is_available_now = Column(Boolean, default=False, nullable=False, server_default="false")
    available_now_started_at = Column(DateTime(timezone=True), nullable=True)
    available_now_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user     = relationship("User", foreign_keys=[user_id], back_populates="teacher_profile")
    reviewer = relationship("User", foreign_keys=[reviewed_by])
