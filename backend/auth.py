import hashlib, hmac, json, re, secrets, sqlite3, time, unicodedata
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
DB_PATH = Path(__file__).resolve().parents[1] / "datasets" / "accounts.sqlite3"
router = APIRouter(prefix="/api/auth")
COOKIE = "dq_session"
COUNTRIES = json.loads((Path(__file__).resolve().parents[1] / "frontend" / "country-codes.json").read_text(encoding="utf-8"))
COUNTRY_CODES = {country["code"] for country in COUNTRIES}
EMAIL_PATTERN = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, company TEXT, phone TEXT, email TEXT UNIQUE, salt TEXT, password_hash TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER, expires REAL)")
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    for name, kind in (("created_at", "REAL"), ("country_code", "TEXT"), ("country_region", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE users ADD COLUMN {name} {kind}")
    db.execute("CREATE TABLE IF NOT EXISTS login_logs (id INTEGER PRIMARY KEY, user_id INTEGER, email TEXT, token TEXT, login_at REAL, logout_at REAL, expires_at REAL, end_reason TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS admin_sessions (token TEXT PRIMARY KEY, expires REAL)")
    db.commit()
    return db
class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        email = value.strip().lower()
        if not re.fullmatch(EMAIL_PATTERN, email) or len(email.split("@")[0]) > 64:
            raise ValueError("Enter a valid email address, such as you@company.com.")
        return email
    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if not value.strip():
            raise ValueError("Enter your password.")
        return value

class Registration(Credentials):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    company: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=30)
    country_code: str = "+91"
    country_region: str | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not any(unicodedata.category(char).startswith("L") for char in value) or any(not (unicodedata.category(char)[0] in "LM" or char in " '\u2019-") for char in value):
            raise ValueError("Use letters, spaces, apostrophes, or hyphens for your name.")
        return value

    @field_validator("company")
    @classmethod
    def validate_company(cls, value):
        value = value.strip()
        if not any(char.isalnum() for char in value) or any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
            raise ValueError("Enter a valid company name.")
        return value

def public_user(row):
    return {key: row[key] for key in ("id", "first_name", "last_name", "company", "phone", "email")}
def current_user(request):
    token = request.cookies.get(COOKIE)
    if not token: return None
    with connect() as db:
        row = db.execute("SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE token = ? AND expires > ?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    return public_user(row) if row else None
@router.get("/countries")
def countries():
    return COUNTRIES

def registration_values(data):
    values = [data.first_name.strip(), data.last_name.strip(), data.company.strip(), data.phone.strip()]
    email = data.email.strip().lower()
    if not all(values):
        raise HTTPException(400, "Please complete all fields.")
    if data.country_code not in COUNTRY_CODES:
        raise HTTPException(400, "Select a supported country code.")
    phone = values[3]
    matching = [country for country in COUNTRIES if country["code"] == data.country_code and (data.country_region is None or country["region"] == data.country_region)]
    if not matching:
        raise HTTPException(400, "Selected country and country code do not match.")
    lengths = sorted({length for country in matching for length in country["lengths"]})
    if not re.fullmatch(r"[0-9]+", phone) or len(phone) not in lengths or len(data.country_code[1:] + phone) > 15:
        allowed = ", ".join(map(str, lengths))
        raise HTTPException(400, f"For the selected country ({data.country_code}), enter {allowed} digits without the country code or domestic dialing prefix.")
    values[3] = data.country_code + phone
    if len(data.password) < 8: raise HTTPException(400, "Use at least 8 characters for your password.")
    return values

@router.post("/signup", status_code=201)
def signup(data: Registration):
    values = registration_values(data)
    email = data.email
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", data.password.encode(), bytes.fromhex(salt), 600000).hex()
    try:
        with connect() as db:
            next_id = db.execute("SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM users UNION ALL SELECT user_id AS id FROM login_logs)").fetchone()[0]
            db.execute("INSERT INTO users (id,first_name,last_name,company,phone,email,salt,password_hash,created_at,country_code,country_region) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (next_id,*values,email,salt,digest,time.time(),data.country_code,data.country_region))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "An account with this email already exists. Please log in.")
    return {"message": "Account created. Log in with your email and password."}
@router.post("/login")
def login(data: Credentials, request: Request, response: Response):
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (data.email.strip().lower(),)).fetchone()
    salt = row["salt"] if row else "00" * 16
    digest = hashlib.pbkdf2_hmac("sha256", data.password.encode(), bytes.fromhex(salt), 600000).hex()
    if not row or not hmac.compare_digest(digest, row["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect.")
    token = secrets.token_urlsafe(32)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        previous = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
        db.execute("UPDATE login_logs SET logout_at=?, end_reason='Replaced by new login' WHERE token=? AND logout_at IS NULL AND expires_at>?", (now, previous, now))
        db.execute("DELETE FROM sessions WHERE token=?", (previous,))
        db.execute("INSERT INTO sessions VALUES (?,?,?)", (token_hash, row["id"], now+86400))
        db.execute("INSERT INTO login_logs (user_id,email,token,login_at,expires_at) VALUES (?,?,?,?,?)", (row["id"],row["email"],token_hash,now,now+86400))
    response.set_cookie(COOKIE, token, max_age=86400, httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/")
    return {"user": public_user(row)}
@router.get("/me")
def me(request: Request):
    user = current_user(request)
    if not user: raise HTTPException(401, "Please log in to continue.")
    return {"user": user}
@router.post("/logout")
def logout(request: Request, response: Response):
    with connect() as db:
        token_hash = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
        db.execute("UPDATE login_logs SET logout_at=?, end_reason='Signed out' WHERE token=? AND logout_at IS NULL AND expires_at>?", (time.time(), token_hash, time.time()))
        db.execute("DELETE FROM sessions WHERE token = ?", (token_hash,))
    response.delete_cookie(COOKIE, path="/")
    return {"message": "Logged out."}
