"""Local tests for Project Phase 2 student profile ingestion."""

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def reset_students() -> None:
    main.students_by_id.clear()


def test_history_import_profile_and_plan_lifecycle() -> None:
    reset_students()

    with open("student-example.html", "rb") as transcript:
        response = client.post(
            "/api/v1/students/111/history/import",
            files={"file": ("student-example.html", transcript, "text/html")},
        )

    assert response.status_code == 201, response.json()
    assert response.json() == {"status": "success", "past_courses_imported": 6}

    profile = client.get("/api/v1/students/111/profile")
    assert profile.status_code == 200, profile.json()
    assert set(profile.json().keys()) == {"student_id", "history", "plan"}
    assert profile.json()["student_id"] == "111"
    assert profile.json()["plan"] == []

    history_by_course = {
        (course["course_code"], course["term"]): course
        for course in profile.json()["history"]
    }
    assert history_by_course[("COSC-2006", "24W")] == {
        "course_code": "COSC-2006",
        "term": "24W",
        "credits_earned": 3,
        "status": "Completed",
    }
    assert ("COSC-2036", "26SP") not in history_by_course
    assert ("COSC-4106", "26SP") in history_by_course
    assert ("COSC-3707", "25F") in history_by_course

    plan_response = client.post(
        "/api/v1/students/111/plan",
        json={"planned_courses": [{"course_code": "COSC-3506", "term": "26F"}]},
    )
    assert plan_response.status_code == 200, plan_response.json()
    assert plan_response.json()["planned_courses_saved"] == 1

    profile_after_plan = client.get("/api/v1/students/111/profile")
    assert profile_after_plan.json()["plan"] == [
        {"course_code": "COSC-3506", "term": "26F"}
    ]

    put_history = client.put(
        "/api/v1/students/111/history",
        json={
            "history": [
                {
                    "course_code": "TEST-1000",
                    "term": "26W",
                    "credits_earned": 3,
                    "status": "Completed",
                }
            ]
        },
    )
    assert put_history.status_code == 200, put_history.json()
    assert client.get("/api/v1/students/111/profile").json()["history"] == [
        {
            "course_code": "TEST-1000",
            "term": "26W",
            "credits_earned": 3,
            "status": "Completed",
        }
    ]

    delete_plan = client.delete("/api/v1/students/111/plan")
    assert delete_plan.status_code == 200, delete_plan.json()
    assert client.get("/api/v1/students/111/profile").json()["plan"] == []


def test_unknown_student_routes_return_404() -> None:
    reset_students()

    assert client.get("/api/v1/students/999/profile").status_code == 404
    assert client.delete("/api/v1/students/999/history").status_code == 404
    assert (
        client.post(
            "/api/v1/students/999/plan",
            json={"planned_courses": [{"course_code": "COSC-3506", "term": "26F"}]},
        ).status_code
        == 404
    )


def test_student_profiles_are_isolated() -> None:
    reset_students()

    first_html = """
    <table>
      <tr><th>Status</th><th>Course</th><th></th><th>Grade</th><th>Term</th><th>Credits</th></tr>
      <tr><td>Completed</td><td>AAAA-1000</td><td>First</td><td>80</td><td>24F</td><td>3</td></tr>
    </table>
    """
    second_html = """
    <table>
      <tr><th>Status</th><th>Course</th><th></th><th>Grade</th><th>Term</th><th>Credits</th></tr>
      <tr><td>Completed</td><td>BBBB-2000</td><td>Second</td><td>75</td><td>25W</td><td>3</td></tr>
    </table>
    """

    first_response = client.post(
        "/api/v1/students/alpha/history/import",
        files={"file": ("first.html", first_html.encode("utf-8"), "text/html")},
    )
    second_response = client.post(
        "/api/v1/students/beta/history/import",
        files={"file": ("second.html", second_html.encode("utf-8"), "text/html")},
    )
    assert first_response.status_code == 201, first_response.json()
    assert second_response.status_code == 201, second_response.json()

    alpha = client.get("/api/v1/students/alpha/profile").json()
    beta = client.get("/api/v1/students/beta/profile").json()

    assert alpha["history"] == [
        {
            "course_code": "AAAA-1000",
            "term": "24F",
            "credits_earned": 3,
            "status": "Completed",
        }
    ]
    assert beta["history"] == [
        {
            "course_code": "BBBB-2000",
            "term": "25W",
            "credits_earned": 3,
            "status": "Completed",
        }
    ]
