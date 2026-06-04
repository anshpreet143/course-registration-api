"""Local tests for Project Part 1 catalog ingestion."""

from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_import_and_lookup_with_and_without_space() -> None:
    with open("sample_catalog.html", "rb") as catalog:
        response = client.post(
            "/api/v1/admin/catalog/import",
            files={"file": ("sample_catalog.html", catalog, "text/html")},
        )

    assert response.status_code == 200, response.json()
    assert response.json()["imported"] == 5

    no_space = client.get("/api/v1/catalog/courses/COSC3506")
    with_space = client.get("/api/v1/catalog/courses/COSC%203506")

    for response in [no_space, with_space]:
        assert response.status_code == 200, response.json()
        assert response.json() == {
            "course_code": "COSC 3506",
            "title": "Software Systems Development",
            "credits": 3,
            "prerequisites": "COSC 2007",
            "cross_listed": "ITEC 3506",
        }


def test_hidden_style_different_course_code_is_not_hardcoded() -> None:
    html = """
    <table>
      <tr>
        <th>Course Code</th><th>Title</th><th>Credits</th>
        <th>Prerequisites</th><th>Cross-listed</th>
      </tr>
      <tr>
        <td>ABCD 1234</td><td>Hidden Test Course</td><td>4</td>
        <td>ABCD 1000</td><td>EFGH 1234</td>
      </tr>
    </table>
    """
    response = client.post(
        "/api/v1/admin/catalog/import",
        files={"file": ("hidden.html", html.encode("utf-8"), "text/html")},
    )
    assert response.status_code == 200, response.json()

    lookup = client.get("/api/v1/catalog/courses/ABCD1234")
    assert lookup.status_code == 200, lookup.json()
    assert lookup.json()["course_code"] == "ABCD 1234"
    assert lookup.json()["title"] == "Hidden Test Course"
    assert lookup.json()["credits"] == 4

