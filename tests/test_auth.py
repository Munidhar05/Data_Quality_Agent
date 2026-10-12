from fastapi.testclient import TestClient
from backend.app import app
import backend.auth as auth

ACCOUNT = {"first_name":"Jane", "last_name":"Doe", "company":"Feuji", "country_code":"+91", "phone":"9876543210", "email":"Jane@example.com", "password":"my-password123"}

def test_account_lifecycle():
    with TestClient(app) as client:
        assert client.get("/api/rules").status_code == 401
        assert client.post("/api/auth/signup", json=ACCOUNT).status_code == 201
        assert client.post("/api/auth/signup", json=ACCOUNT).status_code == 409
        assert client.post("/api/auth/login", json={"email":ACCOUNT["email"], "password":"wrong"}).status_code == 401
        response = client.post("/api/auth/login", json={"email":"jane@example.com", "password":ACCOUNT["password"]})
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert client.get("/api/auth/me").json()["user"]["first_name"] == "Jane"
        assert client.get("/api/rules").status_code == 200
        with auth.connect() as db:
            row = db.execute("SELECT * FROM users").fetchone()
            assert row["password_hash"] != ACCOUNT["password"]
            db.execute("UPDATE sessions SET expires = 0")
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/login", json={"email":ACCOUNT["email"], "password":ACCOUNT["password"]}).status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/rules").status_code == 401

def test_invalid_registration_and_origin():
    with TestClient(app) as client:
        for changes in ({"email":"invalid"}, {"phone":"letters"}, {"password":"short"}, {"first_name":" "}):
            assert client.post("/api/auth/signup", json={**ACCOUNT, **changes}).status_code in (400, 422)
        assert client.post("/api/auth/signup", json=ACCOUNT, headers={"Origin":"https://other.example"}).status_code == 403


def test_phone_and_email_validation():
    with TestClient(app) as client:
        for phone in ("987654321", "98765432101", "+919876543210", "98765 43210", "98765abcde", "??????????"):
            assert client.post("/api/auth/signup", json={**ACCOUNT, "phone":phone}).status_code == 400
        for email in ("a..b@example.com", ".a@example.com", "a@-example.com", "a@example..com", "a@example", "a b@example.com", "a@domain.c"):
            assert client.post("/api/auth/signup", json={**ACCOUNT, "email":email}).status_code == 422
            assert client.post("/api/auth/login", json={"email":email, "password":"password"}).status_code == 422
        assert client.post("/api/auth/signup", json={**ACCOUNT, "country_code":"+999"}).status_code == 400
        assert client.post("/api/auth/signup", json=ACCOUNT).status_code == 201
        client.post("/api/auth/login", json={"email":ACCOUNT["email"], "password":ACCOUNT["password"]})
        assert client.get("/api/auth/me").json()["user"]["phone"] == "+919876543210"
        assert client.post("/api/auth/signup", json={**ACCOUNT, "email":"other@example.co.uk", "country_code":"+44", "phone":"7700900123"}).status_code == 201


def test_all_country_codes_are_available_and_accepted():
    with TestClient(app) as client:
        response = client.get("/api/auth/countries")
        assert response.status_code == 200
        countries = response.json()
        regions = {country["region"]: country["code"] for country in countries}
        assert len(regions) > 240
        assert regions["IN"] == "+91"
        assert regions["US"] == regions["CA"] == "+1"
        assert regions["GB"] == regions["JE"] == "+44"
        assert regions["XK"] == "+383"
        assert regions["NZ"] == "+64"
        assert {country["code"] for country in countries} == auth.COUNTRY_CODES
        for code in ("+81", "+49", "+55", "+27", "+383", "+683"):
            phone = "1" * next(country["lengths"][0] for country in countries if country["code"] == code)
            account = {**ACCOUNT, "country_code":code, "phone":phone, "email":f"country{code[1:]}@example.com"}
            assert client.post("/api/auth/signup", json=account).status_code == 201
        assert client.post("/api/auth/signup", json={**ACCOUNT,"country_code":"+999"}).status_code == 400
        assert client.post("/api/auth/signup", json={**ACCOUNT,"country_code":"+49","phone":"1"*14}).status_code == 400


def test_basic_field_validation():
    with TestClient(app) as client:
        for field, value in (("first_name", "123"), ("last_name", "Doe7"), ("first_name", "<script>"), ("company", "   "), ("company", "---"), ("company", "Bad\nCompany"), ("password", "        "), ("first_name", ""), ("phone", "abcdefghij")):
            response = client.post("/api/auth/signup", json={**ACCOUNT, field:value})
            assert response.status_code in (400,422), (field, value, response.text)
        assert client.post("/api/auth/login", json={"email":ACCOUNT["email"], "password":" "}).status_code == 422
        account = {**ACCOUNT, "first_name":"Anne-Marie", "last_name":"O'Connor", "company":"A & B Solutions, Ltd."}
        assert client.post("/api/auth/signup", json=account).status_code == 201
        assert client.post("/api/auth/signup", json={**ACCOUNT,"email":"JANE@EXAMPLE.COM"}).status_code == 409


def test_country_specific_phone_lengths():
    with TestClient(app) as client:
        cases = [("AF", "+93", 9), ("SG", "+65", 8), ("BR", "+55", 11), ("DE", "+49", 12)]
        for region, code, length in cases:
            data = {**ACCOUNT, "country_region":region, "country_code":code, "phone":"1"*length, "email":f"length{region}@example.com"}
            assert client.post("/api/auth/signup", json=data).status_code == 201
        for region, code, length in (("AF", "+93", 11), ("SG", "+65", 12), ("IN", "+91", 8)):
            assert client.post("/api/auth/signup", json={**ACCOUNT, "country_region":region, "country_code":code, "phone":"1"*length}).status_code == 400
        assert client.post("/api/auth/signup", json={**ACCOUNT,"country_region":"AF"}).status_code == 400
