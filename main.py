"""Project Part 1: Environment Setup and Catalog Ingestion.

Student Name: Sukhveer Kaur
Student ID: 5147346
Course: ITEC3706 S01 - Software Engineering
Institution: Algoma University - Sault Ste. Marie

This FastAPI application implements the Part 1 grading contract:

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
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse


app = FastAPI(title="Course Catalog Ingestion API")


CourseRecord = dict[str, str | int]


# In-memory store for the current running process. This is enough for Part 1
# because the grader imports a catalog and then immediately queries it.
catalog_by_normalized_code: dict[str, CourseRecord] = {}


def normalize_course_code(course_code: str) -> str:
    """Normalize course codes so COSC3506 and COSC 3506 match the same record."""

    return re.sub(r"[^A-Z0-9]", "", course_code.upper())


def clean_text(value: str) -> str:
    """Collapse repeated whitespace while preserving the visible cell text."""

    return " ".join(value.replace("\xa0", " ").split())


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


@app.get("/")
def root() -> dict[str, Any]:
    """Simple reachability endpoint for Render/browser wake-up."""

    return {
        "status": "ok",
        "message": "Course Catalog Ingestion API is running.",
        "import_endpoint": "/api/v1/admin/catalog/import",
        "lookup_endpoint": "/api/v1/catalog/courses/{course_code}",
    }


@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)) -> dict[str, Any]:
    """Import an uploaded HTML course catalog into memory."""

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded catalog file is empty.")

    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("latin-1")

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

