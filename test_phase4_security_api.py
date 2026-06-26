"""Local tests for Project Phase 4 security and recommendation behavior."""

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def reset_state() -> None:
    main.catalog_by_normalized_code.clear()
    main.students_by_id.clear()
    main.users_by_username.clear()
    main.audit_request_times.clear()
    main.seed_admin_user()


def register_and_login(username: str, password: str = "MyPass1!") -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.json()

    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.json()
    return login.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def import_catalog(html: str) -> None:
    response = client.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("catalog.html", html.encode("utf-8"), "text/html")},
    )
    assert response.status_code == 200, response.json()


def test_auth_lifecycle_and_admin_login() -> None:
    reset_state()

    register = client.post(
        "/api/v1/auth/register",
        json={"username": "s12345", "password": "MyPass1!"},
    )
    assert register.status_code == 201, register.json()
    assert register.json() == {"status": "registered"}
    assert main.users_by_username["s12345"]["password_hash"] != "MyPass1!"

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": "s12345", "password": "MyPass1!"},
    )
    assert duplicate.status_code == 409

    bad_login = client.post(
        "/api/v1/auth/login",
        json={"username": "s12345", "password": "wrong"},
    )
    assert bad_login.status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "s12345", "password": "MyPass1!"},
    )
    assert login.status_code == 200, login.json()
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"
    assert len(token) > 30

    claims = main.decode_token(token)
    assert claims["sub"] == "s12345"
    assert claims["role"] == "user"

    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert admin_login.status_code == 200, admin_login.json()
    admin_claims = main.decode_token(admin_login.json()["access_token"])
    assert admin_claims["role"] == "admin"


def test_history_import_bola_owner_required() -> None:
    reset_state()
    owner_token = register_and_login("s12345")
    other_token = register_and_login("s99999")

    with open("student-example.html", "rb") as transcript:
        no_token = client.post(
            "/api/v1/students/s12345/history/import",
            files={"file": ("student-example.html", transcript, "text/html")},
        )
    assert no_token.status_code == 401

    with open("student-example.html", "rb") as transcript:
        wrong_owner = client.post(
            "/api/v1/students/s12345/history/import",
            files={"file": ("student-example.html", transcript, "text/html")},
            headers=bearer(other_token),
        )
    assert wrong_owner.status_code == 401

    with open("student-example.html", "rb") as transcript:
        owner = client.post(
            "/api/v1/students/s12345/history/import",
            files={"file": ("student-example.html", transcript, "text/html")},
            headers=bearer(owner_token),
        )
    assert owner.status_code == 201, owner.json()


def test_profile_and_plan_rbac_owner_or_admin() -> None:
    reset_state()
    owner_token = register_and_login("s12345")
    other_token = register_and_login("s99999")
    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    admin_token = admin_login.json()["access_token"]

    main.students_by_id["s12345"] = main.StudentProfile(
        student_id="s12345",
        history=[
            main.PastCourse(
                course_code="COSC-2006",
                term="24W",
                credits_earned=3,
                status="Completed",
            )
        ],
        plan=[main.PlannedCourse(course_code="COSC-2007", term="26F")],
    )

    assert client.get("/api/v1/students/s12345/profile").status_code == 401
    assert (
        client.get(
            "/api/v1/students/s12345/profile", headers=bearer(other_token)
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/students/s12345/profile", headers=bearer(owner_token)
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/students/s12345/profile", headers=bearer(admin_token)
        ).status_code
        == 200
    )

    plan = client.get("/api/v1/students/s12345/plan", headers=bearer(owner_token))
    assert plan.status_code == 200, plan.json()
    assert plan.json()["planned_courses"] == [
        {"course_code": "COSC-2007", "term": "26F"}
    ]


def test_audit_report_rate_limit_allows_ten_then_blocks_eleventh() -> None:
    reset_state()
    token = register_and_login("s12345")
    main.students_by_id["s12345"] = main.StudentProfile(student_id="s12345")

    status_codes = [
        client.get(
            "/api/v1/students/s12345/audit-report", headers=bearer(token)
        ).status_code
        for _ in range(11)
    ]

    assert status_codes[:10] == [200] * 10
    assert status_codes[10] == 429


def test_recommendations_use_topological_order_and_rbac() -> None:
    reset_state()
    token = register_and_login("s12345")
    import_catalog(
        """
        <table>
          <tr>
            <th>Course Code</th><th>Title</th><th>Credits</th>
            <th>Prerequisites</th><th>Cross-listed</th>
          </tr>
          <tr><td>COSC 1000</td><td>Intro</td><td>3</td><td></td><td></td></tr>
          <tr><td>COSC 2000</td><td>Middle</td><td>3</td><td>COSC 1000</td><td></td></tr>
          <tr><td>COSC 3000</td><td>Advanced</td><td>3</td><td>COSC 2000</td><td></td></tr>
          <tr><td>MATH 1000</td><td>Math</td><td>3</td><td></td><td></td></tr>
        </table>
        """
    )
    main.students_by_id["s12345"] = main.StudentProfile(
        student_id="s12345",
        history=[
            main.PastCourse(
                course_code="COSC-1000",
                term="24F",
                credits_earned=3,
                status="Completed",
            )
        ],
    )

    unauthenticated = client.get("/api/v1/students/s12345/recommendations")
    assert unauthenticated.status_code == 401

    response = client.get(
        "/api/v1/students/s12345/recommendations",
        headers=bearer(token),
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["student_id"] == "s12345"
    assert isinstance(body["recommended_pathway"], list)

    all_recommended = [
        course
        for term_group in body["recommended_pathway"]
        for course in term_group["courses"]
    ]
    assert "COSC 1000" not in all_recommended
    assert "COSC 2000" in all_recommended
    assert "COSC 3000" in all_recommended

    term_index = {
        course: index
        for index, term_group in enumerate(body["recommended_pathway"])
        for course in term_group["courses"]
    }
    assert term_index["COSC 2000"] < term_index["COSC 3000"]
