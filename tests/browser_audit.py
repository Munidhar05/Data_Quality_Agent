"""Run manually: .venv/Scripts/python.exe tests/browser_audit.py (requires Playwright and Edge)."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
import backend.app as api
import backend.auth as auth
import backend.storage as storage
from playwright.sync_api import sync_playwright, expect


def run():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    with tempfile.TemporaryDirectory(prefix="dq-browser-fixes-") as temp:
        root = Path(temp)
        auth.DB_PATH = root / "accounts.sqlite3"
        storage.UPLOAD_DIR = root / "uploads"; storage.UPLOAD_DIR.mkdir()
        storage.RUN_DIR = root / "runs"; storage.RUN_DIR.mkdir()
        api.UPLOAD_DIR = storage.UPLOAD_DIR
        api.llm_provider.configured = False
        os.environ["DQ_ADMIN_PASSWORD"] = "browser-admin-secret"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        try:
            for _ in range(100):
                if server.started: break
                time.sleep(.1)
            assert server.started
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="msedge", headless=True)
                try:
                    context = browser.new_context(viewport={"width":1440,"height":900})
                    page = context.new_page(); page.set_default_timeout(15000)
                    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + "/signup/")
                    expect(page.locator("#auth-heading")).to_have_text("Create your account")
                    page.wait_for_function("document.querySelector('#country-code option').dataset.region")
                    for key, value in {"first_name":"Audit", "last_name":"Tester", "company":"Audit Company", "phone":"9876543210", "email":"audit@example.com", "password":"Audit-password-123"}.items():
                        page.locator(f"[name={key}]").fill(value)
                    page.locator("#auth-submit").click()
                    expect(page.locator("#auth-heading")).to_have_text("Welcome back")
                    page.locator("[name=password]").fill("Audit-password-123")
                    page.locator("#auth-submit").click()
                    expect(page.locator("#welcome-upload")).to_be_visible()
                    with page.expect_download() as sample:
                        page.locator("a[download='sample.csv']").click()
                    assert sample.value.suggested_filename == "sample.csv"
                    csv = b"id,amount,status,email\n001,10.00,active,a@example.com\n002,-1.00,active,bad\n002,-1.00,active,bad\n"
                    page.locator("#file-input").set_input_files({"name":"Audit File.csv", "mimeType":"text/csv", "buffer":csv})
                    expect(page.locator("#workspace-content")).to_be_visible()
                    dataset_url = page.url
                    dataset_id = page.evaluate("state.dataset.dataset_id")
                    page.locator(".rail-link[data-page=review]").click()
                    expect(page.locator(".rail-link[data-page=review]")).to_have_attribute("aria-current", "page")
                    assert page.evaluate("document.activeElement === document.querySelector('#page-review h1')")
                    count = page.locator(".rule-card").count()
                    page.locator("[data-step-business]").click()
                    page.locator("#business-input").fill("amount must be greater than 0")
                    page.reload()
                    expect(page.locator("#business-input")).to_have_value("amount must be greater than 0")
                    assert page.locator(".rule-card").count() == count
                    expect(page.locator("#approve-run")).to_have_text("Generate & review rules")
                    page.locator("#approve-run").click()
                    expect(page.locator(".business-rule-item")).to_have_count(1)
                    expect(page.locator("#business-input")).to_have_value("")
                    expect(page.locator("#page-business")).to_have_class("page active")
                    assert context.request.get(base + "/api/runs").json() == []
                    # Mixed input adds supported rules but never validates unresolved text.
                    page.locator("#business-input").fill("amount must be greater than 0\namount should be valid")
                    page.locator("#approve-run").click()
                    expect(page.locator("#business-input")).to_have_value("amount should be valid")
                    expect(page.locator(".business-rule-item")).to_have_count(1)
                    assert context.request.get(base + "/api/runs").json() == []
                    page.locator("#approve-run").click()
                    expect(page.locator("#business-feedback")).to_contain_text("amount should be valid")
                    assert context.request.get(base + "/api/runs").json() == []
                    # User explicitly removes the unresolved requirement before approving.
                    page.locator("#business-input").fill("")
                    expect(page.locator("#approve-run")).to_have_text("Run validation")
                    page.reload()
                    expect(page.locator(".business-rule-item")).to_have_count(1)
                    business_url = page.url
                    page.locator("#approve-run").click()
                    expect(page.locator("#dq-summary-report")).to_be_visible()
                    expect(page.locator(".report-score")).to_contain_text("Quality score:")
                    run_url = page.url
                    run_id = page.evaluate("state.lastRun.run_id")
                    assert page.evaluate("state.lastRun.accepted_count") == 1
                    assert page.evaluate("state.lastRun.rejected_count") == 2
                    assert page.evaluate("state.lastRun.business_rules.length") == 1
                    with page.expect_download() as accepted:
                        page.locator("[data-download-records=accepted]").click()
                    text = Path(accepted.value.path()).read_text()
                    assert "001,10.00" in text
                    with page.expect_download() as rejected:
                        page.locator("[data-download-records=rejected]").click()
                    assert "002,-1.00" in Path(rejected.value.path()).read_text()
                    print("PASS signup/login, sample, complete validation, CSV value preservation and refresh persistence")
                    for fmt in ("html", "csv", "json", "svg", "png", "jpeg", "webp"):
                        page.locator("#report-download-menu summary").click()
                        with page.expect_download() as exported:
                            page.locator(f"[data-report-format={fmt}]").click()
                        assert Path(exported.value.path()).stat().st_size > 0
                        print("PASS report export", fmt)
                    page.evaluate("window.print = () => { window.auditPrinted = true; }")
                    page.locator("#report-download-menu summary").click(); page.locator("[data-report-format=pdf]").click()
                    assert page.evaluate("window.auditPrinted")
                    page.locator(".rail-link[data-page=history]").click()
                    expect(page.locator(".history-open")).to_have_text("Audit File.csv")
                    page.goto(run_url); expect(page.locator("#dq-summary-report")).to_be_visible()
                    page.locator("#back-to-review").click()
                    expect(page.locator("#page-business")).to_have_class("page active")
                    expect(page.locator(".business-rule-item")).to_have_count(1)
                    assert page.evaluate("state.dataset.dataset_id") == dataset_id
                    page.goto(base + "/available-checks")
                    expect(page.locator("#catalog-help")).to_have_text("Explore 19 data quality checks, grouped into 7 categories.")
                    for url in (base + "/available-checks", run_url, business_url):
                        page.set_viewport_size({"width":390,"height":844});page.goto(url)
                        page.wait_for_function("!document.body.classList.contains('auth-pending') && document.querySelector('.rail-link[aria-current]')")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), url
                    print("PASS results navigation, score, history filenames, catalog, mobile overflow")
                    page.goto(run_url);expect(page.locator("#dq-summary-report")).to_be_visible()
                    page.locator(".dq-audit-details summary").click()
                    page.evaluate("window.auditFailures = Array.from({length: 601}, (_, i) => ({rule_name: 'Test', column: 'amount', reason: 'Invalid', actual_value: i === 0 ? 0 : i === 1 ? false : i === 2 ? '' : i, status: 'FAIL', row_index: i})); renderFailures(window.auditFailures)")
                    expect(page.locator("#failure-list > p")).to_contain_text("of 601 matching issues")
                    assert page.locator(".failure-row:not(.failure-head)").count() == 100
                    assert page.locator(".failure-row:not(.failure-head)").nth(0).locator(".failure-cell").last.inner_text() == "0"
                    assert page.locator(".failure-row:not(.failure-head)").nth(1).locator(".failure-cell").last.inner_text() == "false"
                    assert page.locator(".failure-row:not(.failure-head)").nth(2).locator(".failure-cell").last.inner_text() == "(empty)"
                    page.locator("#failure-next").click();expect(page.locator("#failure-list > p")).to_contain_text("101")
                    page.evaluate("renderFailures(window.auditFailures, 'false')");expect(page.locator(".failure-row:not(.failure-head)")).to_have_count(1)
                    page.goto(dataset_url);expect(page.locator("#workspace-content")).to_be_visible()
                    csv50 = "id,amount\n" + "\n".join(f"{i:03d},{i}" for i in range(50)) + "\n"
                    page.locator("#file-input").set_input_files({"name":"50 rows.csv", "mimeType":"text/csv", "buffer":csv50.encode()})
                    expect(page.locator("#active-dataset-name")).to_have_text("50 rows.csv")
                    page.locator(".rail-link[data-page=profile]").click();expect(page.locator("#explorer-table tbody tr")).to_have_count(50)
                    expect(page.locator("#explorer-next")).to_be_disabled()
                    page.locator("#explorer-search").fill("049");page.locator("#explorer-search-button").click();expect(page.locator("#explorer-table tbody tr")).to_have_count(1)
                    page.locator(".rail-link[data-page=review]").click()
                    # Add schema check and edit the expected fields rather than using detected schema.
                    page.locator(".add-check-picker > summary").click()
                    button = page.locator(".add-optional-check[data-rule-id='DQ-CATALOG-001']")
                    button.evaluate("el => el.closest('details').open = true")
                    button.click()
                    schema = page.locator("[data-param-key=schema]")
                    expect(schema).to_be_visible();schema.fill('{"id":"StringType","amount":"LongType"}')
                    page.reload();expect(page.locator("[data-param-key=schema]")).to_have_value('{"id":"StringType","amount":"LongType"}')
                    # Reference filename survives card rendering and refresh.
                    page.locator(".add-check-picker > summary").click()
                    reference = page.locator(".add-optional-check[data-rule-id='DQ-CATALOG-010']")
                    reference.evaluate("el => el.closest('details').open = true");reference.click()
                    page.locator(".reference-file-input").set_input_files({"name":"Reference.csv", "mimeType":"text/csv", "buffer":b"id\n000\n"})
                    expect(page.locator(".reference-status")).to_have_text("Reference ready: Reference.csv")
                    page.reload();expect(page.locator(".reference-status")).to_have_text("Reference ready: Reference.csv")
                    print("PASS failure pagination, zero/false, final record page, schema editing and reference draft")
                    other = browser.new_context();op = other.new_page();op.goto(business_url)
                    expect(op.locator("#auth-submit")).to_be_visible()
                    op.wait_for_url("**/login?next=*")
                    account = {"first_name":"Other", "last_name":"Tester", "company":"Audit", "country_code":"+91", "phone":"9876543210", "email":"other@example.com", "password":"Other-password-123"}
                    assert other.request.post(base + "/api/auth/signup", data=account).status == 201
                    assert other.request.post(base + "/api/auth/login", data={"email":account["email"],"password":account["password"]}).status == 200
                    assert other.request.get(base + "/api/runs").json() == []
                    assert other.request.get(base + f"/api/datasets/{dataset_id}/profile").status == 404
                    assert other.request.get(base + f"/api/runs/{run_id}/download/accepted").status == 404
                    admin = context.new_page();admin.goto(base + "/admin")
                    admin.locator("#admin-login-form [name=password]").fill("browser-admin-secret");admin.locator("#admin-login-form button").click()
                    expect(admin.locator("#admin-workspace")).to_be_visible()
                    admin.locator("#create-user").click()
                    for key, value in {"first_name":"New", "last_name":"User", "company":"Test", "email":"created@example.com", "phone":"9876543210", "password":"Created-password-123"}.items():admin.locator(f"#user-form [name={key}]").fill(value)
                    admin.locator("#user-form button[type=submit]").click()
                    row = admin.locator("#users-body tr").filter(has_text="created@example.com")
                    expect(row).to_be_visible();row.locator("[data-edit]").click();admin.locator("#user-form [name=company]").fill("Updated Company");admin.locator("#user-form button[type=submit]").click();expect(row).to_contain_text("Updated Company")
                    row.locator("[data-delete]").click();admin.locator("#confirm-delete").click();expect(row).to_have_count(0)
                    print("PASS login return links, account isolation and admin create/edit/delete")
                    from test_file_inputs import fixture_content
                    for extension in (".xlsx", ".json", ".tsv", ".xml", ".avro"):
                        page.goto(base + "/upload-file")
                        page.locator("#file-input").set_input_files({"name": "Browser input" + extension, "mimeType": "application/octet-stream", "buffer": fixture_content(extension)})
                        expect(page.locator("#active-dataset-name")).to_have_text("Browser input" + extension)
                        assert page.evaluate("state.profile.row_count") == 2
                        if extension == ".xlsx":
                            expect(page.locator("#upload-help")).to_contain_text("Records")
                        page.reload()
                        expect(page.locator("#active-dataset-name")).to_have_text("Browser input" + extension)
                    print("PASS Excel, JSON, TSV, XML and Avro browser uploads and refresh")
                    assert not errors, errors
                    print("PASS browser audit: no application JavaScript errors")
                finally:
                    browser.close()
        finally:
            server.should_exit = True;thread.join(10)

if __name__ == "__main__":
    run()
