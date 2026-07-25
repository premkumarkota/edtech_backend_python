"""
Firebase Cloud Messaging (FCM) utility.

Sends push notifications to student and teacher mobile apps.
Uses the same Firebase Admin SDK instance already initialised for phone OTP.

IMPORTANT: This requires the Firebase service account JSON to have
'Firebase Cloud Messaging API' enabled in the GCP console.
No extra credentials needed — same firebase-key.json used for OTP auth.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


_CATEGORY_MAP = {
    "session_booked": "sessions",
    "session_cancelled": "sessions",
    "session_incoming_call": "sessions",
    "instant_request": "sessions",
    "instant_request_accepted": "sessions",
    "instant_request_declined": "sessions",
    "instant_session_accepted": "sessions",
    "instant_session_declined": "sessions",
    "instant_session_expired": "sessions",
    "study_session_reminder": "study_planner",
    "study_streak_reminder": "study_planner",
    "study_plan_reminder": "study_planner",
    "subscription_activated": "offers",
    "withdrawal_processing": "system",
    "withdrawal_completed": "system",
    "withdrawal_rejected": "system",
    "profile_approved": "system",
    "profile_rejected": "system",
}


def _persist_notification(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict],
    user_id: Optional[int] = None,
) -> None:
    """Save notification to DB for in-app notification feed. Fire-and-forget."""
    try:
        import json
        from app.database import SessionLocal
        from app.models.notification import Notification
        from app.models.user import User

        db = SessionLocal()
        try:
            # Resolve user_id from fcm_token if not provided
            uid = user_id
            if not uid and fcm_token:
                user = db.query(User.id).filter(User.fcm_token == fcm_token).first()
                if user:
                    uid = user[0]

            if not uid:
                return

            notif_type = (data or {}).get("type", "general")
            category = _CATEGORY_MAP.get(notif_type, "system")

            db.add(Notification(
                user_id=uid,
                title=title,
                body=body,
                notif_type=notif_type,
                category=category,
                data=json.dumps(data) if data else None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"Failed to persist notification: {e}")


def send_push(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    user_id: Optional[int] = None,
) -> bool:
    """
    Send a push notification to a single device and persist it to DB.

    Args:
        fcm_token: Device FCM registration token (stored in users.fcm_token)
        title:     Notification title shown on device
        body:      Notification body text
        data:      Optional key-value payload for in-app handling
                   e.g. {"type": "session_booked", "session_id": "42"}
        user_id:   Optional user ID to persist notification in DB for in-app feed

    Returns:
        True if sent successfully, False otherwise (never raises).
    """
    # Always persist to DB (even if FCM fails, user can see it in-app)
    _persist_notification(fcm_token, title, body, data, user_id)

    if not fcm_token:
        logger.warning("send_push called with empty fcm_token — skipping.")
        return False

    try:
        from app.utils.firebase import init_firebase
        init_firebase()

        import firebase_admin.messaging as fcm_messaging

        message = fcm_messaging.Message(
            notification=fcm_messaging.Notification(
                title=title,
                body=body,
            ),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=fcm_messaging.AndroidConfig(
                priority="high",
                notification=fcm_messaging.AndroidNotification(
                    sound="default",
                    channel_id="edtech_sessions",
                ),
            ),
            apns=fcm_messaging.APNSConfig(
                payload=fcm_messaging.APNSPayload(
                    aps=fcm_messaging.Aps(sound="default"),
                ),
            ),
        )
        fcm_messaging.send(message)
        logger.info(f"FCM sent: '{title}' to token ...{fcm_token[-8:]}")
        return True

    except Exception as e:
        # Never crash the main flow because of a notification failure
        logger.error(f"FCM send failed: {e}")
        return False


# ── Convenience wrappers ──────────────────────────────────────────────────────

def notify_teacher_session_booked(
    fcm_token: str,
    student_name: str,
    session_date: str,
    session_time: str,
    duration_mins: int,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="New Session Booked",
        body=f"{student_name} booked a {duration_mins}-min session on {session_date} at {session_time}",
        data={"type": "session_booked", "session_id": session_id},
    )


def notify_teacher_session_cancelled(
    fcm_token: str,
    student_name: str,
    session_date: str,
    session_time: str,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Session Cancelled",
        body=f"{student_name} cancelled the session on {session_date} at {session_time}",
        data={"type": "session_cancelled", "session_id": session_id},
    )


def notify_student_session_cancelled(
    fcm_token: str,
    teacher_name: str,
    session_date: str,
    session_time: str,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Session Cancelled",
        body=f"Your session with {teacher_name} on {session_date} at {session_time} was cancelled",
        data={"type": "session_cancelled", "session_id": session_id},
    )


def notify_teacher_approved(
    fcm_token: str,
    teacher_name: str,
    rate_per_hour: float,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Profile Approved!",
        body=f"Congratulations {teacher_name}! Your profile has been approved. Your rate is ₹{rate_per_hour}/hr.",
        data={"type": "profile_approved", "rate_per_hour": rate_per_hour},
    )


def notify_teacher_rejected(
    fcm_token: str,
    teacher_name: str,
    reason: str,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Profile Not Approved",
        body=f"Hi {teacher_name}, your profile needs revision. Reason: {reason}",
        data={"type": "profile_rejected"},
    )


def notify_student_subscription_activated(
    fcm_token: str,
    student_name: str,
    plan_name: str,
    expires_at: str,
    video_minutes: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="🎉 Subscription Activated!",
        body=f"Welcome, {student_name}! Your {plan_name} plan is now active. {video_minutes} video call minutes unlocked. Valid till {expires_at}.",
        data={"type": "subscription_activated"},
    )


def notify_session_reminder(
    fcm_token: str,
    other_party_name: str,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="⏰ Session Starting Soon",
        body=f"Your session with {other_party_name} starts in 15 minutes. Get ready!",
        data={"type": "session_reminder", "session_id": str(session_id)},
    )


def notify_teacher_incoming_call(
    fcm_token: str,
    student_name: str,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Incoming Session Call",
        body=f"{student_name} is calling for your live session.",
        data={
            "type": "session_incoming_call",
            "session_id": session_id,
            "caller_name": student_name,
            "caller_role": "student",
        },
    )


def notify_student_incoming_call(
    fcm_token: str,
    teacher_name: str,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Incoming Session Call",
        body=f"{teacher_name} is calling for your live session.",
        data={
            "type": "session_incoming_call",
            "session_id": session_id,
            "caller_name": teacher_name,
            "caller_role": "teacher",
        },
    )


def notify_teacher_instant_session_request(
    fcm_token: str,
    student_name: str,
    duration_mins: int,
    request_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Instant Session Request",
        body=f"{student_name} wants a {duration_mins}-min session now.",
        data={"type": "instant_request", "request_id": request_id},
    )


def notify_student_instant_session_accepted(
    fcm_token: str,
    teacher_name: str,
    request_id: int,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Instant Session Accepted",
        body=f"{teacher_name} accepted your instant session request. You can join now.",
        data={
            "type": "instant_session_accepted",
            "request_id": request_id,
            "session_id": session_id,
            "teacher_name": teacher_name,  # used by Flutter to show caller name in call screen
        },
    )


def notify_student_instant_session_declined(
    fcm_token: str,
    teacher_name: str,
    request_id: int,
    reason: str,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Instant Session Declined",
        body=f"{teacher_name} declined your request. {reason}",
        data={"type": "instant_session_declined", "request_id": request_id},
    )


def notify_student_instant_session_expired(
    fcm_token: str,
    teacher_name: str,
    request_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Instant Session Expired",
        body=f"{teacher_name} did not respond in time. Please try another tutor.",
        data={"type": "instant_session_expired", "request_id": request_id},
    )


# ── Withdrawal Notifications ──────────────────────────────────────────────────

def notify_withdrawal_processing(
    fcm_token: str,
    amount: float,
    withdrawal_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Withdrawal Initiated",
        body=(
            f"Your withdrawal of ₹{amount:.2f} is being processed. "
            "Funds will reach your bank account shortly."
        ),
        data={"type": "withdrawal_processing", "withdrawal_id": withdrawal_id},
    )


def notify_withdrawal_completed(
    fcm_token: str,
    amount: float,
    withdrawal_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Payment Transferred!",
        body=f"₹{amount:.2f} has been transferred to your bank account successfully.",
        data={"type": "withdrawal_completed", "withdrawal_id": withdrawal_id},
    )


def notify_withdrawal_failed(
    fcm_token: str,
    amount: float,
    withdrawal_id: int,
    reason: str = "",
) -> bool:
    body = f"Your withdrawal of ₹{amount:.2f} could not be processed."
    if reason:
        body += f" Reason: {reason}"
    body += " Please contact support."
    return send_push(
        fcm_token=fcm_token,
        title="Withdrawal Failed",
        body=body,
        data={"type": "withdrawal_failed", "withdrawal_id": withdrawal_id},
    )


# ── Study Planner Notifications ───────────────────────────────────────────────

def notify_student_study_reminder(
    fcm_token: str,
    slot_label: str,
    topic_title: str,
    subject_name: str,
    goal_id: int,
    entry_id: int,
) -> bool:
    """Push notification when a student's study slot is about to start."""
    return send_push(
        fcm_token=fcm_token,
        title=f"Time to Study! — {slot_label} Session",
        body=f"📚 {topic_title} ({subject_name}) is waiting for you. Let's go!",
        data={
            "type": "study_session_reminder",
            "goal_id": goal_id,
            "entry_id": entry_id,
        },
    )


def notify_student_streak_reminder(
    fcm_token: str,
    current_streak: int,
) -> bool:
    """Evening nudge if student hasn't studied today and has an active streak."""
    return send_push(
        fcm_token=fcm_token,
        title="Don't break your streak! 🔥",
        body=f"You have a {current_streak}-day streak. Study today to keep it alive!",
        data={"type": "study_streak_reminder"},
    )


def notify_withdrawal_rejected(
    fcm_token: str,
    amount: float,
    reason: str,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Withdrawal Rejected",
        body=(
            f"Your withdrawal request of ₹{amount:.2f} was rejected. "
            f"Reason: {reason}"
        ),
        data={"type": "withdrawal_rejected"},
    )
