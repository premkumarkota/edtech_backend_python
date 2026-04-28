from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


def _page_template(title: str, body: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #4b5563;
      --primary: #1d7bf2;
      --border: #e5e7eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 900px;
      margin: 24px auto;
      padding: 0 16px 32px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px;
      margin-bottom: 14px;
    }}
    h1, h2, h3 {{ margin-top: 0; }}
    h1 {{ font-size: 28px; color: #111827; }}
    h2 {{ font-size: 20px; margin-bottom: 8px; }}
    h3 {{ font-size: 16px; margin-bottom: 6px; }}
    p, li {{ color: var(--muted); }}
    a {{ color: var(--primary); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{
      display: inline-block;
      background: #e8f1fc;
      color: var(--primary);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      font-weight: 600;
      margin-bottom: 10px;
    }}
    .footer {{
      color: #6b7280;
      font-size: 13px;
      margin-top: 8px;
    }}
    ul {{ margin-top: 6px; }}
  </style>
</head>
<body>
  <div class="wrap">
    {body}
  </div>
</body>
</html>
"""


@router.get("/privacy-policy", response_class=HTMLResponse, tags=["Public - Legal"])
def privacy_policy():
    body = """
    <div class="card">
      <span class="badge">MyMentor</span>
      <h1>Privacy Policy</h1>
      <p>
        This Privacy Policy explains how MyMentor ("we", "our", "us") collects, uses, shares,
        and protects personal data when you use our applications and services.
      </p>
      <p class="footer"><strong>Last updated:</strong> 28 April 2026</p>
    </div>

    <div class="card">
      <h2>1) Data We Collect</h2>
      <ul>
        <li>Account information: name, phone number, email, role (student/teacher).</li>
        <li>Profile data: category/class, age, school/college, location, profile photo (if provided).</li>
        <li>Learning and session data: booked sessions, quiz attempts/results, teacher availability and history.</li>
        <li>Device/app data: Firebase push token, crash logs, and technical diagnostics.</li>
        <li>Payment and subscription metadata (processed through integrated payment providers).</li>
      </ul>
    </div>

    <div class="card">
      <h2>2) Why We Use Data</h2>
      <ul>
        <li>To create and manage user accounts and authentication.</li>
        <li>To provide classes, quizzes, subscriptions, reminders, and call/session features.</li>
        <li>To send essential notifications (session updates, account actions, reminders).</li>
        <li>To improve reliability, security, and user experience.</li>
        <li>To comply with legal, regulatory, and anti-fraud obligations.</li>
      </ul>
    </div>

    <div class="card">
      <h2>3) Data Sharing</h2>
      <p>We do not sell personal data. Data may be shared only with:</p>
      <ul>
        <li>Cloud and infrastructure providers used to run the service.</li>
        <li>Integrated service providers (for example authentication, messaging, payments).</li>
        <li>Authorities when required by law or legal process.</li>
      </ul>
    </div>

    <div class="card">
      <h2>4) Security</h2>
      <p>
        Data is transmitted over encrypted channels (HTTPS/TLS). Access is restricted and monitored
        based on role and operational need.
      </p>
    </div>

    <div class="card">
      <h2>5) Data Retention</h2>
      <p>
        We keep data only as long as needed for service delivery, dispute handling, security,
        and legal obligations. When no longer required, data is deleted or anonymized.
      </p>
    </div>

    <div class="card">
      <h2>6) Children and Families Policy</h2>
      <p>
        MyMentor is committed to complying with Google Play Families Policy and applicable child safety requirements.
      </p>
    </div>

    <div class="card">
      <h2>7) Contact</h2>
      <p>
        For privacy questions, contact: <a href="mailto:privacy@mymentorservices.com">privacy@mymentorservices.com</a>
      </p>
    </div>
    """
    return _page_template("MyMentor Privacy Policy", body)


@router.get("/account-deletion", response_class=HTMLResponse, tags=["Public - Legal"])
def account_deletion():
    body = """
    <div class="card">
      <span class="badge">MyMentor</span>
      <h1>Account and Data Deletion</h1>
      <p>
        You can request deletion of your MyMentor account and associated personal data using the steps below.
      </p>
      <p class="footer"><strong>Last updated:</strong> 28 April 2026</p>
    </div>

    <div class="card">
      <h2>How to Request Account Deletion</h2>
      <h3>Option A: From inside the app (recommended)</h3>
      <ul>
        <li>Open MyMentor app.</li>
        <li>Go to Profile/Settings.</li>
        <li>Tap Logout and contact support for deletion request confirmation if needed.</li>
      </ul>
      <h3>Option B: Email request</h3>
      <ul>
        <li>Send an email to <a href="mailto:privacy@mymentorservices.com">privacy@mymentorservices.com</a></li>
        <li>Subject: <strong>Account Deletion Request</strong></li>
        <li>Include your registered phone number and app role (Student/Teacher).</li>
      </ul>
    </div>

    <div class="card">
      <h2>What Gets Deleted</h2>
      <ul>
        <li>Account profile details (name, email, phone, profile metadata).</li>
        <li>Stored app tokens and account linkage for push notifications.</li>
        <li>Associated user-generated profile information.</li>
      </ul>
    </div>

    <div class="card">
      <h2>What May Be Retained</h2>
      <ul>
        <li>Financial/transaction records if legally required.</li>
        <li>Security and audit logs for fraud prevention and legal compliance.</li>
      </ul>
      <p>
        Retained records are kept only for the legally required period, after which they are deleted or anonymized.
      </p>
    </div>

    <div class="card">
      <h2>Processing Timeline</h2>
      <p>
        Deletion requests are typically completed within 7 business days after successful account verification.
      </p>
    </div>
    """
    return _page_template("MyMentor Account Deletion", body)

