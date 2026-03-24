
#Pragadeesh

"""
Finace — FastAPI Backend
=================================
Install dependencies:
    pip install fastapi uvicorn mysql-connector-python python-multipart
    pip install passlib[bcrypt] python-jose[cryptography]
    pip install fastapi-mail pypdf python-docx httpx

Run the server:
    uvicorn main:app --reload --port 8000

MySQL password : 0000
Database       : finance_ai
"""

import os
import io
import random, string
from datetime import datetime, timedelta
from typing import Optional

import httpx
import mysql.connector
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from jose import jwt
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from pypdf import PdfReader
import docx

from backend.rag import add_document, build_index, search

# ─────────────────────────────────────────────────────────
#  API CONFIG
# ─────────────────────────────────────────────────────────
OPENROUTER_API_KEY = "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"
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"
MODEL              = "openai/gpt-4o-mini"

# ─────────────────────────────────────────────────────────
#  DB CONFIG
# ─────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":       "localhost",
    "user":       "root",
    "password":   "0000",
    "database":   "finance_ai",
    "autocommit": True,
}

SECRET_KEY         = "financetracker_super_secret_change_me"
ALGORITHM          = "HS256"
OTP_EXPIRE_MINUTES = 10

# ─────────────────────────────────────────────────────────
#  EMAIL CONFIG
# ─────────────────────────────────────────────────────────
EMAIL_CONF = ConnectionConfig(
    MAIL_USERNAME   = "finaceai.pvt@gmail.com",
    MAIL_PASSWORD   = "tjmpznbrthfvtelm",
    MAIL_FROM       = "finaceai.pvt@gmail.com",
    MAIL_PORT       = 587,
    MAIL_SERVER     = "smtp.gmail.com",
    MAIL_STARTTLS   = True,
    MAIL_SSL_TLS    = False,
    USE_CREDENTIALS = True,
    VALIDATE_CERTS  = True,
)

# ─────────────────────────────────────────────────────────
#  APP
# ─────────────────────────────────────────────────────────
app    = FastAPI(title="Finace API", version="1.0")
mailer = FastMail(EMAIL_CONF)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Global conversation memory for chat
conversation = [{"role": "system", "content": "You are a helpful financial assistant."}]

# ─────────────────────────────────────────────────────────
#  DB HELPER
# ─────────────────────────────────────────────────────────
def db_exec(sql: str, params=None, fetch=False):
    conn = mysql.connector.connect(**DB_CONFIG)
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

        # ── NEW: bank statement data per user ──────────────
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
    ]

    for sql in table_sqls:
        try:
            db_exec(sql)
        except Exception as e:
            print(f"[DB WARN] {e}")
    print("[DB] Tables ready ✓")

    # Load RAG documents
    DOC_FOLDER = "documents"
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
                doc = docx.Document(path)
                text = "\n".join([p.text for p in doc.paragraphs])
            elif fname.endswith(".txt"):
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
        except Exception as e:
            print("Doc Error:", e)
        if text:
            add_document(text)

    global index
    index = build_index()
    print("RAG Ready ✅")

# ─────────────────────────────────────────────────────────
#  AUTH HELPERS
# ─────────────────────────────────────────────────────────
def make_token(user_id: int, email: str) -> str:
    return jwt.encode(
        {"sub": str(user_id), "email": email,
         "exp": datetime.utcnow() + timedelta(hours=24)},
        SECRET_KEY, algorithm=ALGORITHM,
    )

def gen_otp() -> str:
    return "".join(random.choices(string.digits, k=6))

async def email_otp(email: str, otp: str, first_name: str):
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
        await mailer.send_message(msg)
        print(f"[EMAIL] OTP sent to {email}")
    except Exception as e:
        print(f"[EMAIL FAILED] {e}")
        print(f"[DEV OTP] {email} → {otp}")

async def send_welcome_email(email: str, full_name: str):
    html = f"""<!DOCTYPE html><html><body style="font-family:Inter,sans-serif;max-width:600px;margin:auto">
    <div style="background:linear-gradient(135deg,#10b981,#059669);color:#fff;padding:40px;border-radius:12px 12px 0 0;text-align:center">
      <div style="font-size:2rem;font-weight:800">✦ Finace</div>
      <h1>Welcome, {full_name}! 🚀</h1>
    </div>
    <div style="padding:30px;background:#f8fafc">
      <p>Welcome to <strong>Finace</strong> — your AI-powered financial advisor.</p>
      <p style="margin-top:20px;color:#6b7280;font-size:14px">Tip: Complete your profile for personalised recommendations!</p>
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
    except Exception as e:
        print(f"[WELCOME EMAIL FAILED] {e}")

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
    if len(password) < 8:
        raise HTTPException(400, detail="Password must be at least 8 characters.")
    existing = db_exec("SELECT id, is_verified FROM users WHERE email=%s", (email,), fetch=True)
    if existing:
        if existing[0]["is_verified"]:
            raise HTTPException(409, detail="An account with this email already exists.")
    else:
        db_exec(
            "INSERT INTO users (full_name, email, dob, password_hash, is_verified) VALUES (%s,%s,%s,%s,0)",
            (full_name, email, dob, pwd_ctx.hash(password))
        )
    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp = gen_otp()
    db_exec("INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
            (email, otp, datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)))
    await email_otp(email, otp, full_name.split()[0])
    return {"message": "OTP sent to your email.", "email": email}

# ── RESEND OTP ────────────────────────────────────────────
@app.post("/resend-otp")
async def resend_otp(email: str = Form(...)):
    rows = db_exec("SELECT full_name FROM users WHERE email=%s AND is_verified=0", (email,), fetch=True)
    if not rows:
        raise HTTPException(404, detail="No unverified account found for this email.")
    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp = gen_otp()
    db_exec("INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
            (email, otp, datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)))
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
    user = db_exec("SELECT * FROM users WHERE email=%s", (email,), fetch=True)[0]
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
    if not pwd_ctx.verify(password, user["password_hash"]):
        raise HTTPException(401, detail="Invalid email or password.")
    if not user["is_verified"]:
        raise HTTPException(403, detail="Please verify your email before logging in.")
    token = make_token(user["id"], email)
    profile_rows = db_exec("SELECT * FROM user_profiles WHERE user_id=%s", (user["id"],), fetch=True)
    profile_data = {}
    if profile_rows:
        r = profile_rows[0]
        profile_data = {
            "monthly_income":  float(r["monthly_income"])  if r.get("monthly_income")  else None,
            "monthly_expense": float(r["monthly_expense"]) if r.get("monthly_expense") else None,
            "gender":          r.get("gender")          or "",
            "work_field":      r.get("work_field")      or "",
            "has_insurance":   r.get("has_insurance")   or "",
            "emergency_fund":  r.get("emergency_fund")  or "",
        }
    return {
        "message": "Login successful.",
        "token": token,
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
#  STATEMENT ENDPOINTS (dashboard)
# ─────────────────────────────────────────────────────────

@app.post("/statement/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    """
    Extract plain text from a PDF (GPay, bank statement, etc.)
    and return it so the frontend parser can process it.
    """
    raw = await file.read()
    pages = []
    try:
        reader = PdfReader(io.BytesIO(raw))
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    except Exception as e:
        raise HTTPException(400, detail=f"Could not read PDF: {e}")
    if not pages:
        raise HTTPException(400, detail="No text could be extracted from this PDF.")
    return {"text": "\n".join(pages), "pages": len(pages)}


@app.post("/statement/save")
def save_statement(
    user_id:           int   = Form(...),
    total_income:      float = Form(0),
    total_expense:     float = Form(0),
    period_label:      str   = Form(""),
    transactions_json: str   = Form("[]"),
    cat_totals_json:   str   = Form("{}"),
):
    """
    Save parsed statement data for a user.
    Replaces any previous statement for the same user.
    """
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
#  CHAT  (RAG + OpenRouter)
# ─────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(message: str = Form(...), file: Optional[UploadFile] = File(None)):
    file_text = ""
    if file:
        raw = await file.read()
        file_text = raw.decode("utf-8", errors="ignore")

    context, score = search(message, index, k=1)
    if context and score > 0.35:
        prompt = f"Use this document:\n{context}\n\nQuestion: {message}"
    else:
        prompt = message
    if file_text:
        prompt += f"\n\nFile:\n{file_text[:2000]}"

    conversation.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient() as client:
        res = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={"model": MODEL, "messages": conversation},
        )
    result = res.json()
    reply  = result["choices"][0]["message"]["content"]
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