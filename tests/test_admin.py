import time
from fastapi.testclient import TestClient
from backend.app import app
from backend import auth
ACCOUNT = {"first_name":"Jane","last_name":"Doe","company":"Feuji","country_code":"+91","country_region":"IN","phone":"9876543210","email":"Jane@example.com","password":"my-password123"}


def test_admin_permissions_and_lifecycle(monkeypatch):
    monkeypatch.setenv("DQ_ADMIN_PASSWORD", "test-admin-secret")
    with TestClient(app) as admin, TestClient(app) as user:
        assert admin.get("/admin").status_code == 200
        for path in ("/users","/logs","/me"):
            assert admin.get("/api/admin"+path).status_code == 401
        assert admin.post("/api/admin/users",json=ACCOUNT).status_code == 401
        assert admin.post("/api/admin/login",json={"password":"wrong"}).status_code == 401
        assert admin.post("/api/admin/login",json={"password":"test-admin-secret"},headers={"Origin":"https://elsewhere.example"}).status_code == 403
        response=admin.post("/api/admin/login",json={"password":"test-admin-secret"})
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert admin.post("/api/admin/users",json=ACCOUNT).status_code == 201
        rows=admin.get("/api/admin/users").json()
        assert len(rows)==1 and rows[0]["created_at"]>0
        assert "password_hash" not in rows[0] and "salt" not in rows[0]
        uid=rows[0]["id"]
        assert user.post("/api/auth/login",json={"email":ACCOUNT["email"],"password":ACCOUNT["password"]}).status_code == 200
        assert user.get("/api/admin/users").status_code == 401
        assert admin.get("/api/rules").status_code == 401
        logs=admin.get("/api/admin/logs").json()
        assert logs[0]["status"]=="Active" and logs[0]["logout_at"] is None and "token" not in logs[0]
        user.post("/api/auth/logout")
        assert admin.get("/api/admin/logs").json()[0]["logout_at"]>=logs[0]["login_at"]
        user.post("/api/auth/login",json={"email":ACCOUNT["email"],"password":ACCOUNT["password"]})
        edit={**ACCOUNT,"first_name":"Updated"};edit.pop("password")
        assert admin.put(f"/api/admin/users/{uid}",json=edit).status_code==200
        assert user.get("/api/auth/me").status_code==401
        assert user.post("/api/auth/login",json={"email":ACCOUNT["email"],"password":ACCOUNT["password"]}).status_code==200
        assert admin.put(f"/api/admin/users/{uid}",json={**edit,"password":"new-password123"}).status_code==200
        assert user.post("/api/auth/login",json={"email":ACCOUNT["email"],"password":ACCOUNT["password"]}).status_code==401
        assert user.post("/api/auth/login",json={"email":ACCOUNT["email"],"password":"new-password123"}).status_code==200
        assert admin.delete(f"/api/admin/users/{uid}").status_code==200
        assert user.get("/api/auth/me").status_code==401
        assert admin.get("/api/admin/users").json()==[]
        assert len(admin.get("/api/admin/logs").json())==4
        assert admin.post("/api/admin/users",json={**ACCOUNT,"email":"new@example.com"}).status_code==201
        assert admin.get("/api/admin/users").json()[0]["id"]!=uid
        assert admin.post("/api/admin/logout").status_code==200
        assert admin.get("/api/admin/logs").status_code==401


def test_admin_edit_validation_and_expiry(monkeypatch):
    monkeypatch.setenv("DQ_ADMIN_PASSWORD", "test-admin-secret")
    with TestClient(app) as client:
        client.post("/api/admin/login",json={"password":"test-admin-secret"})
        client.post("/api/admin/users",json=ACCOUNT)
        uid=client.get("/api/admin/users").json()[0]["id"]
        assert client.put(f"/api/admin/users/{uid}",json={**ACCOUNT,"phone":"letters"}).status_code==400
        assert client.put(f"/api/admin/users/{uid}",json={**ACCOUNT,"password":"short"}).status_code==400
        assert client.delete("/api/admin/users/999999").status_code==404
        with auth.connect() as db:
            db.execute("UPDATE admin_sessions SET expires=0")
        assert client.get("/api/admin/users").status_code==401
