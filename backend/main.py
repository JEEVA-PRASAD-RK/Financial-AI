"""
Finace — FastAPI Backend
=================================
Install dependencies:
    pip install fastapi uvicorn mysql-connector-python python-multipart
    pip install passlib[bcrypt] python-jose[cryptography]
    pip install fastapi-mail pypdf python-docx httpx
    pip install pdfplumber pillow

Run the server (NO --reload to avoid file-watcher conflicts with PDF temp files):
    uvicorn main:app --port 8000 --timeout-keep-alive 120
"""

import os
import io
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Optional
import re
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import mysql.connector
import bcrypt as _bcrypt
from jose import jwt
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pypdf import PdfReader
import docx
import json

# ── Import bank_parser — use relative imports
from .config import (
    get_database_config,
    get_jwt_secret_key,
    get_mail_config,
    get_openrouter_api_key,
)
from .bank_parser import parse_bank_pdf, get_fastapi_router

# ── PDF parsing availability check
try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    print("[WARN] pdfplumber not installed. Run: pip install pdfplumber")

from .rag import add_document, build_index, search

from .networth import router as networth_router, init_networth_tables

# ─────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"
MODEL              = "openai/gpt-4o-mini"

ALGORITHM          = "HS256"
OTP_EXPIRE_MINUTES = 10

logger = logging.getLogger(__name__)
MAILER_UNAVAILABLE_DETAIL = (
    "Email service is unavailable. Configure valid FIN_AI_MAIL_USERNAME, "
    "FIN_AI_MAIL_PASSWORD, and FIN_AI_MAIL_FROM settings."
)


def _create_mailer() -> FastMail | None:
    try:
        mail_config = get_mail_config()
        if mail_config is None:
            logger.warning("Mail service unavailable: required mail settings are missing.")
            return None
        return FastMail(ConnectionConfig(**mail_config))
    except ValueError:
        logger.warning("Mail service unavailable: mail settings are invalid.")
        return None


mailer = _create_mailer()


def _require_mailer() -> FastMail:
    if mailer is None:
        raise HTTPException(status_code=503, detail=MAILER_UNAVAILABLE_DETAIL)
    return mailer

# ─────────────────────────────────────────────────────────
#  APP — create first, then add middleware, then add router
# ─────────────────────────────────────────────────────────
app = FastAPI(title="Finace API", version="1.0")

# Middleware must come before routers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register bank_parser routes ONCE, here, after app exists ──
_bp_router = get_fastapi_router()
if _bp_router:
    app.include_router(_bp_router)
    print("[main] bank_parser routes registered OK")
else:
    print("[main] WARNING: bank_parser router not available")

# ── Register networth routes ──
app.include_router(networth_router)
print("[main] networth routes registered OK")

def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

# Global conversation memory for chat
conversation = [{"role": "system", "content": "You are a helpful financial assistant."}]

# ─────────────────────────────────────────────────────────
#  DB HELPER
# ─────────────────────────────────────────────────────────
def db_exec(sql: str, params=None, fetch=False):
    conn = mysql.connector.connect(**get_database_config())
    cur  = conn.cursor(dictionary=True)
    cur.execute(sql, params or ())
    rows = cur.fetchall() if fetch else None
    conn.commit()
    cur.close()
    conn.close()
    return rows

# ─────────────────────────────────────────────────────────
#  STARTUP — CREATE TABLES + LOAD RAG DOCUMENTS
# ─────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    table_sqls = [
        """CREATE TABLE IF NOT EXISTS users (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            full_name     VARCHAR(120)  NOT NULL,
            email         VARCHAR(180)  NOT NULL UNIQUE,
            dob           DATE,
            password_hash VARCHAR(255)  NOT NULL,
            is_verified   TINYINT(1)    DEFAULT 0,
            created_at    DATETIME      DEFAULT CURRENT_TIMESTAMP
        )""",

        """CREATE TABLE IF NOT EXISTS otp_tokens (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            email      VARCHAR(180) NOT NULL,
            otp        VARCHAR(10)  NOT NULL,
            expires_at DATETIME     NOT NULL,
            used       TINYINT(1)   DEFAULT 0,
            INDEX idx_email (email)
        )""",

        """CREATE TABLE IF NOT EXISTS user_profiles (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            user_id         INT            NOT NULL UNIQUE,
            monthly_income  DECIMAL(12,2)  DEFAULT 0,
            monthly_expense DECIMAL(12,2)  DEFAULT 0,
            gender          VARCHAR(40),
            work_field      VARCHAR(80),
            has_insurance   VARCHAR(40),
            emergency_fund  VARCHAR(60),
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",

        """CREATE TABLE IF NOT EXISTS user_statements (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            user_id           INT           NOT NULL,
            uploaded_at       DATETIME      DEFAULT CURRENT_TIMESTAMP,
            period_label      VARCHAR(100),
            total_income      DECIMAL(14,2) DEFAULT 0,
            total_expense     DECIMAL(14,2) DEFAULT 0,
            transactions_json MEDIUMTEXT,
            cat_totals_json   TEXT,
            INDEX idx_stmt_user (user_id)
        )""",

        """CREATE TABLE IF NOT EXISTS user_suggestions (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            user_id          INT            NOT NULL,
            suggestion_text  TEXT           NOT NULL,
            suggestion_type  VARCHAR(50)    DEFAULT 'general',
            is_starred       TINYINT(1)     DEFAULT 0,
            is_read          TINYINT(1)     DEFAULT 0,
            created_at       DATETIME       DEFAULT CURRENT_TIMESTAMP,
            source           VARCHAR(50)    DEFAULT 'ai_analysis',
            INDEX idx_sugg_user (user_id),
            INDEX idx_sugg_starred (is_starred),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",

        """CREATE TABLE IF NOT EXISTS user_income_sources (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            user_id         INT            NOT NULL,
            source_name     VARCHAR(120)   NOT NULL,
            monthly_amount  DECIMAL(12,2)  DEFAULT 0,
            created_at      DATETIME       DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME       DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_income_user (user_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",

        # ── BUDGET TABLE ──────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS user_budgets (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            user_id    INT           NOT NULL,
            category   VARCHAR(100)  NOT NULL,
            amount     DECIMAL(12,2) DEFAULT 0,
            created_at DATETIME      DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME      DEFAULT CURRENT_TIMESTAMP
                       ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_user_category (user_id, category),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
    ]

    for sql in table_sqls:
        try:
            db_exec(sql)
        except Exception as e:
            print(f"[DB WARN] {e}")
    print("[DB] Tables ready OK")

    # Initialize networth tables
    try:
        init_networth_tables()
    except Exception as e:
        print(f"[NetWorth WARN] {e}")

    # Load RAG documents
    DOC_FOLDER = "documents"
    if os.path.exists(DOC_FOLDER):
        for fname in os.listdir(DOC_FOLDER):
            path = os.path.join(DOC_FOLDER, fname)
            text = ""
            try:
                if fname.endswith(".pdf"):
                    reader = PdfReader(path)
                    for page in reader.pages:
                        if page.extract_text():
                            text += page.extract_text()
                elif fname.endswith(".docx"):
                    doc  = docx.Document(path)
                    text = "\n".join([p.text for p in doc.paragraphs])
                elif fname.endswith(".txt"):
                    with open(path, "r", encoding="utf-8") as f:
                        text = f.read()
            except Exception as e:
                print(f"[Doc Error] {fname}: {e}")
            if text:
                add_document(text)

    global index
    index = build_index()
    print("[RAG] Ready OK")

# ─────────────────────────────────────────────────────────
#  AUTH HELPERS
# ─────────────────────────────────────────────────────────
def make_token(user_id: int, email: str) -> str:
    return jwt.encode(
        {"sub": str(user_id), "email": email,
         "exp": datetime.utcnow() + timedelta(hours=24)},
        get_jwt_secret_key(), algorithm=ALGORITHM,
    )

def gen_otp() -> str:
    return "".join(random.choices(string.digits, k=6))

async def email_otp(email: str, otp: str, first_name: str):
    active_mailer = _require_mailer()
    html = f"""
    <div style="font-family:Inter,sans-serif;max-width:460px;margin:auto;
                background:#f4f6fb;padding:30px;border-radius:16px">
      <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);
                  border-radius:12px;padding:18px;text-align:center;margin-bottom:22px">
        <span style="font-size:1.4rem;font-weight:800;color:#fff">✦ Finace</span>
      </div>
      <h2 style="color:#1a1a2e;margin-bottom:8px">Hi {first_name}! 👋</h2>
      <p style="color:#5a6478;line-height:1.6;margin-bottom:18px">
        Use the code below to verify your email.
        It expires in <strong>{OTP_EXPIRE_MINUTES} minutes</strong>.
      </p>
      <div style="background:#fff;border:2px solid #c7d2fe;border-radius:12px;
                  padding:22px;text-align:center;margin-bottom:18px">
        <div style="letter-spacing:14px;font-size:2rem;font-weight:800;color:#4f46e5">{otp}</div>
      </div>
      <p style="color:#b0b8cc;font-size:.78rem;text-align:center">
        If you didn't sign up for Finace, ignore this email.
      </p>
    </div>"""
    msg = MessageSchema(
        subject    = "Your Finace verification code",
        recipients = [email],
        body       = html,
        subtype    = MessageType.html,
    )
    try:
        await active_mailer.send_message(msg)
        print(f"[EMAIL] OTP sent to {email}")
    except Exception as exc:
        logger.error("OTP email delivery failed (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="OTP email delivery failed. Please retry later.",
        ) from exc

async def send_welcome_email(email: str, full_name: str):
    if mailer is None:
        logger.warning("Welcome email not sent: mail service is unavailable.")
        return
    html = f"""<!DOCTYPE html><html><body style="font-family:Inter,sans-serif;max-width:600px;margin:auto">
    <div style="background:linear-gradient(135deg,#10b981,#059669);color:#fff;padding:40px;
                border-radius:12px 12px 0 0;text-align:center">
      <div style="font-size:2rem;font-weight:800">✦ Finace</div>
      <h1>Welcome, {full_name}! 🚀</h1>
    </div>
    <div style="padding:30px;background:#f8fafc">
      <p>Welcome to <strong>Finace</strong> — your AI-powered financial advisor.</p>
      <p style="margin-top:20px;color:#6b7280;font-size:14px">
        Tip: Complete your profile for personalised recommendations!
      </p>
    </div>
    <div style="text-align:center;padding:20px;color:#6b7280;font-size:13px">
      © 2026 Finace. All rights reserved.
    </div></body></html>"""
    msg = MessageSchema(
        subject    = "🎉 Welcome to Finace!",
        recipients = [email],
        body       = html,
        subtype    = MessageType.html,
    )
    try:
        await mailer.send_message(msg)
        print(f"[EMAIL] Welcome sent to {email}")
    except Exception as exc:
        logger.warning("Welcome email delivery failed (%s).", type(exc).__name__)

# ─────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Finace API running ✓"}

# ── REGISTER ─────────────────────────────────────────────
@app.post("/register")
async def register(
    full_name: str = Form(...),
    email:     str = Form(...),
    dob:       str = Form(...),
    password:  str = Form(...),
):
    _require_mailer()
    if len(password) < 8:
        raise HTTPException(400, detail="Password must be at least 8 characters.")
    existing = db_exec("SELECT id, is_verified FROM users WHERE email=%s", (email,), fetch=True)
    if existing:
        if existing[0]["is_verified"]:
            raise HTTPException(409, detail="An account with this email already exists.")
    else:
        db_exec(
            "INSERT INTO users (full_name, email, dob, password_hash, is_verified) VALUES (%s,%s,%s,%s,0)",
            (full_name, email, dob, hash_password(password))
        )
    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp = gen_otp()
    db_exec(
        "INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
        (email, otp, datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES))
    )
    await email_otp(email, otp, full_name.split()[0])
    return {"message": "OTP sent to your email.", "email": email}

# ── RESEND OTP ────────────────────────────────────────────
@app.post("/resend-otp")
async def resend_otp(email: str = Form(...)):
    _require_mailer()
    rows = db_exec(
        "SELECT full_name FROM users WHERE email=%s AND is_verified=0", (email,), fetch=True
    )
    if not rows:
        raise HTTPException(404, detail="No unverified account found for this email.")
    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp = gen_otp()
    db_exec(
        "INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
        (email, otp, datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES))
    )
    await email_otp(email, otp, rows[0]["full_name"].split()[0])
    return {"message": "New OTP sent."}

# ── VERIFY OTP ────────────────────────────────────────────
@app.post("/verify-otp")
async def verify_otp(email: str = Form(...), otp: str = Form(...)):
    row = db_exec(
        "SELECT * FROM otp_tokens WHERE email=%s AND otp=%s AND used=0 ORDER BY id DESC LIMIT 1",
        (email, otp), fetch=True
    )
    if not row:
        raise HTTPException(400, "Invalid OTP.")
    if datetime.utcnow() > row[0]["expires_at"]:
        raise HTTPException(400, "OTP has expired. Request a new one.")
    db_exec("UPDATE otp_tokens SET used=1 WHERE id=%s", (row[0]["id"],))
    db_exec("UPDATE users SET is_verified=1 WHERE email=%s", (email,))
    user  = db_exec("SELECT * FROM users WHERE email=%s", (email,), fetch=True)[0]
    token = make_token(user["id"], email)
    await send_welcome_email(email, user["full_name"])
    return {
        "token": token,
        "user": {
            "id":         user["id"],
            "full_name":  user["full_name"],
            "email":      user["email"],
            "dob":        user["dob"].isoformat() if user.get("dob") else None,
            "created_at": user["created_at"].strftime("%d %B %Y") if user.get("created_at") else None,
        }
    }

# ── LOGIN ─────────────────────────────────────────────────
@app.post("/login")
def login(email: str = Form(...), password: str = Form(...)):
    rows = db_exec("SELECT * FROM users WHERE email=%s", (email,), fetch=True)
    if not rows:
        raise HTTPException(401, detail="Invalid email or password.")
    user = rows[0]
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(401, detail="Invalid email or password.")
    if not user["is_verified"]:
        raise HTTPException(403, detail="Please verify your email before logging in.")
    token        = make_token(user["id"], email)
    profile_rows = db_exec("SELECT * FROM user_profiles WHERE user_id=%s", (user["id"],), fetch=True)
    profile_data = {}
    if profile_rows:
        r = profile_rows[0]
        profile_data = {
            "monthly_income":  float(r["monthly_income"])  if r.get("monthly_income")  else None,
            "monthly_expense": float(r["monthly_expense"]) if r.get("monthly_expense") else None,
            "gender":          r.get("gender")       or "",
            "work_field":      r.get("work_field")   or "",
            "has_insurance":   r.get("has_insurance") or "",
            "emergency_fund":  r.get("emergency_fund") or "",
        }
    return {
        "message": "Login successful.",
        "token":   token,
        "user": {
            "id":         user["id"],
            "full_name":  user["full_name"],
            "email":      user["email"],
            "dob":        user["dob"].isoformat() if user.get("dob") else None,
            "created_at": user["created_at"].strftime("%d %B %Y") if user.get("created_at") else None,
        },
        "profile": profile_data,
    }

# ── ONBOARDING ────────────────────────────────────────────
@app.post("/onboarding")
def save_onboarding(
    user_id:         int   = Form(...),
    monthly_income:  float = Form(0),
    monthly_expense: float = Form(0),
    gender:          str   = Form(""),
    work_field:      str   = Form(""),
    has_insurance:   str   = Form(""),
    emergency_fund:  str   = Form(""),
):
    db_exec(
        """INSERT INTO user_profiles
               (user_id, monthly_income, monthly_expense, gender, work_field, has_insurance, emergency_fund)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE
               monthly_income  = VALUES(monthly_income),
               monthly_expense = VALUES(monthly_expense),
               gender          = VALUES(gender),
               work_field      = VALUES(work_field),
               has_insurance   = VALUES(has_insurance),
               emergency_fund  = VALUES(emergency_fund)""",
        (user_id, monthly_income, monthly_expense, gender, work_field, has_insurance, emergency_fund)
    )
    return {"message": "Profile saved ✓"}

# ─────────────────────────────────────────────────────────
#  STATEMENT ENDPOINTS
# ─────────────────────────────────────────────────────────
# NOTE: /statement/parse-ocr and /statement/parse-text are
#       registered above via bank_parser's router.
#       Only save/get remain here.

@app.post("/statement/save")
def save_statement(
    user_id:           int   = Form(...),
    total_income:      float = Form(0),
    total_expense:     float = Form(0),
    period_label:      str   = Form(""),
    transactions_json: str   = Form("[]"),
    cat_totals_json:   str   = Form("{}"),
):
    """Save parsed statement data — replaces any previous statement for this user."""
    db_exec("DELETE FROM user_statements WHERE user_id=%s", (user_id,))
    db_exec(
        """INSERT INTO user_statements
               (user_id, period_label, total_income, total_expense,
                transactions_json, cat_totals_json)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (user_id, period_label, total_income, total_expense,
         transactions_json, cat_totals_json)
    )
    return {"message": "Statement saved ✓"}


@app.get("/statement/{user_id}")
def get_statement(user_id: int):
    """Return the latest saved statement for a user."""
    rows = db_exec(
        "SELECT * FROM user_statements WHERE user_id=%s ORDER BY uploaded_at DESC LIMIT 1",
        (user_id,), fetch=True
    )
    if not rows:
        return {"statement": None}
    r = rows[0]
    return {
        "statement": {
            "period_label":      r.get("period_label", ""),
            "total_income":      float(r["total_income"])  if r.get("total_income")  else 0,
            "total_expense":     float(r["total_expense"]) if r.get("total_expense") else 0,
            "uploaded_at":       r["uploaded_at"].strftime("%d %b %Y") if r.get("uploaded_at") else "",
            "transactions_json": r.get("transactions_json", "[]"),
            "cat_totals_json":   r.get("cat_totals_json",   "{}"),
        }
    }

# ─────────────────────────────────────────────────────────
#  INCOME SOURCES
# ─────────────────────────────────────────────────────────
@app.post("/income-source/save")
def save_income_source(
    user_id:        int   = Form(...),
    source_name:    str   = Form(...),
    monthly_amount: float = Form(0),
):
    existing = db_exec(
        "SELECT id FROM user_income_sources WHERE user_id=%s AND source_name=%s",
        (user_id, source_name), fetch=True
    )
    if existing:
        db_exec(
            "UPDATE user_income_sources SET monthly_amount=%s, updated_at=CURRENT_TIMESTAMP "
            "WHERE user_id=%s AND source_name=%s",
            (monthly_amount, user_id, source_name)
        )
    else:
        db_exec(
            "INSERT INTO user_income_sources (user_id, source_name, monthly_amount) VALUES (%s,%s,%s)",
            (user_id, source_name, monthly_amount)
        )
    return {"message": "Income source saved ✓"}


@app.get("/income-sources/{user_id}")
def get_income_sources(user_id: int):
    rows = db_exec(
        "SELECT id, source_name, monthly_amount, updated_at "
        "FROM user_income_sources WHERE user_id=%s ORDER BY updated_at DESC",
        (user_id,), fetch=True
    )
    sources = []
    total   = 0
    for row in rows:
        amount = float(row["monthly_amount"]) if row.get("monthly_amount") else 0
        sources.append({
            "id":         row["id"],
            "name":       row["source_name"],
            "amount":     amount,
            "updated_at": row["updated_at"].strftime("%d %b %Y") if row.get("updated_at") else ""
        })
        total += amount
    return {"sources": sources, "total_from_sources": round(total, 2)}


@app.post("/income-source/delete")
def delete_income_source(user_id: int = Form(...), source_id: int = Form(...)):
    db_exec(
        "DELETE FROM user_income_sources WHERE id=%s AND user_id=%s",
        (source_id, user_id)
    )
    return {"message": "Income source deleted ✓"}

# ─────────────────────────────────────────────────────────
#  UPDATE EMAIL
# ─────────────────────────────────────────────────────────
@app.post("/update-email")
async def update_email(user_id: int = Form(...), email: str = Form(...)):
    existing = db_exec(
        "SELECT id FROM users WHERE email=%s AND id!=%s", (email, user_id), fetch=True
    )
    if existing:
        raise HTTPException(400, detail="Email already exists")
    db_exec("UPDATE users SET email=%s WHERE id=%s", (email, user_id))
    return {"message": "Email updated successfully"}

# ─────────────────────────────────────────────────────────
#  AI SUGGESTIONS
# ─────────────────────────────────────────────────────────
@app.post("/generate-suggestion")
async def generate_suggestion(user_id: int = Form(...)):
    user_rows = db_exec(
        """SELECT u.full_name, u.email,
                  p.monthly_income, p.monthly_expense, p.work_field,
                  p.has_insurance, p.emergency_fund
           FROM users u
           LEFT JOIN user_profiles p ON u.id = p.user_id
           WHERE u.id = %s""",
        (user_id,), fetch=True
    )
    if not user_rows:
        raise HTTPException(404, detail="User not found")

    user   = user_rows[0]
    prompt = f"""
Based on this user's financial profile, generate the single most important financial recommendation:

User Profile:
- Monthly Income:   ₹{user.get('monthly_income', 0):,.2f}
- Monthly Expenses: ₹{user.get('monthly_expense', 0):,.2f}
- Work Field:       {user.get('work_field', 'Not specified')}
- Insurance:        {user.get('has_insurance', 'Not specified')}
- Emergency Fund:   {user.get('emergency_fund', 'Not specified')}

Give the SINGLE most important financial action they should take now.
Prioritise: 1) Emergency fund  2) Insurance  3) Investment
Be specific, actionable, and explain WHY. Keep it concise but comprehensive.
"""
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {get_openrouter_api_key()}"},
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]},
            )
        suggestion = res.json()["choices"][0]["message"]["content"]
        return {"suggestion": suggestion}
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to generate suggestion: {str(e)}")


@app.post("/save-suggestion")
async def save_suggestion(
    user_id:         int = Form(...),
    suggestion_text: str = Form(...),
    suggestion_type: str = Form("general"),
    source:          str = Form("manual"),
):
    db_exec(
        "INSERT INTO user_suggestions (user_id, suggestion_text, suggestion_type, source) "
        "VALUES (%s, %s, %s, %s)",
        (user_id, suggestion_text, suggestion_type, source)
    )
    return {"message": "Suggestion saved"}


@app.get("/suggestions/{user_id}")
def get_suggestions(user_id: int):
    rows = db_exec(
        "SELECT * FROM user_suggestions WHERE user_id=%s "
        "ORDER BY is_starred DESC, created_at DESC",
        (user_id,), fetch=True
    )
    return {
        "suggestions": [
            {
                "id":         r["id"],
                "text":       r["suggestion_text"],
                "type":       r["suggestion_type"],
                "is_starred": bool(r["is_starred"]),
                "is_read":    bool(r["is_read"]),
                "created_at": r["created_at"].strftime("%d %b %Y, %I:%M %p") if r.get("created_at") else "",
                "source":     r.get("source", "manual"),
            }
            for r in rows
        ]
    }

# ── FORGOT PASSWORD ───────────────────────────────────────
@app.post("/forgot-password")
async def forgot_password(email: str = Form(...)):
    _require_mailer()
    rows = db_exec(
        "SELECT id, full_name FROM users WHERE email=%s AND is_verified=1",
        (email,), fetch=True
    )
    if not rows:
        raise HTTPException(404, detail="No account found with this email.")
    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp = gen_otp()
    db_exec(
        "INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
        (email, otp, datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES))
    )
    await email_otp(email, otp, rows[0]["full_name"].split()[0])
    return {"message": "OTP sent to your email."}


# ── VERIFY FORGOT PASSWORD OTP ────────────────────────────
@app.post("/verify-forgot-otp")
def verify_forgot_otp(email: str = Form(...), otp: str = Form(...)):
    row = db_exec(
        "SELECT * FROM otp_tokens WHERE email=%s AND otp=%s AND used=0 ORDER BY id DESC LIMIT 1",
        (email, otp), fetch=True
    )
    if not row:
        raise HTTPException(400, detail="Invalid OTP.")
    if datetime.utcnow() > row[0]["expires_at"]:
        raise HTTPException(400, detail="OTP has expired. Request a new one.")
    db_exec("UPDATE otp_tokens SET used=1 WHERE id=%s", (row[0]["id"],))
    user  = db_exec("SELECT * FROM users WHERE email=%s", (email,), fetch=True)[0]
    token = make_token(user["id"], email)
    return {"message": "OTP verified.", "token": token}


@app.get("/get-profile")
def get_profile(user_id: int):
    user_rows = db_exec(
        "SELECT id, full_name, email, dob, created_at FROM users WHERE id=%s",
        (user_id,), fetch=True
    )
    if not user_rows:
        raise HTTPException(404, detail="User not found")
    u = user_rows[0]

    profile_rows = db_exec(
        "SELECT * FROM user_profiles WHERE user_id=%s", (user_id,), fetch=True
    )
    profile_data = {}
    if profile_rows:
        r = profile_rows[0]
        profile_data = {
            "monthly_income":  float(r["monthly_income"])  if r.get("monthly_income")  else None,
            "monthly_expense": float(r["monthly_expense"]) if r.get("monthly_expense") else None,
            "gender":          r.get("gender")        or "",
            "work_field":      r.get("work_field")    or "",
            "has_insurance":   r.get("has_insurance") or "",
            "emergency_fund":  r.get("emergency_fund") or "",
        }

    return {
        "user": {
            "id":         u["id"],
            "full_name":  u["full_name"],
            "email":      u["email"],
            "dob":        u["dob"].isoformat() if u.get("dob") else None,
            "created_at": u["created_at"].isoformat() if u.get("created_at") else None,
        },
        "profile": profile_data
    }


# ── RESET PASSWORD ────────────────────────────────────────
@app.post("/reset-password")
def reset_password(
    email:        str = Form(...),
    new_password: str = Form(...),
    token:        str = Form(""),
):
    if len(new_password) < 8:
        raise HTTPException(400, detail="Password must be at least 8 characters.")
    rows = db_exec("SELECT id FROM users WHERE email=%s AND is_verified=1", (email,), fetch=True)
    if not rows:
        raise HTTPException(404, detail="User not found.")
    db_exec(
        "UPDATE users SET password_hash=%s WHERE email=%s",
        (hash_password(new_password), email)
    )
    return {"message": "Password updated successfully."}


@app.post("/toggle-star-suggestion")
def toggle_star_suggestion(suggestion_id: int = Form(...)):
    db_exec(
        "UPDATE user_suggestions SET is_starred = NOT is_starred WHERE id=%s",
        (suggestion_id,)
    )
    return {"message": "Star status updated"}


@app.delete("/suggestions/{suggestion_id}")
def delete_suggestion(suggestion_id: int):
    db_exec("DELETE FROM user_suggestions WHERE id=%s", (suggestion_id,))
    return {"message": "Suggestion deleted"}

# ─────────────────────────────────────────────────────────
#  BUDGET
# ─────────────────────────────────────────────────────────
@app.get("/budget/{user_id}")
def get_budget(user_id: int):
    """Return all budget categories for a user."""
    rows = db_exec(
        "SELECT id, category, amount, updated_at "
        "FROM user_budgets WHERE user_id=%s ORDER BY category",
        (user_id,), fetch=True
    )
    budgets = [
        {
            "id":         r["id"],
            "category":   r["category"],
            "amount":     float(r["amount"]),
            "updated_at": r["updated_at"].strftime("%d %b %Y") if r.get("updated_at") else "",
        }
        for r in rows
    ]
    return {
        "budgets": budgets,
        "total":   round(sum(b["amount"] for b in budgets), 2),
    }


@app.post("/budget/save")
def save_budget(
    user_id:  int   = Form(...),
    category: str   = Form(...),
    amount:   float = Form(0),
):
    """Create or update a budget category for a user (upsert on user_id + category)."""
    db_exec(
        """INSERT INTO user_budgets (user_id, category, amount)
           VALUES (%s, %s, %s)
           ON DUPLICATE KEY UPDATE
               amount     = VALUES(amount),
               updated_at = CURRENT_TIMESTAMP""",
        (user_id, category, amount)
    )
    return {"message": "Budget saved ✓"}


@app.delete("/budget/{budget_id}")
def delete_budget(budget_id: int):
    """Delete a single budget entry by its ID."""
    db_exec("DELETE FROM user_budgets WHERE id=%s", (budget_id,))
    return {"message": "Budget deleted ✓"}


# ─────────────────────────────────────────────────────────
#  CHAT  (RAG + OpenRouter)
# ─────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(
    message: str                    = Form(...),
    user_id: Optional[int]          = Form(None),
    file:    Optional[UploadFile]   = File(None),
):
    file_text = ""
    if file:
        raw       = await file.read()
        file_text = raw.decode("utf-8", errors="ignore")

    user_context = ""
    if user_id:
        user_rows = db_exec(
            """SELECT u.full_name, u.email, u.dob,
                      p.monthly_income, p.monthly_expense, p.work_field,
                      p.has_insurance, p.emergency_fund
               FROM users u
               LEFT JOIN user_profiles p ON u.id = p.user_id
               WHERE u.id = %s""",
            (user_id,), fetch=True
        )
        if user_rows:
            u = user_rows[0]
            user_context = (
                f"User Profile:\n"
                f"- Name: {u.get('full_name','N/A')}\n"
                f"- Monthly Income:   ₹{u.get('monthly_income',0):,.2f}\n"
                f"- Monthly Expenses: ₹{u.get('monthly_expense',0):,.2f}\n"
                f"- Work Field:       {u.get('work_field','Not specified')}\n"
                f"- Insurance:        {u.get('has_insurance','Not specified')}\n"
                f"- Emergency Fund:   {u.get('emergency_fund','Not specified')}\n\n"
                f"Please provide personalised financial advice.\n"
            )

    context, score = search(message, index, k=1)
    if context and score > 0.35:
        prompt = f"{user_context}Use this document:\n{context}\n\nQuestion: {message}"
    else:
        prompt = f"{user_context}Question: {message}"

    if file_text:
        prompt += f"\n\nFile:\n{file_text[:2000]}"

    conversation.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient() as client:
        res = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {get_openrouter_api_key()}"},
            json={"model": MODEL, "messages": conversation},
        )
    reply = res.json()["choices"][0]["message"]["content"]
    conversation.append({"role": "assistant", "content": reply})
    return {"reply": reply}

# ─────────────────────────────────────────────────────────
#  METAL RATES
# ─────────────────────────────────────────────────────────
@app.get("/metal-rates")
def get_rates(date: str = None):
    try:
        if date:
            rows = db_exec(
                "SELECT metal, karat, price FROM metal_rates WHERE date=%s ORDER BY karat DESC",
                (date,), fetch=True
            )
        else:
            rows = db_exec(
                "SELECT metal, karat, price FROM metal_rates WHERE date=CURDATE() ORDER BY karat DESC",
                fetch=True
            )
        return {"rates": rows or []}
    except Exception as e:
        raise HTTPException(500, detail=str(e))