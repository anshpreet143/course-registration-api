# Project Part 1: Environment Setup and Catalog Ingestion

**Student Name:** Sukhveer Kaur  
**Student ID:** 5147346  
**Course:** ITEC3706 S01 - Software Engineering  
**Institution:** Algoma University - Sault Ste. Marie

This is the corrected Project Part 1 submission for the Moodle/VPL requirement:

- `main.py`
- `api_url.txt`

The API ingests an HTML course catalog, parses the course table into structured records, and exposes a lookup endpoint for individual courses.

## Required Endpoints

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

