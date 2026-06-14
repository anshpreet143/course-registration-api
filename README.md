# Course Registration API

**Student Name:** Sukhveer Kaur  
**Student ID:** 5147346  
**Course:** ITEC3706 S01 - Software Engineering  
**Institution:** Algoma University - Sault Ste. Marie

This repository contains the FastAPI service used for the course project.

- Part 1 ingests an HTML course catalog and exposes course lookup.
- Part 2 ingests one student's messy transcript HTML, stores planned courses, and returns a unified profile.

## Part 1 Endpoints

### Import Catalog

```text
POST /api/v1/admin/catalog/import
```

Requirements:

- Accepts `multipart/form-data`.
- File field name must be `file`.
- Uploaded file must contain an HTML course table.
- Parser must be general and must not hardcode course codes or titles.

Local test:

```bash
curl -X POST -F "file=@sample_catalog.html;type=text/html" \
  http://localhost:8000/api/v1/admin/catalog/import
```

### Get Course

```text
GET /api/v1/catalog/courses/{course_code}
```

The lookup works with or without a space:

```bash
curl http://localhost:8000/api/v1/catalog/courses/COSC3506
curl "http://localhost:8000/api/v1/catalog/courses/COSC%203506"
```

The response includes exactly these graded keys:

```json
{
  "course_code": "COSC 3506",
  "title": "Software Systems Development",
  "credits": 3,
  "prerequisites": "COSC 2007",
  "cross_listed": "ITEC 3506"
}
```

## Local Setup

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Start the API:

```bash
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/docs
```

## Local Verification

Run the local tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import test_catalog_api
test_catalog_api.test_import_and_lookup_with_and_without_space()
test_catalog_api.test_hidden_style_different_course_code_is_not_hardcoded()
print("catalog tests passed")
PY
```

## Render Deployment

Render settings:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
```

After Render gives the live service URL, update `api_url.txt` so it contains only the base URL, for example:

```text
https://yourname-course-api.onrender.com
```

Do not include a trailing slash or endpoint path.

Before submitting to Moodle/VPL, open the Render URL in a browser and wait for the server to wake up.

## Part 2 Endpoints

All Part 2 routes use this prefix:

```text
/api/v1/students/{student_id}
```

### Import Student History

```text
POST /api/v1/students/{student_id}/history/import
```

Requirements:

- Accepts `multipart/form-data`.
- File field name must be `file`.
- Uploaded file is a transcript HTML export.
- A student exists after history import creates the profile.
- Response is `201 Created`.

Local test:

```bash
curl -F "file=@student-example.html;type=text/html" \
  http://localhost:8000/api/v1/students/111/history/import
```

### Replace Or Clear History

```text
PUT /api/v1/students/{student_id}/history
DELETE /api/v1/students/{student_id}/history
```

`PUT` expects:

```json
{
  "history": [
    {
      "course_code": "COSC-1046",
      "term": "23F",
      "credits_earned": 3,
      "status": "Completed"
    }
  ]
}
```

### Store Or Clear Plan

```text
POST /api/v1/students/{student_id}/plan
PUT /api/v1/students/{student_id}/plan
DELETE /api/v1/students/{student_id}/plan
```

`POST` and `PUT` expect:

```json
{
  "planned_courses": [
    {
      "course_code": "COSC-3506",
      "term": "26F"
    }
  ]
}
```

### Get Unified Profile

```text
GET /api/v1/students/{student_id}/profile
```

The response contains exactly these top-level keys:

```json
{
  "student_id": "111",
  "history": [
    {
      "course_code": "COSC-1046",
      "term": "23F",
      "credits_earned": 3,
      "status": "Completed"
    }
  ],
  "plan": [
    {
      "course_code": "COSC-3506",
      "term": "26F"
    }
  ]
}
```

Unknown students return `404` for history lifecycle, plan, and profile routes.
