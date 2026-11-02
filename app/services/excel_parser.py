"""
Excel Parser Service
Parses uploaded Excel files into quiz questions.

Expected Excel format (first sheet, header row first):
| question_text | option_a | option_b | option_c | option_d | correct_option | marks | explanation |

Used by chapter quizzes (POST /api/admin/quiz/{id}/upload) and mock tests
(POST /api/admin/mock-tests/upload). build_quiz_excel_template() produces a
ready-to-fill workbook in this format.
"""

from typing import List, Dict, Any
import io

MAX_QUESTIONS = 200
TEMPLATE_HEADERS = [
    "question_text", "option_a", "option_b", "option_c", "option_d",
    "correct_option", "marks", "explanation",
]

def parse_quiz_excel(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parse Excel bytes into a list of question dicts.
    Exact column mapping for high-level precision:
    - question_text
    - option_a, option_b, option_c, option_d
    - correct_option (A, B, C, D)
    - marks (int)
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required. Run: pip install openpyxl")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception:
        raise ValueError("This file isn't a valid Excel workbook. Upload a .xlsx file.")

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    if not rows:
        raise ValueError("Excel file is empty.")

    # Normalize headers (lowercase, strip whitespace)
    headers = [str(h).strip().lower().replace(" ", "_") if h else "" for h in rows[0]]

    # Map user specific columns
    REQUIRED = {"question_text", "option_a", "option_b", "correct_option"}
    missing = REQUIRED - set(headers)
    if missing:
        # Fallback support for generic 'question' or 'correct'
        if "question" in headers:
            headers = [h if h != "question" else "question_text" for h in headers]
            missing.discard("question_text")
        for alias in ("correct", "answer", "correct_answer"):
            if alias in headers and "correct_option" not in headers:
                headers = [h if h != alias else "correct_option" for h in headers]
                missing.discard("correct_option")
        
        if missing:
            raise ValueError(f"Missing required columns: {missing}. Expected exactly: {REQUIRED}")

    questions = []
    for row_num, row in enumerate(rows[1:], start=2):
        row_dict = dict(zip(headers, row))

        # Skip completely empty rows
        if not any(row_dict.values()):
            continue

        q_text  = _get(row_dict, "question_text")
        opt_a   = _get(row_dict, "option_a")
        opt_b   = _get(row_dict, "option_b")
        correct = _get(row_dict, "correct_option")

        if not q_text:
            raise ValueError(f"Row {row_num}: 'question_text' is empty.")
        if not opt_a or not opt_b:
            raise ValueError(f"Row {row_num}: 'option_a' and 'option_b' are required.")
        if not correct:
            raise ValueError(f"Row {row_num}: 'correct_option' is empty.")

        correct = str(correct).strip().upper()
        if correct not in ("A", "B", "C", "D"):
            # Handle cases where user might put the actual text instead of A/B/C/D if he's not careful, 
            # but per spec, we expect A/B/C/D
            raise ValueError(
                f"Row {row_num}: 'correct_option' must be A, B, C, or D. Got: {correct!r}"
            )

        opt_c = _get_optional(row_dict, "option_c")
        opt_d = _get_optional(row_dict, "option_d")
        if (correct == "C" and not opt_c) or (correct == "D" and not opt_d):
            raise ValueError(
                f"Row {row_num}: correct_option is {correct} but option_{correct.lower()} is empty."
            )

        marks = _get(row_dict, "marks")
        try:
            marks = int(float(marks)) if marks is not None else 1
        except (ValueError, TypeError):
            raise ValueError(f"Row {row_num}: 'marks' must be a whole number. Got: {marks!r}")
        if marks < 1:
            raise ValueError(f"Row {row_num}: 'marks' must be at least 1.")

        questions.append({
            "question_text":  str(q_text).strip(),
            "option_a":       str(opt_a).strip(),
            "option_b":       str(opt_b).strip(),
            "option_c":       opt_c,
            "option_d":       opt_d,
            "correct_option": correct,
            "explanation":    _get_optional(row_dict, "explanation"), # Keep optional
            "marks":          marks,
            "order_index":    len(questions),
        })
        if len(questions) > MAX_QUESTIONS:
            raise ValueError(f"Too many questions. Upload at most {MAX_QUESTIONS} per file.")

    if not questions:
        raise ValueError("No valid questions found in the Excel file.")

    return questions

def _get(row: dict, key: str) -> Any:
    val = row.get(key)
    if val is None or str(val).strip() == "" or str(val).strip().lower() == "none":
        return None
    return val

def _get_optional(row: dict, key: str) -> Any:
    val = _get(row, key)
    return str(val).strip() if val is not None else None


def build_quiz_excel_template() -> bytes:
    """A blank .xlsx in the upload format, with two example rows and a help sheet."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Questions"
    ws.append(TEMPLATE_HEADERS)
    ws.append([
        "What is the SI unit of force?", "Joule", "Newton", "Watt", "Pascal",
        "B", 1, "Force is measured in newtons (N).",
    ])
    ws.append([
        "Which of these is a prime number?", "21", "27", "29", "",
        "C", 1, "",
    ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2563EB")
    for col, width in zip("ABCDEFGH", (60, 22, 22, 22, 22, 15, 8, 45)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"

    help_ws = wb.create_sheet("How to fill")
    for line in (
        ["Column", "Required", "Notes"],
        ["question_text", "Yes", "The question."],
        ["option_a, option_b", "Yes", "At least two options."],
        ["option_c, option_d", "No", "Leave blank for 2-3 option questions."],
        ["correct_option", "Yes", "A, B, C or D. The option it points to must be filled."],
        ["marks", "No", "Whole number, default 1."],
        ["explanation", "No", "Shown to students after they submit."],
        ["", "", f"Only the first sheet is read. Max {MAX_QUESTIONS} questions. Delete the example rows."],
    ):
        help_ws.append(line)
    for cell in help_ws[1]:
        cell.font = Font(bold=True)
    help_ws.column_dimensions["A"].width = 22
    help_ws.column_dimensions["B"].width = 10
    help_ws.column_dimensions["C"].width = 70

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
