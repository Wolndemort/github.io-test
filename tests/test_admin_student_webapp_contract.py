from pathlib import Path


def test_student_webapp_creation_does_not_report_false_failure_after_success():
    page = (Path(__file__).parents[1] / "templates/admin_students.html").read_text(encoding="utf-8")
    assert "const form = input.closest('form');" in page
    assert "form.dataset.deleteAttached" in page
    assert "list.insertAdjacentHTML('afterbegin', studentCardHtml(s));" in page


def test_student_dates_use_one_human_format():
    page = (Path(__file__).parents[1] / "templates/admin_students.html").read_text(encoding="utf-8")
    assert 'placeholder="ДД.ММ.ГГГГ"' in page
    assert 'name="expire_date" type="date"' not in page


def test_student_delete_has_matching_api_route_and_payload():
    page = (Path(__file__).parents[1] / "templates/admin_students.html").read_text(encoding="utf-8")
    api = (Path(__file__).parents[1] / "admin_module/api.py").read_text(encoding="utf-8")
    assert "method: 'DELETE'" in page
    assert '@router.delete("/admin/students/{student_id}")' in api
    assert "await db.delete(student)" in api
