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


def send_push(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> bool:
    """
    Send a push notification to a single device.

    Args:
        fcm_token: Device FCM registration token (stored in users.fcm_token)
        title:     Notification title shown on device
        body:      Notification body text
        data:      Optional key-value payload for in-app handling
                   e.g. {"type": "session_booked", "session_id": "42"}

    Returns:
        True if sent successfully, False otherwise (never raises).
    """
    if not fcm_token:
        logger.warning("send_push called with empty fcm_token — skipping.")
        return False

    try:
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


def notify_session_reminder(
    fcm_token: str,
    other_party_name: str,
    session_id: int,
) -> bool:
    return send_push(
        fcm_token=fcm_token,
        title="Session Starting Soon",
        body=f"Your session with {other_party_name} starts in 15 minutes",
        data={"type": "session_reminder", "session_id": session_id},
    )
