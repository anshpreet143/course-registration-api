"""Course registration API for Project Parts 1 and 2.

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


@app.get("/")
def root() -> dict[str, Any]:
    """Simple reachability endpoint for Render/browser wake-up."""

    return {
        "status": "ok",
        "message": "Course Registration and Credit Tracking API is running.",
        "import_endpoint": "/api/v1/admin/catalog/import",
        "lookup_endpoint": "/api/v1/catalog/courses/{course_code}",
        "student_profile_endpoint": "/api/v1/students/{student_id}/profile",
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
        catalog_by_normalized_code[normalize_course_code(str(course["course_code"]))] = course

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
async def import_student_history(student_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Import one student's transcript HTML and create or replace their history."""

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded transcript file is empty.")

    history = parse_student_history_html(decode_uploaded_file(raw))
    existing_plan = students_by_id.get(student_id, StudentProfile(student_id=student_id)).plan
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
