from fastapi.testclient import TestClient
from backend.app import app


def test_page_urls_serve_app_and_keep_api_routes_separate():
    client = TestClient(app)
    for path in ["/", "/login", "/signup", "/upload-file", "/choose-checks", "/business-rules", "/results", "/file-details", "/history", "/available-checks"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"]
        assert 'id="auth-form"' in response.text
    assert client.get('/does-not-exist').status_code == 404
    assert client.get('/api/datasets/not-a-dataset/profile').status_code == 401


def test_page_assets_and_trailing_slash_urls():
    import re
    client = TestClient(app)
    for path in ['/login', '/signup', '/upload-file', '/choose-checks', '/business-rules', '/results', '/file-details', '/history', '/available-checks', '/admin']:
        response = client.get(path + '/')
        assert response.status_code == 200, path
        for asset in re.findall(r'(?:src|href)="(/static/[^"#]+)"', response.text):
            assert client.get(asset).status_code == 200, (path, asset)


def test_authenticated_file_and_result_deep_links(tmp_path, monkeypatch):
    import backend.storage as storage
    monkeypatch.setattr(storage, 'UPLOAD_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(storage, 'RUN_DIR', tmp_path / 'runs')
    storage.UPLOAD_DIR.mkdir()
    storage.RUN_DIR.mkdir()
    client = TestClient(app)
    account = {'first_name':'Route', 'last_name':'Tester', 'company':'Feuji', 'country_code':'+91', 'phone':'9876543210', 'email':'routes@example.com', 'password':'route-test-password'}
    assert client.post('/api/auth/signup', json=account).status_code == 201
    assert client.post('/api/auth/login', json={'email':account['email'], 'password':account['password']}).status_code == 200
    upload = client.post('/api/datasets', files={'file':('routes.csv',b'id,amount\n1,10\n2,20\n','text/csv')})
    assert upload.status_code == 200
    assert upload.json()['dataset']['filename'] == 'routes.csv'
    dataset_id = upload.json()['dataset']['dataset_id']
    for path in ['/upload-file', '/choose-checks', '/business-rules', '/file-details']:
        assert client.get(path, params={'dataset':dataset_id}).status_code == 200
    restored = client.get(f'/api/datasets/{dataset_id}/profile').json()
    assert restored['profile']['row_count'] == 2
    assert restored['dataset']['filename'] == 'routes.csv'
    run = client.post('/api/runs', json={'dataset_id':dataset_id, 'selected_rules':[{'rule_id':'DQ-DUPLICATE-ROWS','configuration':{}}], 'approved':True})
    assert run.status_code == 200, run.text
    assert run.json()['dataset_filename'] == 'routes.csv'
    run_id = run.json()['run_id']
    assert client.get('/results', params={'run':run_id}).status_code == 200
    assert client.get(f'/api/runs/{run_id}').json()['accepted_count'] == 2
    assert client.get(f'/api/runs/{run_id}/download/accepted').status_code == 200
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get(f'/api/runs/{run_id}').status_code == 401
