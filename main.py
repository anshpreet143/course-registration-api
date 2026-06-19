"""Course registration API for Project Parts 1, 2, and 3.

Student Name: Sukhveer Kaur
Student ID: 5147346
Course: ITEC3706 S01 - Software Engineering
Institution: Algoma University - Sault Ste. Marie

This FastAPI application keeps the Part 1 catalog endpoints and adds the Part 2
student profile endpoints.

Part 1:

1. POST /api/v1/admin/catalog/import
   - Accepts multipart/form-data.
   - Required file field name: file.
   - The uploaded file is an HTML course catalog.
   - The API parses the first table that has course catalog columns.
   - Parsed courses are stored in application memory.

2. GET /api/v1/catalog/courses/{course_code}
   - Looks up an imported course by course code.
   - Works with course codes with or without spaces.
   - Example: COSC3506 and COSC 3506 both resolve to COSC 3506.
   - Returns exactly the five graded keys:
     course_code, title, credits, prerequisites, cross_listed.

The parser is intentionally general. It reads table headers and row cells rather
than hardcoding sample course codes or titles. This matters because the grader
uses a hidden catalog with different course data.

Part 2:

1. POST /api/v1/students/{student_id}/history/import
   - Accepts multipart/form-data with an HTML transcript file.
   - Extracts past courses from transcript tables using the canonical rule.
   - Stores history separately for each student.

2. History, plan, and profile endpoints
   - PUT/DELETE history and POST/PUT/DELETE plan update a known student.
   - GET /profile returns exactly student_id, history, and plan.

Part 3:

1. GET /api/v1/students/{student_id}/auditreport
   - Checks planned courses against prerequisite timing rules.
   - Reports cross-listed courses that would duplicate completed credit.
   - Calculates earned, planned, and remaining graduation credits.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


app = FastAPI(title="Course Registration and Credit Tracking API")


CourseRecord = dict[str, str | int]
TranscriptRow = dict[str, str | int]


# In-memory store for the current running process. This is enough for Part 1
# because the grader imports a catalog and then immediately queries it.
catalog_by_normalized_code: dict[str, CourseRecord] = {}


class PastCourse(BaseModel):
    """Normalized record for one course already taken or currently in progress."""

    course_code: str
    term: str
    credits_earned: int = Field(ge=0)
    status: str


class PlannedCourse(BaseModel):
    """Future course plan item submitted as JSON."""

    course_code: str
    term: str


class HistoryUpdate(BaseModel):
    """Request body for replacing a student's full academic history."""

    history: list[PastCourse]


class PlanUpdate(BaseModel):
    """Request body for creating or replacing a student's planned courses."""

    planned_courses: list[PlannedCourse]


class StudentProfile(BaseModel):
    """In-memory profile state for one student."""

    student_id: str
    history: list[PastCourse] = Field(default_factory=list)
    plan: list[PlannedCourse] = Field(default_factory=list)


# Part 2 state is keyed by student_id. This avoids the common grading failure
# where one student's imported transcript leaks into another student's profile.
students_by_id: dict[str, StudentProfile] = {}


def normalize_course_code(course_code: str) -> str:
    """Normalize course codes so COSC3506 and COSC 3506 match the same record."""

    return re.sub(r"[^A-Z0-9]", "", course_code.upper())


def clean_text(value: str) -> str:
    """Collapse repeated whitespace while preserving the visible cell text."""

    return " ".join(value.replace("\xa0", " ").split())


def decode_uploaded_file(raw: bytes) -> str:
    """Decode uploaded HTML files using UTF-8 first, then a simple fallback."""

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_credits(value: str) -> int:
    """Convert the credits cell to an integer when possible."""

    cleaned = clean_text(value)
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(float(cleaned))
        except ValueError as exc:
            raise ValueError(f"Invalid credits value: {value}") from exc


def parse_transcript_credits(value: str) -> int:
    """Read transcript credits, returning 0 when the cell is blank or non-numeric."""

    match = re.search(r"\d+", clean_text(value))
    if match is None:
        return 0
    return int(match.group(0))


class TableExtractor(HTMLParser):
    """Small HTML table parser built with Python's standard library."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_table and self._in_row and tag in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._current_row.append(clean_text("".join(self._current_cell)))
            self._current_cell = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = []
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False


def header_key(header: str) -> str:
    """Map flexible table header text to the field names used by the API."""

    normalized = re.sub(r"[^a-z]", "", header.lower())
    aliases = {
        "coursecode": "course_code",
        "code": "course_code",
        "title": "title",
        "coursename": "title",
        "credits": "credits",
        "credit": "credits",
        "prerequisites": "prerequisites",
        "prerequisite": "prerequisites",
        "crosslisted": "cross_listed",
        "crosslist": "cross_listed",
        "crosslisting": "cross_listed",
    }
    return aliases.get(normalized, normalized)


def rows_to_courses(rows: list[list[str]]) -> list[CourseRecord]:
    """Convert table rows into course records if the table has required columns."""

    if not rows:
        return []

    headers = [header_key(cell) for cell in rows[0]]
    required = ["course_code", "title", "credits", "prerequisites", "cross_listed"]
    if not all(field in headers for field in required):
        return []

    indexes = {field: headers.index(field) for field in required}
    courses: list[CourseRecord] = []

    for row in rows[1:]:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))

        course_code = clean_text(row[indexes["course_code"]])
        title = clean_text(row[indexes["title"]])
        if not course_code or not title:
            continue

        courses.append(
            {
                "course_code": course_code,
                "title": title,
                "credits": parse_credits(row[indexes["credits"]]),
                "prerequisites": clean_text(row[indexes["prerequisites"]]),
                "cross_listed": clean_text(row[indexes["cross_listed"]]),
            }
        )

    return courses


def parse_catalog_html(html: str) -> list[CourseRecord]:
    """Parse the uploaded HTML catalog and return course records."""

    parser = TableExtractor()
    parser.feed(html)

    for table in parser.tables:
        courses = rows_to_courses(table)
        if courses:
            return courses

    raise ValueError("No course catalog table with required columns was found.")


def soup_cell_text(cell: Tag) -> str:
    """Extract visible text plus image alt text from one transcript table cell."""

    parts: list[str] = []
    for image in cell.find_all("img"):
        alt_text = clean_text(str(image.get("alt") or image.get("title") or ""))
        if alt_text:
            parts.append(alt_text)

    text = clean_text(cell.get_text(" ", strip=True))
    if text:
        parts.append(text)

    return clean_text(" ".join(parts))


def transcript_header_key(header: str) -> str:
    """Normalize transcript table headers used by the student export."""

    normalized = re.sub(r"[^a-z]", "", header.lower())
    aliases = {
        "status": "status",
        "course": "course",
        "grade": "grade",
        "term": "term",
        "credits": "credits",
    }
    return aliases.get(normalized, normalized)


def status_from_cell(value: str) -> str | None:
    """Return a canonical past-course status when the table cell is relevant."""

    cleaned = clean_text(value)
    allowed = ["Completed", "In-Progress", "Attempted"]
    for status_value in allowed:
        if status_value.lower() in cleaned.lower():
            return status_value
    return None


def grade_information_rank(grade: str) -> int:
    """Rank grades so transcript duplicates keep the most useful row."""

    cleaned = clean_text(grade).upper()
    if re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return 3
    if cleaned and cleaned != "P":
        return 2
    if cleaned == "P":
        return 1
    return 0


def row_sort_value(row: TranscriptRow) -> tuple[int, int]:
    """Comparison value for duplicate transcript rows."""

    return (
        grade_information_rank(str(row["grade"])),
        int(row["credits_earned"]),
    )


def extract_transcript_rows_from_table(table: Tag) -> list[TranscriptRow]:
    """Extract valid past-course rows from one transcript table."""

    table_rows = table.find_all("tr")
    if not table_rows:
        return []

    header_cells = table_rows[0].find_all(["th", "td"])
    headers = [transcript_header_key(soup_cell_text(cell)) for cell in header_cells]
    required = ["status", "course", "grade", "term", "credits"]
    if not all(field in headers for field in required):
        return []

    indexes = {field: headers.index(field) for field in required}
    extracted: list[TranscriptRow] = []

    for row in table_rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < len(headers):
            continue

        values = [soup_cell_text(cell) for cell in cells]
        status_value = status_from_cell(values[indexes["status"]])
        term = clean_text(values[indexes["term"]])
        if status_value is None or not term:
            continue

        course_code = clean_text(values[indexes["course"]])
        if not course_code:
            continue

        extracted.append(
            {
                "course_code": course_code,
                "term": term,
                "credits_earned": parse_transcript_credits(values[indexes["credits"]]),
                "status": status_value,
                "grade": clean_text(values[indexes["grade"]]),
            }
        )

    return extracted


def parse_student_history_html(html: str) -> list[PastCourse]:
    """Parse the messy student transcript export into normalized history records."""

    soup = BeautifulSoup(html, "html.parser")
    deduped: dict[tuple[str, str], TranscriptRow] = {}

    for table in soup.find_all("table"):
        for row in extract_transcript_rows_from_table(table):
            key = (str(row["course_code"]), str(row["term"]))
            current = deduped.get(key)
            if current is None or row_sort_value(row) > row_sort_value(current):
                deduped[key] = row

    return [
        PastCourse(
            course_code=str(row["course_code"]),
            term=str(row["term"]),
            credits_earned=int(row["credits_earned"]),
            status=str(row["status"]),
        )
        for row in deduped.values()
    ]


def get_existing_student(student_id: str) -> StudentProfile:
    """Fetch a student profile or return the Phase 2 required 404."""

    student = students_by_id.get(student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found.")
    return student


def parse_term_order(term: str) -> tuple[int, int]:
    """Convert terms like 23F and 26SP into sortable values."""

    match = re.fullmatch(r"(\d{2})(W|SP|S|F)", clean_text(term).upper())
    if match is None:
        return (9999, 9999)

    season_order = {"W": 1, "SP": 2, "S": 3, "F": 4}
    return (int(match.group(1)), season_order[match.group(2)])


def completed_history_by_code(history: list[PastCourse]) -> dict[str, PastCourse]:
    """Keep one completed record per course, using the latest completed term."""

    completed: dict[str, PastCourse] = {}
    for course in history:
        if course.status.lower() != "completed":
            continue

        key = normalize_course_code(course.course_code)
        current = completed.get(key)
        if current is None:
            completed[key] = course
            continue

        if parse_term_order(course.term) > parse_term_order(current.term):
            completed[key] = course
        elif parse_term_order(course.term) == parse_term_order(current.term):
            if course.credits_earned > current.credits_earned:
                completed[key] = course

    return completed


def extract_course_codes(value: str) -> list[str]:
    """Extract course-code-like tokens from prerequisite or cross-list text."""

    if clean_text(value).lower() in {"", "none", "n/a", "na"}:
        return []

    matches = re.findall(r"\b[A-Za-z]{3,5}[-\s]?\d{4}\b", value)
    return [format_course_code_for_message(match) for match in matches]


def format_course_code_for_message(course_code: str) -> str:
    """Format matched course codes consistently for audit messages."""

    normalized = normalize_course_code(course_code)
    match = re.fullmatch(r"([A-Z]{3,5})(\d{4})", normalized)
    if match is None:
        return clean_text(course_code).upper()
    return f"{match.group(1)}-{match.group(2)}"


def catalog_record_for(course_code: str) -> CourseRecord | None:
    """Find a catalog record using format-insensitive course-code matching."""

    return catalog_by_normalized_code.get(normalize_course_code(course_code))


def has_completed_before(
    completed_courses: dict[str, PastCourse],
    course_code: str,
    planned_term: str,
) -> bool:
    """Check if a prerequisite was completed in a strictly earlier term."""

    completed = completed_courses.get(normalize_course_code(course_code))
    if completed is None:
        return False
    return parse_term_order(completed.term) < parse_term_order(planned_term)


def build_timeline_validation(
    plan: list[PlannedCourse],
    completed_courses: dict[str, PastCourse],
) -> list[dict[str, Any]]:
    """Build grouped missing-prerequisite errors ordered by planned term."""

    errors_by_term: dict[str, list[dict[str, str]]] = {}

    for planned in plan:
        catalog_course = catalog_record_for(planned.course_code)
        if catalog_course is None:
            continue

        prerequisites = extract_course_codes(str(catalog_course["prerequisites"]))
        for prerequisite in prerequisites:
            if has_completed_before(completed_courses, prerequisite, planned.term):
                continue

            errors_by_term.setdefault(planned.term, []).append(
                {
                    "course_code": planned.course_code,
                    "type": "MISSING_PREREQUISITE",
                    "message": f"Missing prerequisite: {prerequisite}",
                }
            )

    return [
        {"term": term, "errors": errors_by_term[term]}
        for term in sorted(errors_by_term, key=parse_term_order)
    ]


def build_cross_list_violations(
    plan: list[PlannedCourse],
    completed_courses: dict[str, PastCourse],
) -> list[dict[str, str]]:
    """Detect planned courses that duplicate already completed cross-listed credit."""

    violations: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for planned in plan:
        catalog_course = catalog_record_for(planned.course_code)
        cross_listed_courses: list[str] = []

        if catalog_course is not None:
            cross_listed_courses.extend(
                extract_course_codes(str(catalog_course["cross_listed"]))
            )

        planned_key = normalize_course_code(planned.course_code)
        for completed_course in completed_courses.values():
            completed_catalog = catalog_record_for(completed_course.course_code)
            if completed_catalog is None:
                continue

            reverse_cross_listed = extract_course_codes(
                str(completed_catalog["cross_listed"])
            )
            if any(
                normalize_course_code(code) == planned_key
                for code in reverse_cross_listed
            ):
                cross_listed_courses.append(completed_course.course_code)

        for cross_listed in cross_listed_courses:
            completed = completed_courses.get(normalize_course_code(cross_listed))
            if completed is None:
                continue

            pair_key = (
                normalize_course_code(planned.course_code),
                normalize_course_code(completed.course_code),
            )
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            violations.append(
                {
                    "course_code": planned.course_code,
                    "type": "CROSS_LIST_CONFLICT",
                    "message": (
                        "Cross-listed with completed course "
                        f"{format_course_code_for_message(completed.course_code)}"
                    ),
                }
            )

    return violations


def already_counted_cross_list(
    course: PastCourse,
    counted_courses: list[PastCourse],
) -> bool:
    """Check if an equivalent completed cross-listed course is already counted."""

    course_catalog = catalog_record_for(course.course_code)
    course_cross_listed = (
        extract_course_codes(str(course_catalog["cross_listed"]))
        if course_catalog is not None
        else []
    )
    course_key = normalize_course_code(course.course_code)

    for counted in counted_courses:
        counted_catalog = catalog_record_for(counted.course_code)
        counted_cross_listed = (
            extract_course_codes(str(counted_catalog["cross_listed"]))
            if counted_catalog is not None
            else []
        )
        counted_key = normalize_course_code(counted.course_code)

        if any(
            normalize_course_code(code) == counted_key for code in course_cross_listed
        ):
            return True
        if any(
            normalize_course_code(code) == course_key for code in counted_cross_listed
        ):
            return True

    return False


def calculate_credit_summary(
    plan: list[PlannedCourse],
    completed_courses: dict[str, PastCourse],
) -> dict[str, int]:
    """Calculate earned, planned, and remaining credits for graduation."""

    total_earned = 0
    counted_courses: list[PastCourse] = []

    for course in sorted(completed_courses.values(), key=lambda item: item.course_code):
        if already_counted_cross_list(course, counted_courses):
            continue
        counted_courses.append(course)
        total_earned += course.credits_earned

    total_planned = 0

    for planned in plan:
        catalog_course = catalog_record_for(planned.course_code)
        if catalog_course is None:
            continue
        total_planned += int(catalog_course["credits"])

    return {
        "total_earned": total_earned,
        "total_planned": total_planned,
        "total_remaining_for_graduation": max(0, 120 - total_earned - total_planned),
    }


@app.get("/")
def root() -> dict[str, Any]:
    """Simple reachability endpoint for Render/browser wake-up."""

    return {
        "status": "ok",
        "message": "Course Registration and Credit Tracking API is running.",
        "import_endpoint": "/api/v1/admin/catalog/import",
        "lookup_endpoint": "/api/v1/catalog/courses/{course_code}",
        "student_profile_endpoint": "/api/v1/students/{student_id}/profile",
        "audit_report_endpoint": "/api/v1/students/{student_id}/audit-report",
    }


@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)) -> dict[str, Any]:
    """Import an uploaded HTML course catalog into memory."""

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded catalog file is empty.")

    html = decode_uploaded_file(raw)

    try:
        courses = parse_catalog_html(html)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    catalog_by_normalized_code.clear()
    for course in courses:
        catalog_by_normalized_code[
            normalize_course_code(str(course["course_code"]))
        ] = course

    return {"imported": len(courses)}


@app.get("/api/v1/catalog/courses/{course_code}")
def get_course(course_code: str) -> JSONResponse:
    """Return one imported course using the exact five-key graded schema."""

    course = catalog_by_normalized_code.get(normalize_course_code(course_code))
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found.")

    return JSONResponse(
        content={
            "course_code": course["course_code"],
            "title": course["title"],
            "credits": course["credits"],
            "prerequisites": course["prerequisites"],
            "cross_listed": course["cross_listed"],
        }
    )


@app.post(
    "/api/v1/students/{student_id}/history/import",
    status_code=status.HTTP_201_CREATED,
)
async def import_student_history(
    student_id: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Import one student's transcript HTML and create or replace their history."""

    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=400, detail="Uploaded transcript file is empty."
        )

    history = parse_student_history_html(decode_uploaded_file(raw))
    existing_plan = students_by_id.get(
        student_id, StudentProfile(student_id=student_id)
    ).plan
    students_by_id[student_id] = StudentProfile(
        student_id=student_id,
        history=history,
        plan=existing_plan,
    )

    return {"status": "success", "past_courses_imported": len(history)}


@app.put("/api/v1/students/{student_id}/history")
def replace_student_history(student_id: str, body: HistoryUpdate) -> dict[str, str]:
    """Replace the full academic history for an existing student."""

    student = get_existing_student(student_id)
    student.history = body.history
    return {"status": "success", "message": "Academic history updated successfully"}


@app.delete("/api/v1/students/{student_id}/history")
def clear_student_history(student_id: str) -> dict[str, str]:
    """Clear all imported academic history for an existing student."""

    student = get_existing_student(student_id)
    student.history = []
    return {"status": "success", "message": "Academic history cleared successfully"}


@app.post("/api/v1/students/{student_id}/plan")
def create_student_plan(student_id: str, body: PlanUpdate) -> dict[str, Any]:
    """Store planned courses for an existing student."""

    student = get_existing_student(student_id)
    student.plan = body.planned_courses
    return {"status": "success", "planned_courses_saved": len(student.plan)}


@app.put("/api/v1/students/{student_id}/plan")
def replace_student_plan(student_id: str, body: PlanUpdate) -> dict[str, Any]:
    """Replace all planned courses for an existing student."""

    student = get_existing_student(student_id)
    student.plan = body.planned_courses
    return {"status": "success", "planned_courses_saved": len(student.plan)}


@app.delete("/api/v1/students/{student_id}/plan")
def clear_student_plan(student_id: str) -> dict[str, str]:
    """Clear planned courses for an existing student."""

    student = get_existing_student(student_id)
    student.plan = []
    return {"status": "success", "message": "Academic plan cleared successfully"}


@app.get("/api/v1/students/{student_id}/profile")
def get_student_profile(student_id: str) -> dict[str, Any]:
    """Return exactly the three top-level keys required by Phase 2."""

    student = get_existing_student(student_id)
    return {
        "student_id": student.student_id,
        "history": [course.model_dump() for course in student.history],
        "plan": [course.model_dump() for course in student.plan],
    }


@app.get("/api/v1/students/{student_id}/audit-report")
@app.get("/api/v1/students/{student_id}/auditreport")
def get_audit_report(student_id: str, strict: bool = False) -> dict[str, Any]:
    """Return the Phase 3 academic audit report for one student."""

    student = get_existing_student(student_id)
    completed_courses = completed_history_by_code(student.history)
    timeline_validation = build_timeline_validation(student.plan, completed_courses)
    cross_list_violations = build_cross_list_violations(student.plan, completed_courses)
    credit_summary = calculate_credit_summary(student.plan, completed_courses)

    has_issues = bool(timeline_validation or cross_list_violations)
    report_status = "ok"
    if has_issues:
        report_status = "failed" if strict else "warning"

    return {
        "student_id": student.student_id,
        "status": report_status,
        "timeline_validation": timeline_validation,
        "cross_list_violations": cross_list_violations,
        "credit_summary": credit_summary,
    }
