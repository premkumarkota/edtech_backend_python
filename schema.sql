-- ============================================================
-- EdTech Platform — Complete PostgreSQL Schema
-- Generated from SQLAlchemy models
-- Run: psql -U <user> -d <dbname> -f schema.sql
-- ============================================================

-- ── ENUMS ────────────────────────────────────────────────────

CREATE TYPE userrole AS ENUM ('admin', 'student', 'teacher');

CREATE TYPE teacherstatus AS ENUM ('pending', 'approved', 'rejected');

CREATE TYPE subscriptionstatus AS ENUM ('pending', 'active', 'expired', 'cancelled', 'failed');

CREATE TYPE sessionstatus AS ENUM ('booked', 'in_progress', 'completed', 'cancelled', 'no_show');

CREATE TYPE quizstatus AS ENUM ('draft', 'published', 'archived');

CREATE TYPE attemptstatus AS ENUM ('in_progress', 'completed', 'abandoned');

-- ── TABLE 1: categories ──────────────────────────────────────
-- Exam boards / course types (CBSE, SSC, B.Tech, etc.)
-- Created by admin. Students & teachers pick one during onboarding.

CREATE TABLE categories (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL UNIQUE,
    image_url  VARCHAR(500),
    is_active  BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX ix_categories_id ON categories (id);


-- ── TABLE 2: users ───────────────────────────────────────────
-- Single table for all 3 roles: admin / student / teacher
-- Admin uses email + hashed_password (bcrypt)
-- Student & Teacher use firebase_uid (Firebase Phone OTP)

CREATE TABLE users (
    id                   SERIAL PRIMARY KEY,
    name                 VARCHAR(100),
    email                VARCHAR(255) UNIQUE,
    phone_number         VARCHAR(20) UNIQUE,
    hashed_password      VARCHAR(255),                          -- admin only
    role                 userrole NOT NULL,
    is_active            BOOLEAN DEFAULT TRUE,
    firebase_uid         VARCHAR(128) UNIQUE,                   -- student/teacher only
    onboarding_completed BOOLEAN DEFAULT FALSE,
    profile_image_url    VARCHAR(500),

    -- Legacy flat fields (kept for backward compat)
    dob            VARCHAR(50),
    age            INTEGER,
    school_college VARCHAR(200),
    location       VARCHAR(200),
    document_url   VARCHAR(500),
    is_verified    BOOLEAN DEFAULT FALSE,

    -- FK to primary category (set during onboarding)
    category_id INTEGER REFERENCES categories (id),

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ,

    CONSTRAINT uq_users_email         UNIQUE (email),
    CONSTRAINT uq_users_phone         UNIQUE (phone_number),
    CONSTRAINT uq_users_firebase_uid  UNIQUE (firebase_uid)
);

CREATE INDEX ix_users_id          ON users (id);
CREATE INDEX ix_users_email       ON users (email);
CREATE INDEX ix_users_phone       ON users (phone_number);
CREATE INDEX ix_users_firebase    ON users (firebase_uid);


-- ── TABLE 3: student_profiles ────────────────────────────────
-- Extended student-specific fields. 1:1 with users (role=student).

CREATE TABLE student_profiles (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    dob            VARCHAR(20),
    age            INTEGER,
    school_college VARCHAR(200),
    location       VARCHAR(200),
    created_at     TIMESTAMPTZ DEFAULT now(),
    updated_at     TIMESTAMPTZ
);

CREATE INDEX ix_student_profiles_id ON student_profiles (id);


-- ── TABLE 4: teacher_profiles ────────────────────────────────
-- Extended teacher-specific fields. 1:1 with users (role=teacher).
-- Tracks verification status (pending/approved/rejected).

CREATE TABLE teacher_profiles (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    institution_name VARCHAR(200),
    location         VARCHAR(200),
    bio              TEXT,
    experience_years INTEGER,
    document_url     VARCHAR(500),           -- Verification PDF/image URL
    status           teacherstatus NOT NULL DEFAULT 'pending',
    reviewed_by      INTEGER REFERENCES users (id),
    reviewed_at      TIMESTAMPTZ,
    rejection_reason TEXT,
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ
);

CREATE INDEX ix_teacher_profiles_id ON teacher_profiles (id);


-- ── TABLE 5: syllabus ────────────────────────────────────────
-- A subject/course (Math, Physics) belonging to a category.

CREATE TABLE syllabus (
    id            SERIAL PRIMARY KEY,
    category_id   INTEGER NOT NULL REFERENCES categories (id),
    title         VARCHAR(200) NOT NULL,
    description   TEXT,
    thumbnail_url VARCHAR(500),
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_syllabus_id          ON syllabus (id);
CREATE INDEX ix_syllabus_category_id ON syllabus (category_id);


-- ── TABLE 6: chapters ────────────────────────────────────────
-- Chapters within a syllabus (Chapter 1: Sets, Chapter 2: Relations, etc.)

CREATE TABLE chapters (
    id          SERIAL PRIMARY KEY,
    syllabus_id INTEGER NOT NULL REFERENCES syllabus (id),
    title       VARCHAR(200) NOT NULL,
    description TEXT,
    order_index INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_chapters_id ON chapters (id);


-- ── TABLE 7: syllabus_contents ───────────────────────────────
-- Individual learning materials (video, PDF, PPT) within a chapter.

CREATE TABLE syllabus_contents (
    id             SERIAL PRIMARY KEY,
    chapter_id     INTEGER NOT NULL REFERENCES chapters (id),
    title          VARCHAR(200) NOT NULL,
    description    TEXT,
    content_type   VARCHAR(50) NOT NULL,    -- 'video', 'pdf', 'ppt', 'text'
    file_url       VARCHAR(500) NOT NULL,
    video_duration INTEGER,                 -- in seconds (for video only)
    order_index    INTEGER DEFAULT 0,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_syllabus_contents_id ON syllabus_contents (id);


-- ── TABLE 8: quizzes ─────────────────────────────────────────
-- Quiz shell created by admin per category/syllabus/chapter.

CREATE TABLE quizzes (
    id            SERIAL PRIMARY KEY,
    category_id   INTEGER NOT NULL REFERENCES categories (id),
    syllabus_id   INTEGER REFERENCES syllabus (id),
    chapter_id    INTEGER REFERENCES chapters (id),
    title         VARCHAR(200) NOT NULL,
    description   TEXT,
    duration_mins INTEGER DEFAULT 30,
    total_marks   INTEGER DEFAULT 0,
    pass_marks    INTEGER DEFAULT 0,
    status        quizstatus NOT NULL DEFAULT 'draft',
    created_by    INTEGER REFERENCES users (id),
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ
);

CREATE INDEX ix_quizzes_id          ON quizzes (id);
CREATE INDEX ix_quizzes_category_id ON quizzes (category_id);
CREATE INDEX ix_quizzes_syllabus_id ON quizzes (syllabus_id);
CREATE INDEX ix_quizzes_chapter_id  ON quizzes (chapter_id);


-- ── TABLE 9: quiz_questions ──────────────────────────────────
-- Questions for a quiz (loaded via Excel upload).

CREATE TABLE quiz_questions (
    id             SERIAL PRIMARY KEY,
    quiz_id        INTEGER NOT NULL REFERENCES quizzes (id) ON DELETE CASCADE,
    question_text  TEXT NOT NULL,
    option_a       TEXT NOT NULL,
    option_b       TEXT NOT NULL,
    option_c       TEXT,
    option_d       TEXT,
    correct_option CHAR(1) NOT NULL,    -- 'A', 'B', 'C', or 'D'
    explanation    TEXT,
    marks          INTEGER DEFAULT 1,
    order_index    INTEGER DEFAULT 0,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_quiz_questions_id      ON quiz_questions (id);
CREATE INDEX ix_quiz_questions_quiz_id ON quiz_questions (quiz_id);


-- ── TABLE 10: quiz_attempts ──────────────────────────────────
-- A student's attempt at a quiz.

CREATE TABLE quiz_attempts (
    id              SERIAL PRIMARY KEY,
    quiz_id         INTEGER NOT NULL REFERENCES quizzes (id),
    student_id      INTEGER NOT NULL REFERENCES users (id),
    status          attemptstatus NOT NULL DEFAULT 'in_progress',
    score           INTEGER DEFAULT 0,
    total_marks     INTEGER DEFAULT 0,
    percentage      FLOAT,
    passed          BOOLEAN,
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    time_taken_secs INTEGER
);

CREATE INDEX ix_quiz_attempts_id         ON quiz_attempts (id);
CREATE INDEX ix_quiz_attempts_quiz_id    ON quiz_attempts (quiz_id);
CREATE INDEX ix_quiz_attempts_student_id ON quiz_attempts (student_id);


-- ── TABLE 11: quiz_answers ───────────────────────────────────
-- Per-question answers for a quiz attempt.

CREATE TABLE quiz_answers (
    id              SERIAL PRIMARY KEY,
    attempt_id      INTEGER NOT NULL REFERENCES quiz_attempts (id) ON DELETE CASCADE,
    question_id     INTEGER NOT NULL REFERENCES quiz_questions (id) ON DELETE CASCADE,
    selected_option CHAR(1),        -- 'A', 'B', 'C', 'D', or NULL (skipped)
    is_correct      BOOLEAN,
    marks_awarded   INTEGER DEFAULT 0
);

CREATE INDEX ix_quiz_answers_id         ON quiz_answers (id);
CREATE INDEX ix_quiz_answers_attempt_id ON quiz_answers (attempt_id);


-- ── TABLE 12: subscription_plans ────────────────────────────
-- Admin-defined plans. Students pick and pay for one.
-- Soft delete only (is_active = false).

CREATE TABLE subscription_plans (
    id                           SERIAL PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    description                  TEXT,
    price                        NUMERIC(10, 2) NOT NULL,        -- INR
    validity_days                INTEGER NOT NULL DEFAULT 30,
    mock_tests_allowed           INTEGER NOT NULL DEFAULT 0,     -- 0 = unlimited
    video_call_minutes_per_month INTEGER NOT NULL DEFAULT 0,     -- 0 = no video access
    is_active                    BOOLEAN DEFAULT TRUE,
    created_at                   TIMESTAMPTZ DEFAULT now(),
    updated_at                   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_subscription_plans_id ON subscription_plans (id);


-- ── TABLE 13: student_subscriptions ─────────────────────────
-- A student's purchased subscription.
-- Entitlements are SNAPSHOTTED from the plan at purchase time.
-- Only ONE active subscription per student at a time.

CREATE TABLE student_subscriptions (
    id          SERIAL PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    plan_id     INTEGER NOT NULL REFERENCES subscription_plans (id),

    status      subscriptionstatus NOT NULL DEFAULT 'pending',

    started_at  TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,

    -- Entitlement snapshot (copied from plan at purchase time)
    mock_tests_allowed       INTEGER NOT NULL DEFAULT 0,
    mock_tests_used          INTEGER NOT NULL DEFAULT 0,
    video_call_minutes_total INTEGER NOT NULL DEFAULT 0,
    video_call_minutes_used  INTEGER NOT NULL DEFAULT 0,

    -- Razorpay references
    razorpay_order_id   VARCHAR(100) UNIQUE,
    razorpay_payment_id VARCHAR(100) UNIQUE,
    amount_paid         NUMERIC(10, 2),

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_student_subscriptions_id         ON student_subscriptions (id);
CREATE INDEX ix_student_subscriptions_student_id ON student_subscriptions (student_id);

-- Only one ACTIVE subscription per student at a time
CREATE UNIQUE INDEX uq_one_active_sub_per_student
    ON student_subscriptions (student_id)
    WHERE status = 'active';


-- ── TABLE 14: razorpay_payments ──────────────────────────────
-- Full audit log of every Razorpay payment event. Never deleted.
-- gateway_response stores the raw webhook payload (JSONB).

CREATE TABLE razorpay_payments (
    id                  SERIAL PRIMARY KEY,
    student_id          INTEGER REFERENCES users (id),
    subscription_id     INTEGER REFERENCES student_subscriptions (id),
    razorpay_order_id   VARCHAR(100) NOT NULL UNIQUE,
    razorpay_payment_id VARCHAR(100) UNIQUE,
    razorpay_signature  VARCHAR(500),
    amount              NUMERIC(10, 2) NOT NULL,
    currency            VARCHAR(10) DEFAULT 'INR',
    event_type          VARCHAR(50),
    status              VARCHAR(30) NOT NULL DEFAULT 'created',   -- created / captured / failed
    gateway_response    JSONB,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_razorpay_payments_id         ON razorpay_payments (id);
CREATE INDEX ix_razorpay_payments_order_id   ON razorpay_payments (razorpay_order_id);
CREATE INDEX ix_razorpay_payments_payment_id ON razorpay_payments (razorpay_payment_id);


-- ── TABLE 15: video_call_sessions ────────────────────────────
-- Full lifecycle of a video call between a student and a teacher.
-- Minutes deducted from subscription ONLY when status = 'completed'.

CREATE TABLE video_call_sessions (
    id               SERIAL PRIMARY KEY,
    student_id       INTEGER NOT NULL REFERENCES users (id),
    teacher_id       INTEGER NOT NULL REFERENCES users (id),
    subscription_id  INTEGER NOT NULL REFERENCES student_subscriptions (id),

    status           sessionstatus NOT NULL DEFAULT 'booked',
    agora_channel_name VARCHAR(200) UNIQUE,

    scheduled_at            TIMESTAMPTZ NOT NULL,
    started_at              TIMESTAMPTZ,
    ended_at                TIMESTAMPTZ,
    scheduled_duration_mins INTEGER NOT NULL DEFAULT 30,
    actual_duration_mins    INTEGER,                -- set when teacher calls /end

    cancelled_by  INTEGER REFERENCES users (id),
    cancel_reason TEXT,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_video_call_sessions_id             ON video_call_sessions (id);
CREATE INDEX ix_video_call_sessions_student_id     ON video_call_sessions (student_id);
CREATE INDEX ix_video_call_sessions_teacher_id     ON video_call_sessions (teacher_id);
CREATE INDEX ix_video_call_sessions_subscription_id ON video_call_sessions (subscription_id);


-- ── TABLE 16: teacher_rates ──────────────────────────────────
-- Per-teacher rate (INR per minute), set by admin.
-- One row per teacher. Admin updates in-place.
-- Default rate: ₹2.00/min (if no row exists, service defaults to 2.00).

CREATE TABLE teacher_rates (
    id              SERIAL PRIMARY KEY,
    teacher_id      INTEGER NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
    rate_per_minute NUMERIC(8, 2) NOT NULL DEFAULT 2.00,
    set_by_admin_id INTEGER REFERENCES users (id),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_teacher_rates_id         ON teacher_rates (id);
CREATE INDEX ix_teacher_rates_teacher_id ON teacher_rates (teacher_id);


-- ── TABLE 17: payout_batches ─────────────────────────────────
-- Admin-triggered monthly payout batch.
-- Covers all PENDING earnings in a date range.

CREATE TABLE payout_batches (
    id             SERIAL PRIMARY KEY,
    initiated_by   INTEGER NOT NULL REFERENCES users (id),
    period_from    DATE NOT NULL,
    period_to      DATE NOT NULL,
    total_teachers INTEGER DEFAULT 0,
    total_amount   NUMERIC(12, 2) DEFAULT 0.00,
    status         VARCHAR(20) NOT NULL DEFAULT 'processing',   -- processing / completed
    notes          TEXT,
    created_at     TIMESTAMPTZ DEFAULT now(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX ix_payout_batches_id ON payout_batches (id);


-- ── TABLE 18: teacher_earnings ───────────────────────────────
-- Auto-created when a session ends (status=completed).
-- rate_per_minute is snapshotted — rate changes don't affect past earnings.

CREATE TABLE teacher_earnings (
    id              SERIAL PRIMARY KEY,
    teacher_id      INTEGER NOT NULL REFERENCES users (id),
    session_id      INTEGER NOT NULL UNIQUE REFERENCES video_call_sessions (id),
    duration_mins   INTEGER NOT NULL,
    rate_per_minute NUMERIC(8, 2) NOT NULL,
    gross_earning   NUMERIC(10, 2) NOT NULL,                    -- duration_mins × rate_per_minute
    payout_status   VARCHAR(20) NOT NULL DEFAULT 'pending',     -- pending / paid
    payout_batch_id INTEGER REFERENCES payout_batches (id),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_teacher_earnings_id         ON teacher_earnings (id);
CREATE INDEX ix_teacher_earnings_teacher_id ON teacher_earnings (teacher_id);


-- ── TABLE 19: teacher_payouts ────────────────────────────────
-- Per-teacher payment record within a payout batch.
-- Admin pays via bank and marks as 'transferred'.

CREATE TABLE teacher_payouts (
    id                 SERIAL PRIMARY KEY,
    batch_id           INTEGER NOT NULL REFERENCES payout_batches (id),
    teacher_id         INTEGER NOT NULL REFERENCES users (id),
    amount             NUMERIC(10, 2) NOT NULL,
    sessions_count     INTEGER NOT NULL DEFAULT 0,
    bank_account       VARCHAR(200),                            -- snapshot of bank details at payout time
    razorpay_payout_id VARCHAR(100),                           -- Razorpay Payout API (Phase 2)
    status             VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending / transferred / failed
    transferred_at     TIMESTAMPTZ,
    notes              TEXT,
    created_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ix_teacher_payouts_id         ON teacher_payouts (id);
CREATE INDEX ix_teacher_payouts_batch_id   ON teacher_payouts (batch_id);
CREATE INDEX ix_teacher_payouts_teacher_id ON teacher_payouts (teacher_id);


-- ── ALEMBIC TRACKING TABLE ───────────────────────────────────
-- Alembic uses this to track which migrations have run.
-- Required if you will use `alembic upgrade head` in the future.

CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);

-- Mark all migrations as applied so Alembic doesn't re-run them
INSERT INTO alembic_version (version_num) VALUES ('f989131358a9')
ON CONFLICT DO NOTHING;


-- ============================================================
-- DONE
-- Tables created: 19
-- Enums created: 6
-- ============================================================
