"""
Excel Parser Service
Parses uploaded Excel files into quiz questions.

Expected Excel format per user image:
| question_text | option_a | option_b | option_c | option_d | correct_option | marks |
"""

from typing import List, Dict, Any
import io

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
    except Exception as e:
        raise ValueError(f"Cannot open Excel file: {e}")

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
        if "correct" in headers:
            headers = [h if h != "correct" else "correct_option" for h in headers]
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

        marks = _get(row_dict, "marks")
        try:
            marks = int(marks) if marks is not None else 1
        except (ValueError, TypeError):
            marks = 1

        questions.append({
            "question_text":  str(q_text).strip(),
            "option_a":       str(opt_a).strip(),
            "option_b":       str(opt_b).strip(),
            "option_c":       _get_optional(row_dict, "option_c"),
            "option_d":       _get_optional(row_dict, "option_d"),
            "correct_option": correct,
            "explanation":    _get_optional(row_dict, "explanation"), # Keep optional
            "marks":          marks,
            "order_index":    row_num - 2,
        })

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
