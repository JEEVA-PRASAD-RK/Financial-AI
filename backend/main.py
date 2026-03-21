
#Pragadeesh

"""
FinanceTracker — FastAPI Backend
=================================
Install dependencies:
    pip install fastapi uvicorn mysql-connector-python python-multipart
    pip install passlib[bcrypt] python-jose[cryptography]
    pip install fastapi-mail

Run the server:
    uvicorn main:app --reload --port 8000

MySQL password : 0000
Database       : finance_ai
Tables created : users, otp_tokens, user_profiles  (auto on startup)
"""
import os
import random, string
from datetime import datetime, timedelta
from typing import Optional
import httpx
import random, string
from datetime import datetime, timedelta
from typing import Optional


from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import mysql.connector
from passlib.context import CryptContext
from jose import jwt
import mysql.connector
from pypdf import PdfReader
import docx

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from backend.rag import add_document, build_index, search

# -----------------------------
# API CONFIG
# -----------------------------
OPENROUTER_API_KEY = "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"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openai/gpt-4o-mini"

# ─────────────────────────────────────────────────────────
#  CONFIG  — only change the email block below
# ─────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":        "localhost",
    "user":        "root",
    "password":    "0000",
    "database":    "finance_ai",
    "autocommit":  True,
}

SECRET_KEY         = "financetracker_super_secret_change_me"
ALGORITHM          = "HS256"
OTP_EXPIRE_MINUTES = 10

# ── Gmail SMTP — replace with your credentials ────────────
EMAIL_CONF = ConnectionConfig(
    MAIL_USERNAME   = "finaceai.pvt@gmail.com",      # ← your Gmail
    MAIL_PASSWORD   = "tjmpznbrthfvtelm",    # ← Gmail App Password
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
app = FastAPI(title="FinanceTracker API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
mailer  = FastMail(EMAIL_CONF)
# MEMORY
# -----------------------------
conversation = [
    {"role": "system", "content": "You are a helpful financial assistant."}
]
# ─────────────────────────────────────────────────────────
#  DB HELPERS
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
#  CREATE TABLES ON STARTUP
# ─────────────────────────────────────────────────────────
@app.on_event("startup")
def create_tables():
    statements = [
        # users
        """CREATE TABLE IF NOT EXISTS users (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            full_name     VARCHAR(120)  NOT NULL,
            email         VARCHAR(180)  NOT NULL UNIQUE,
            dob           DATE,
            password_hash VARCHAR(255)  NOT NULL,
            is_verified   TINYINT(1)    DEFAULT 0,
            created_at    DATETIME      DEFAULT CURRENT_TIMESTAMP
        )""",

        # otp_tokens
        """CREATE TABLE IF NOT EXISTS otp_tokens (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            email      VARCHAR(180) NOT NULL,
            otp        VARCHAR(10)  NOT NULL,
            expires_at DATETIME     NOT NULL,
            used       TINYINT(1)   DEFAULT 0,
            INDEX idx_email (email)
        )""",

        # user_profiles  (onboarding answers)
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
    ]
    for sql in statements:
        try:
            db_exec(sql)
        except Exception as e:
            print(f"[DB WARN] {e}")
    print("[DB] Tables ready ✓")
     # LOAD DOCUMENTS
    DOC_FOLDER = "documents"
    for file in os.listdir(DOC_FOLDER):
        path = os.path.join(DOC_FOLDER, file)
        text = ""

        try:
            if file.endswith(".pdf"):
                reader = PdfReader(path)
                for page in reader.pages:
                    if page.extract_text():
                        text += page.extract_text()

            elif file.endswith(".docx"):
                doc = docx.Document(path)
                text = "\n".join([p.text for p in doc.paragraphs])

            elif file.endswith(".txt"):
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
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=24),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def gen_otp() -> str:
    return "".join(random.choices(string.digits, k=6))

async def email_otp(email: str, otp: str, first_name: str):
    html = f"""
    <div style="font-family:Inter,sans-serif;max-width:460px;margin:auto;
                background:#f4f6fb;padding:30px;border-radius:16px">
      <div style="background:linear-gradient(135deg,#4f46e5,#7c3aed);
                  border-radius:12px;padding:18px;text-align:center;margin-bottom:22px">
        <span style="font-size:1.4rem;font-weight:800;color:#fff">✦ FinanceTracker</span>
      </div>
      <h2 style="color:#1a1a2e;margin-bottom:8px">Hi {first_name}! 👋</h2>
      <p style="color:#5a6478;line-height:1.6;margin-bottom:18px">
        Use the code below to verify your email.
        It expires in <strong>{OTP_EXPIRE_MINUTES} minutes</strong>.
      </p>
      <div style="background:#fff;border:2px solid #c7d2fe;border-radius:12px;
                  padding:22px;text-align:center;margin-bottom:18px">
        <div style="letter-spacing:14px;font-size:2rem;font-weight:800;color:#4f46e5">
          {otp}
        </div>
      </div>
      <p style="color:#b0b8cc;font-size:.78rem;text-align:center">
        If you didn't sign up for FinanceTracker, ignore this email.
      </p>
    </div>
    """
    msg = MessageSchema(
        subject    = "Your FinanceTracker verification code",
        recipients = [email],
        body       = html,
        subtype    = MessageType.html,
    )
    try:
        await mailer.send_message(msg)
        print(f"[EMAIL] OTP sent to {email}")
    except Exception as e:
        # Dev fallback — print to console if email fails
        print(f"[EMAIL FAILED] {e}")
        print(f"[DEV OTP] {email} → {otp}")

# ─────────────────────────────────────────────────────────
#  ENDPOINTS
# ─────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "FinanceTracker API running ✓"}


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

    existing = db_exec(
        "SELECT id, is_verified FROM users WHERE email=%s", (email,), fetch=True
    )

    if existing:
        if existing[0]["is_verified"]:
            raise HTTPException(409, detail="An account with this email already exists.")
        # Unverified account exists → allow re-registering / resending OTP
    else:
        db_exec(
            """INSERT INTO users (full_name, email, dob, password_hash, is_verified)
               VALUES (%s, %s, %s, %s, 0)""",
            (full_name, email, dob, pwd_ctx.hash(password))
        )

    # Invalidate old OTPs, create new one
    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp     = gen_otp()
    expires = datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)
    db_exec(
        "INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
        (email, otp, expires)
    )

    await email_otp(email, otp, full_name.split()[0])
    return {"message": "Registration successful. OTP sent to your email.", "email": email}


# ── RESEND OTP ────────────────────────────────────────────
@app.post("/resend-otp")
async def resend_otp(email: str = Form(...)):
    rows = db_exec(
        "SELECT full_name FROM users WHERE email=%s AND is_verified=0",
        (email,), fetch=True
    )
    if not rows:
        raise HTTPException(404, detail="No unverified account found for this email.")

    db_exec("UPDATE otp_tokens SET used=1 WHERE email=%s AND used=0", (email,))
    otp     = gen_otp()
    expires = datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)
    db_exec(
        "INSERT INTO otp_tokens (email, otp, expires_at) VALUES (%s,%s,%s)",
        (email, otp, expires)
    )
    await email_otp(email, otp, rows[0]["full_name"].split()[0])
    return {"message": "New OTP sent."}
async def send_welcome_email(email, full_name):
    print("Sending welcome email to:", email)
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Inter', sans-serif; line-height: 1.6; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #10b981, #059669); color: white; padding: 40px; border-radius: 12px 12px 0 0; text-align: center; }}
            .logo {{ font-size: 32px; font-weight: 800; }}
            .content {{ padding: 40px; background: #f8fafc; }}
            .features {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin: 30px 0; }}
            .feature {{ text-align: center; padding: 20px; background: white; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
            .feature-icon {{ font-size: 32px; margin-bottom: 10px; display: block; }}
            .btn {{ background: linear-gradient(135deg, #4f46e5, #7c3aed); color: white; padding: 14px 32px; border-radius: 10px; text-decoration: none; font-weight: 700; display: inline-block; margin: 20px 0; }}
            .footer {{ text-align: center; padding: 30px; color: #6b7280; font-size: 14px; border-top: 1px solid #e5e7eb; margin-top: 40px; background: #f8fafc; border-radius: 0 0 12px 12px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="logo">✦ Finace.</div>
            <h1>Welcome Aboard, {full_name}!</h1>
            <p style="font-size: 18px; opacity: 0.95;">Your financial journey starts here 🚀</p>
        </div>
        
        <div class="content">
            <h2 style="color: #1f2937;">You're all set!</h2>
            <p style="color: #4b5563; font-size: 16px; margin-bottom: 30px;">
                Welcome to <strong>Finace.</strong> – your personal AI-powered financial advisor.
            </p>
            
            <div class="features">
                <div class="feature">
                    <span class="feature-icon">✦</span>
                    <h3>AI Advisor</h3>
                    <p>Personalized advice on loans, investments & savings</p>
                </div>
                <div class="feature">
                    <span class="feature-icon">📊</span>
                    <h3>Smart Dashboard</h3>
                    <p>Track income, expenses & goals in real-time</p>
                </div>
                <div class="feature">
                    <span class="feature-icon">🎯</span>
                    <h3>Goal Tracking</h3>
                    <p>Set & achieve your financial milestones</p>
                </div>
            </div>
            
            <a href="https://your-finance-app.com/dashboard" class="btn">→ Start Exploring</a>
            
            <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
                Tip: Complete your profile for personalized recommendations!
            </p>
        </div>
        
        <div class="footer">
            <p>Happy financial growth ahead! 🌟</p>
            <p>You received this email because you completed registration on <strong>Finance.</strong></p>
            <p>© 2026 Finace. All rights reserved. | <a href="https://your-finance-app.com" style="color: #4f46e5;">finance.ai.pvt@gmail.com</a></p>
        </div>
    </body>
    </html>
    """

    msg = MessageSchema(
        subject="🎉 Welcome to Finace. – Your Financial Journey Begins!",
        recipients=[email],
        body=html_body,
        subtype=MessageType.html,
    )

    try:
        await mailer.send_message(msg)
        print("Welcome email sent successfully ✅")
    except Exception as e:
        print("Welcome email error:", e)


# ── VERIFY OTP ────────────────────────────────────────────
@app.post("/verify-otp")
async def verify(email: str = Form(...), otp: str = Form(...)):
    row = db_exec(
        "SELECT * FROM otp_tokens WHERE email=%s AND otp=%s AND used=0",
        (email, otp), True
    )
    if not row:
        raise HTTPException(400, "Invalid OTP")

    # mark OTP used
    db_exec("UPDATE otp_tokens SET used=1 WHERE id=%s", (row[0]["id"],))

    # mark user verified
    db_exec("UPDATE users SET is_verified=1 WHERE email=%s", (email,))

    # GET USER INFO
    user_rows = db_exec("SELECT id, full_name, email, dob FROM users WHERE email=%s", (email,), True)
    if not user_rows:
        raise HTTPException(404, "User not found")

    user = user_rows[0]
    token = make_token(user["id"], user["email"])

    # SEND WELCOME EMAIL
    await send_welcome_email(email, user["full_name"])

    return {
        "token": token,
        "user": {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "dob": user["dob"].isoformat() if user["dob"] else None,
            "created_at": user["created_at"].strftime("%d %B %Y") if user.get("created_at")
            else None,
        }
    }



# ── LOGIN ─────────────────────────────────────────────────
@app.post("/login")
def login(
    email:    str = Form(...),
    password: str = Form(...),
):
    rows = db_exec("SELECT * FROM users WHERE email=%s", (email,), fetch=True)
    if not rows:
        raise HTTPException(401, detail="Invalid email or password.")

    user = rows[0]
    if not pwd_ctx.verify(password, user["password_hash"]):
        raise HTTPException(401, detail="Invalid email or password.")
    if not user["is_verified"]:
        raise HTTPException(403, detail="Please verify your email before logging in.")

    token = make_token(user["id"], email)

    # Fetch profile
    profile_rows = db_exec(
        "SELECT * FROM user_profiles WHERE user_id=%s", (user["id"],), fetch=True
    )
    profile_data = {}
    if profile_rows:
        row = profile_rows[0]
        profile_data = {
            "monthly_income":  float(row["monthly_income"])  if row.get("monthly_income")  else None,
            "monthly_expense": float(row["monthly_expense"]) if row.get("monthly_expense") else None,
            "gender":          row.get("gender")          or "",
            "work_field":      row.get("work_field")      or "",
            "has_insurance":   row.get("has_insurance")   or "",
            "emergency_fund":  row.get("emergency_fund")  or "",
        }

    return {
        "message": "Login successful.",
        "token": token,
        "user": {
            "id":        user["id"],
            "full_name": user["full_name"],
            "email":     user["email"],
            "dob":       user["dob"].isoformat() if user.get("dob") else None,
        },
        "profile": profile_data,
    }


# ── SAVE ONBOARDING ───────────────────────────────────────
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
               (user_id, monthly_income, monthly_expense, gender,
                work_field, has_insurance, emergency_fund)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE
               monthly_income  = VALUES(monthly_income),
               monthly_expense = VALUES(monthly_expense),
               gender          = VALUES(gender),
               work_field      = VALUES(work_field),
               has_insurance   = VALUES(has_insurance),
               emergency_fund  = VALUES(emergency_fund)""",
        (user_id, monthly_income, monthly_expense,
         gender, work_field, has_insurance, emergency_fund)
    )
    return {"message": "Onboarding profile saved ✓"}


# ── CHAT ─────────────────────────────────────────────────
@app.post("/chat")
async def chat(message: str = Form(...), file: Optional[UploadFile] = File(None)):

    file_text = ""
    if file:
        raw = await file.read()
        file_text = raw.decode("utf-8", errors="ignore")

    # RAG SEARCH
    context, score = search(message, index, k=1)

    if context and score > 0.35:
        prompt = f"""
Use this document:
{context}

Question: {message}
"""
    else:
        prompt = message

    if file_text:
        prompt += f"\n\nFile:\n{file_text[:2000]}"

    conversation.append({"role": "user", "content": prompt})

    data = {
        "model": MODEL,
        "messages": conversation,
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json=data,
        )

    result = res.json()
    reply = result["choices"][0]["message"]["content"]

    conversation.append({"role": "assistant", "content": reply})

    return {"reply": reply}

# ── METAL RATES (your existing table) ────────────────────
@app.get("/metal-rates")
def get_rates(date: str = None):
    try:
        if date:
            rows = db_exec(
                "SELECT metal, karat, price FROM metal_rates WHERE date = %s ORDER BY karat DESC",
                (date,), fetch=True
            )
        else:
            rows = db_exec(
                "SELECT metal, karat, price FROM metal_rates WHERE date = CURDATE() ORDER BY karat DESC",
                fetch=True
            )
        return {"rates": rows or []}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

