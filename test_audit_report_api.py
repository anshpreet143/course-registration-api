"""Local tests for Project Phase 3 audit report logic."""

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def reset_state() -> None:
    main.catalog_by_normalized_code.clear()
    main.students_by_id.clear()


def import_catalog(html: str) -> None:
    response = client.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("catalog.html", html.encode("utf-8"), "text/html")},
    )
    assert response.status_code == 200, response.json()


def create_student(student_id: str, history: list[dict[str, object]]) -> None:
    response = client.put(
        f"/api/v1/students/{student_id}/history",
        json={"history": history},
    )
    if response.status_code == 404:
        # Phase 2 creates a student through history import. For Phase 3 unit tests
        # we create the profile directly so each case can focus on audit behavior.
        main.students_by_id[student_id] = main.StudentProfile(student_id=student_id)
        response = client.put(
            f"/api/v1/students/{student_id}/history",
            json={"history": history},
        )
    assert response.status_code == 200, response.json()


def test_audit_report_detects_missing_prerequisites_cross_lists_and_credits() -> None:
    reset_state()
    import_catalog(
        """
        <table>
          <tr>
            <th>Course Code</th><th>Title</th><th>Credits</th>
            <th>Prerequisites</th><th>Cross-listed</th>
          </tr>
          <tr><td>COSC 2007</td><td>Data Structures II</td><td>3</td><td>COSC 2006</td><td></td></tr>
          <tr><td>COSC 3127</td><td>Programming Languages</td><td>3</td><td>COSC 2007</td><td></td></tr>
          <tr><td>COSC 4426</td><td>Advanced Systems</td><td>3</td><td>COSC 3127</td><td></td></tr>
          <tr><td>ITEC 3506</td><td>Software Engineering</td><td>3</td><td>COSC 2007</td><td>COSC 3506</td></tr>
        </table>
        """
    )
    create_student(
        "770001",
        [
            {
                "course_code": "COSC-2006",
                "term": "24W",
                "credits_earned": 3,
                "status": "Completed",
            },
            {
                "course_code": "COSC-2006",
                "term": "24F",
                "credits_earned": 3,
                "status": "Completed",
            },
            {
                "course_code": "COSC-3506",
                "term": "26W",
                "credits_earned": 3,
                "status": "Completed",
            },
            {
                "course_code": "COSC-2007",
                "term": "24F",
                "credits_earned": 3,
                "status": "Completed",
            },
            {
                "course_code": "COSC-3707",
                "term": "25F",
                "credits_earned": 3,
                "status": "Attempted",
            },
        ],
    )
    plan = client.post(
        "/api/v1/students/770001/plan",
        json={
            "planned_courses": [
                {"course_code": "COSC-4426", "term": "26F"},
                {"course_code": "ITEC-3506", "term": "26SP"},
            ]
        },
    )
    assert plan.status_code == 200, plan.json()

    response = client.get("/api/v1/students/770001/auditreport")
    assert response.status_code == 200, response.json()
    body = response.json()

    assert set(body.keys()) == {
        "student_id",
        "status",
        "timeline_validation",
        "cross_list_violations",
        "credit_summary",
    }
    assert body["student_id"] == "770001"
    assert body["status"] == "warning"
    assert body["timeline_validation"] == [
        {
            "term": "26F",
            "errors": [
                {
                    "course_code": "COSC-4426",
                    "type": "MISSING_PREREQUISITE",
                    "message": "Missing prerequisite: COSC-3127",
                }
            ],
        }
    ]
    assert body["cross_list_violations"] == [
        {
            "course_code": "ITEC-3506",
            "type": "CROSS_LIST_CONFLICT",
            "message": "Cross-listed with completed course COSC-3506",
        }
    ]
    assert body["credit_summary"] == {
        "total_earned": 9,
        "total_planned": 6,
        "total_remaining_for_graduation": 105,
    }

    strict_response = client.get("/api/v1/students/770001/auditreport?strict=true")
    assert strict_response.status_code == 200, strict_response.json()
    assert strict_response.json()["status"] == "failed"


def test_audit_report_ok_when_rules_are_satisfied() -> None:
    reset_state()
    import_catalog(
        """
        <table>
          <tr>
            <th>Course Code</th><th>Title</th><th>Credits</th>
            <th>Prerequisites</th><th>Cross-listed</th>
          </tr>
          <tr><td>COSC 2007</td><td>Data Structures II</td><td>3</td><td>COSC 2006</td><td></td></tr>
          <tr><td>COSC 3127</td><td>Programming Languages</td><td>3</td><td>COSC 2007</td><td></td></tr>
        </table>
        """
    )
    create_student(
        "770002",
        [
            {
                "course_code": "COSC-2006",
                "term": "24W",
                "credits_earned": 3,
                "status": "Completed",
            },
            {
                "course_code": "COSC-2007",
                "term": "24F",
                "credits_earned": 3,
                "status": "Completed",
            },
        ],
    )
    client.post(
        "/api/v1/students/770002/plan",
        json={"planned_courses": [{"course_code": "COSC-3127", "term": "26F"}]},
    )

    response = client.get("/api/v1/students/770002/auditreport?strict=true")
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "ok"
    assert response.json()["timeline_validation"] == []
    assert response.json()["cross_list_violations"] == []
    assert response.json()["credit_summary"] == {
        "total_earned": 6,
        "total_planned": 3,
        "total_remaining_for_graduation": 111,
    }
