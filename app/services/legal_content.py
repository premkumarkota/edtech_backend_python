"""
Legal policies — single source for the student app, teacher app and the public
web pages that Google Play, the App Store and payment gateways link to.

The policy text lives in app/legal/<slug>.md, converted from
"[App Name] – Legal Policies.docx". The bracketed placeholders in it
("[Company Legal Name]", "[Support Email]" …) are filled from LEGAL_DETAILS at
serve time, so business details are changed here (or via env vars) without
touching the policy wording, and apps never need a release for a text update.

Values still set to None stay visible as "[Placeholder]" — fill them in (and
have a lawyer review the text) before publishing.
"""
import html
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_LEGAL_DIR = Path(__file__).resolve().parent.parent / "legal"

LAST_UPDATED = "2026-10-28"

# Bump when the wording changes in a way users must re-accept.
LEGAL_VERSION = "2026-10-28"

# Business details. None = not provided yet (shown as the bracketed placeholder).
# Each can be overridden by an env var LEGAL_<KEY> (e.g. LEGAL_COMPANY_LEGAL_NAME).
LEGAL_DETAILS: dict[str, Optional[str]] = {
    "App Name": "MyMentor",
    "Company Legal Name": None,
    "Registered Address": None,
    "Support Email": "support@mymentorservices.com",
    "Support Phone": "+91 97415 31816",
    "Privacy Email": "privacy@mymentorservices.com",
    "Safety Email": "support@mymentorservices.com",
    "Grievance Email": "support@mymentorservices.com",
    "Grievance Phone": None,
    "City": None,
    # Policy choices suggested by the template — confirm with your lawyer.
    "Mon–Sat, 10 am – 7 pm IST": "Mon–Sat, 10 am – 7 pm IST",
    "2": "2",
    "3": "3",
    "30": "30",
    "30 days'": "30 days'",
    "6 months": "6 months",
    "12 months": "12 months",
    "the 7th": "the 7th",
    "per class / per hour / revenue share": "per hour",
}

# slug → (title, one-line summary, audiences)
POLICIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "terms-of-use": (
        "Terms of Use",
        "The rules for using the app and your account",
        ("student", "teacher"),
    ),
    "privacy-policy": (
        "Privacy Policy",
        "What data we collect, why, and your rights",
        ("student", "teacher"),
    ),
    "refund-cancellation": (
        "Refund & Cancellation",
        "How to cancel and when you get a refund",
        ("student",),
    ),
    "subscription-terms": (
        "Subscription & Auto-Renewal",
        "Prices, GST, renewals and price changes",
        ("student",),
    ),
    "community-guidelines": (
        "Community Guidelines",
        "How everyone stays safe and respectful in class",
        ("student", "teacher"),
    ),
    "teacher-terms": (
        "Teacher Terms",
        "Your engagement, payments, content and conduct",
        ("teacher",),
    ),
    "grievance-redressal": (
        "Grievance Redressal & Contact Us",
        "How to reach support and our Grievance Officer",
        ("student", "teacher"),
    ),
}


def _detail(key: str) -> Optional[str]:
    env = "LEGAL_" + re.sub(r"[^A-Z0-9]+", "_", key.upper()).strip("_")
    return os.getenv(env) or LEGAL_DETAILS.get(key)


def missing_details() -> list[str]:
    """Placeholders that still have no value."""
    return [k for k in LEGAL_DETAILS if not _detail(k)]


def _fill(text: str) -> str:
    def sub(m: re.Match) -> str:
        value = _detail(m.group(1))
        return value if value else m.group(0)
    return re.sub(r"\[([^\]\n]+)\]", sub, text)


@lru_cache(maxsize=None)
def _raw(slug: str) -> str:
    return (_LEGAL_DIR / f"{slug}.md").read_text(encoding="utf-8")


def get_policy(slug: str) -> Optional[dict]:
    if slug not in POLICIES:
        return None
    title, summary, audiences = POLICIES[slug]
    return {
        "slug": slug,
        "title": title,
        "summary": summary,
        "audiences": list(audiences),
        "last_updated": LAST_UPDATED,
        "version": LEGAL_VERSION,
        "markdown": _fill(_raw(slug)),
    }


def list_policies(audience: Optional[str] = None) -> list[dict]:
    return [
        {"slug": slug, "title": t, "summary": s, "audiences": list(a),
         "last_updated": LAST_UPDATED}
        for slug, (t, s, a) in POLICIES.items()
        if audience is None or audience in a
    ]


# ── Markdown → HTML for the public web pages ─────────────────────────────────
# Handles exactly what app/legal/*.md uses: #/## headings, "- " bullets,
# pipe tables, **bold** and paragraphs. Text is HTML-escaped first.

def _inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\b([\w.+-]+@[\w-]+\.[\w.]+)\b", r'<a href="mailto:\1">\1</a>', t)
    return t


def markdown_to_html(md: str) -> str:
    out: list[str] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:].strip())}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header, body = rows[0], [r for r in rows[1:] if not all(set(c) <= {"-"} for c in r)]
            head = "" if not any(header) else "<thead><tr>" + "".join(
                f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead>"
            out.append("<div class='table'><table>" + head + "<tbody>" + "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body
            ) + "</tbody></table></div>")
            continue
        else:
            out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(out)
