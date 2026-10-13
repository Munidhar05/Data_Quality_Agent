import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from backend import auth

COOKIE = "dq_admin_session"
def require_admin(request: Request):
    token = request.cookies.get(COOKIE, "")
    with auth.connect() as db:
        session = db.execute("SELECT token FROM admin_sessions WHERE token=? AND expires>?", (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
    if not session:
        raise HTTPException(401, "Sign in as administrator to continue.")

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
login_router = APIRouter(prefix="/api/admin")
class AdminCredentials(BaseModel):
    password: str = Field(min_length=1,max_length=128)
class UserEdit(auth.Registration):
    password: str = "unchanged-password"

@login_router.post("/login")
def login(data: AdminCredentials, request: Request, response: Response):
    configured_password = os.environ.get("DQ_ADMIN_PASSWORD", "")
    if not configured_password.strip():
        raise HTTPException(503, "Administration is unavailable. Ask the server administrator to configure DQ_ADMIN_PASSWORD.")
    if not hmac.compare_digest(data.password.encode(), configured_password.encode()):
        raise HTTPException(401, "Incorrect administrator password.")
    token = secrets.token_urlsafe(32)
    with auth.connect() as db:
        db.execute("DELETE FROM admin_sessions WHERE expires<=? OR token=?", (time.time(),hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()))
        db.execute("INSERT INTO admin_sessions VALUES (?,?)", (hashlib.sha256(token.encode()).hexdigest(),time.time()+28800))
    response.set_cookie(COOKIE,token,max_age=28800,httponly=True,samesite="strict",secure=request.url.scheme=="https",path="/")
    return {"message":"Signed in."}

@router.get("/me")
def me():
    return {"admin":True}

@router.post("/logout")
def logout(request: Request,response: Response):
    with auth.connect() as db:
        db.execute("DELETE FROM admin_sessions WHERE token=?",(hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path="/")
    return {"message":"Signed out."}

def list_users(db):
    return [dict(row) for row in db.execute("SELECT users.id,first_name,last_name,company,phone,email,created_at,country_code,country_region,(SELECT MAX(login_at) FROM login_logs WHERE user_id=users.id) AS last_login,(SELECT COUNT(*) FROM sessions WHERE user_id=users.id AND expires>?) AS active_sessions FROM users ORDER BY id DESC", (time.time(),))]

@router.get("/users")
def users():
    with auth.connect() as db:
        return list_users(db)

@router.post("/users",status_code=201)
def create(data: auth.Registration):
    return auth.signup(data)

@router.put("/users/{user_id}")
def edit(user_id: int,data: UserEdit):
    values = auth.registration_values(data)
    with auth.connect() as db:
        if not db.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone():
            raise HTTPException(404,"User not found.")
        try:
            db.execute("UPDATE users SET first_name=?,last_name=?,company=?,phone=?,email=?,country_code=?,country_region=? WHERE id=?",(*values,data.email,data.country_code,data.country_region,user_id))
            if "password" in data.model_fields_set:
                salt = secrets.token_hex(16)
                digest = hashlib.pbkdf2_hmac("sha256",data.password.encode(),bytes.fromhex(salt),600000).hex()
                db.execute("UPDATE users SET salt=?,password_hash=? WHERE id=?",(salt,digest,user_id))
            revoke_user(db,user_id,"Account updated by admin")
        except sqlite3.IntegrityError:
            raise HTTPException(409,"An account with this email already exists.")
    return {"message":"User updated. Existing sessions were signed out."}

def revoke_user(db,user_id,reason):
    db.execute("UPDATE login_logs SET logout_at=?,end_reason=? WHERE user_id=? AND logout_at IS NULL AND expires_at>?",(time.time(),reason,user_id,time.time()))
    db.execute("DELETE FROM sessions WHERE user_id=?",(user_id,))

@router.delete("/users/{user_id}")
def delete(user_id: int):
    with auth.connect() as db:
        if not db.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone():
            raise HTTPException(404,"User not found.")
        revoke_user(db,user_id,"Account deleted by admin")
        db.execute("DELETE FROM users WHERE id=?",(user_id,))
    return {"message":"User deleted. Login history retained."}

@router.get("/logs")
def logs():
    with auth.connect() as db:
        rows = db.execute("SELECT id,user_id,email,login_at,logout_at,expires_at,end_reason FROM login_logs ORDER BY login_at DESC").fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        item["status"] = item["end_reason"] if item["logout_at"] else "Session expired" if item["expires_at"]<=time.time() else "Active"
        result.append(item)
    return result
