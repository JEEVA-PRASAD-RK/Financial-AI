"""
networth.py  —  Fixed Assets & Liabilities / Net Worth Tracker
Bug fixes:
    1. calc_loan_outstanding: return statement was inside the if block only
    2. update_entry: return statement was inside try block (wrong indentation)
    3. init_networth_tables: print/migration code was inside except block
    4. get_detail: indentation bugs fixed — item/detail code was inside if block
    5. Asset value logic: paid amount = asset value, outstanding = liability
    6. _sync_attached_loan: fixed to properly create/update linked liability
    """

from __future__ import annotations
import json, math, httpx
from datetime import datetime, date
from typing import Optional
import mysql.connector
from fastapi import APIRouter, HTTPException, Form, Query

from .config import get_database_config, get_openrouter_api_key

OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"
MODEL              = "openai/gpt-4o-mini"

router = APIRouter(prefix="/networth", tags=["Net Worth"])

# ──────────────────────────────────────────────────────────────────────────────
#  DB HELPER
# ──────────────────────────────────────────────────────────────────────────────
def db_exec(sql: str, params=None, fetch: bool = False):
    conn = mysql.connector.connect(**get_database_config())
    cur  = conn.cursor(dictionary=True)
    cur.execute(sql, params or ())
    rows    = cur.fetchall() if fetch else None
    last_id = cur.lastrowid  if not fetch else None
    conn.commit(); cur.close(); conn.close()
    return rows if fetch else last_id

# ──────────────────────────────────────────────────────────────────────────────
#  TABLE DEFINITIONS
# ──────────────────────────────────────────────────────────────────────────────
_TABLES = [
    """CREATE TABLE IF NOT EXISTS assets_liabilities (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        user_id        INT           NOT NULL,
        entry_type     VARCHAR(20)   NOT NULL COMMENT 'asset | liability',
        category       VARCHAR(80)   NOT NULL,
        name           VARCHAR(180)  NOT NULL,
        current_value  DECIMAL(18,2) NOT NULL DEFAULT 0,
        raw_meta       MEDIUMTEXT    COMMENT 'Full JSON of user inputs',
        start_date     DATE,
        end_date       DATE,
        linked_liab_id INT           DEFAULT NULL COMMENT 'Auto-created liability row id',
        created_at     DATETIME      DEFAULT CURRENT_TIMESTAMP,
        updated_at     DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_al_user (user_id),
        INDEX idx_al_type (entry_type)
    )""",

    """CREATE TABLE IF NOT EXISTS nw_gold (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        entry_id    INT NOT NULL UNIQUE,
        metal_type  VARCHAR(30),
        karat       VARCHAR(10),
        weight_g    DECIMAL(10,3),
        rate_per_g  DECIMAL(10,2),
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_real_estate (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        entry_id       INT NOT NULL UNIQUE,
        property_type  VARCHAR(60),
        area_sqft      DECIMAL(12,2),
        purchase_price DECIMAL(18,2),
        current_price  DECIMAL(18,2),
        purchase_date  DATE,
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_vehicle (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        entry_id          INT NOT NULL UNIQUE,
        vehicle_type      VARCHAR(60),
        make_model        VARCHAR(120),
        purchase_price    DECIMAL(14,2),
        purchase_date     DATE,
        depreciation_rate DECIMAL(6,3),
        computed_value    DECIMAL(14,2),
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_deposit (
        id             INT AUTO_INCREMENT PRIMARY KEY,
        entry_id       INT NOT NULL UNIQUE,
        deposit_type   VARCHAR(20),
        principal      DECIMAL(14,2),
        interest_rate  DECIMAL(7,4),
        compounding    VARCHAR(20),
        start_date     DATE,
        maturity_date  DATE,
        maturity_value DECIMAL(14,2),
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_investment (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        entry_id        INT NOT NULL UNIQUE,
        inv_type        VARCHAR(40),
        fund_name       VARCHAR(180),
        payment_mode    VARCHAR(20),
        invested_amount DECIMAL(14,2),
        current_value   DECIMAL(14,2),
        expected_return DECIMAL(7,4),
        start_date      DATE,
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_loan (
        id                    INT AUTO_INCREMENT PRIMARY KEY,
        entry_id              INT NOT NULL UNIQUE,
        loan_type             VARCHAR(60),
        principal_original    DECIMAL(14,2),
        principal_paid        DECIMAL(14,2) DEFAULT 0,
        principal_outstanding DECIMAL(14,2),
        interest_rate         DECIMAL(7,4),
        rate_period           VARCHAR(10),
        interest_type         VARCHAR(10),
        loan_status           VARCHAR(10),
        start_date            DATE,
        end_date              DATE,
        emi_amount            DECIMAL(14,2),
        asset_entry_id        INT DEFAULT NULL,
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_credit_card (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        entry_id      INT NOT NULL UNIQUE,
        bank_name     VARCHAR(80),
        outstanding   DECIMAL(14,2),
        min_due       DECIMAL(14,2),
        due_date      DATE,
        interest_rate DECIMAL(7,4),
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_emi (
        id           INT AUTO_INCREMENT PRIMARY KEY,
        entry_id     INT NOT NULL UNIQUE,
        provider     VARCHAR(80),
        item_name    VARCHAR(120),
        total_amount DECIMAL(14,2),
        paid_amount  DECIMAL(14,2) DEFAULT 0,
        emi_amount   DECIMAL(14,2),
        months_total INT,
        months_paid  INT DEFAULT 0,
        start_date   DATE,
        end_date     DATE,
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",

    """CREATE TABLE IF NOT EXISTS nw_mortgage (
        id                    INT AUTO_INCREMENT PRIMARY KEY,
        entry_id              INT NOT NULL UNIQUE,
        collateral_type       VARCHAR(80),
        collateral_value      DECIMAL(18,2),
        loan_amount           DECIMAL(14,2),
        principal_paid        DECIMAL(14,2) DEFAULT 0,
        principal_outstanding DECIMAL(14,2),
        interest_rate         DECIMAL(7,4),
        rate_period           VARCHAR(10),
        interest_type         VARCHAR(10),
        start_date            DATE,
        end_date              DATE,
        emi_amount            DECIMAL(14,2),
        lender_name           VARCHAR(120),
        FOREIGN KEY (entry_id) REFERENCES assets_liabilities(id) ON DELETE CASCADE
    )""",
]


def init_networth_tables():
    for sql in _TABLES:
        try:
            db_exec(sql)
        except Exception as e:
            print(f"[NetWorth Table] {e}")

    # Migration: add columns if missing (safe to run every startup)
    for migration in [
        "ALTER TABLE assets_liabilities ADD COLUMN linked_liab_id INT DEFAULT NULL",
        "ALTER TABLE nw_loan ADD COLUMN asset_entry_id INT DEFAULT NULL",
    ]:
        try:
            db_exec(migration)
        except Exception:
            pass  # Column already exists — ignore

    print("[NetWorth] All tables ready OK")


# ──────────────────────────────────────────────────────────────────────────────
#  MATH HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def calc_fd_maturity(principal, rate, start, maturity, compounding):
    years = (maturity - start).days / 365.0
    r = rate / 100.0
    if compounding == "simple":
        return round(principal * (1 + r * years), 2)
    n = {"monthly": 12, "quarterly": 4, "annually": 1}.get(compounding, 4)
    return round(principal * ((1 + r / n) ** (n * years)), 2)


def calc_vehicle_depreciation(purchase_price, purchase_date, dep_rate):
    years   = (date.today() - purchase_date).days / 365.0
    current = purchase_price * ((1 - dep_rate / 100) ** years)
    return round(max(current, purchase_price * 0.05), 2)


# FIX 1: calc_loan_outstanding — return now always at function level
def calc_loan_outstanding(principal, rate, rate_period, interest_type, principal_paid=0):
    outstanding  = max(principal - principal_paid, 0)
    monthly_rate = rate / 100 / (12 if rate_period == "annual" else 1)
    if interest_type == "flat":
        monthly_interest = principal * monthly_rate
    else:
        monthly_interest = outstanding * monthly_rate
    return {
        "outstanding": round(outstanding, 2),
        "monthly_interest": round(monthly_interest, 2),
    }


def calc_emi(principal, annual_rate, months):
    if annual_rate == 0 or months == 0:
        return round(principal / months, 2) if months else 0
    r = annual_rate / 100 / 12
    return round(principal * r * (1 + r)**months / ((1 + r)**months - 1), 2)


# ──────────────────────────────────────────────────────────────────────────────
#  AI CLASSIFY
# ──────────────────────────────────────────────────────────────────────────────
async def ai_classify_other(description: str) -> dict:
    prompt = (
        'You are a financial data classifier for an Indian personal-finance app.\n'
        f'Description: "{description}"\n'
        'Respond ONLY with valid JSON (no markdown):\n'
        '{"entry_type":"asset or liability","category":"<name>","suggested_name":"<short name>","notes":"<1-2 sentences>"}\n'
        'Asset cats: Gold & Jewellery, Real Estate, Vehicle, Fixed Deposit, Market Investments, '
        'Retirement Fund, Cash & Savings, Insurance, Business / Equity, Other Asset\n'
        'Liability cats: Home Loan, Personal Loan, Vehicle Loan, Education Loan, '
        'Credit Card Due, EMI / BNPL, Mortgage Loan, Other Liability'
    )
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            res  = await c.post(OPENROUTER_URL,
            headers={"Authorization": f"Bearer {get_openrouter_api_key()}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}]})
            text = res.json()["choices"][0]["message"]["content"]
            text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(text)
    except Exception:
        return {"entry_type": "asset", "category": "Other Asset",
                "suggested_name": description[:60],
                "notes": "Could not auto-classify. Please review manually."}


# ──────────────────────────────────────────────────────────────────────────────
#  DETAIL SAVERS
# ──────────────────────────────────────────────────────────────────────────────
def _upsert_gold(eid, m):
    db_exec("DELETE FROM nw_gold WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_gold (entry_id,metal_type,karat,weight_g,rate_per_g) VALUES(%s,%s,%s,%s,%s)",
    (eid, m.get("metal_type"), m.get("karat"), m.get("weight_g", 0), m.get("rate_per_g", 0)))


def _upsert_real_estate(eid, m):
    db_exec("DELETE FROM nw_real_estate WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_real_estate (entry_id,property_type,area_sqft,purchase_price,current_price,purchase_date) VALUES(%s,%s,%s,%s,%s,%s)",
    (eid, m.get("property_type"), m.get("area_sqft", 0), m.get("purchase_price", 0),
    m.get("current_price", 0), m.get("purchase_date") or None))


def _upsert_vehicle(eid, m):
    db_exec("DELETE FROM nw_vehicle WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_vehicle (entry_id,vehicle_type,make_model,purchase_price,purchase_date,depreciation_rate,computed_value) VALUES(%s,%s,%s,%s,%s,%s,%s)",
    (eid, m.get("vehicle_type"), m.get("make_model"), m.get("purchase_price", 0),
    m.get("purchase_date") or None, m.get("depreciation_rate", 15), m.get("computed_value", 0)))


def _upsert_deposit(eid, m):
    db_exec("DELETE FROM nw_deposit WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_deposit (entry_id,deposit_type,principal,interest_rate,compounding,start_date,maturity_date,maturity_value) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
    (eid, m.get("deposit_type", "FD"), m.get("principal", 0), m.get("interest_rate", 0),
    m.get("compounding", "quarterly"), m.get("start_date") or None,
    m.get("maturity_date") or None, m.get("maturity_value", 0)))


def _upsert_investment(eid, m):
    db_exec("DELETE FROM nw_investment WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_investment (entry_id,inv_type,fund_name,payment_mode,invested_amount,current_value,expected_return,start_date) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
    (eid, m.get("inv_type"), m.get("fund_name", ""), m.get("payment_mode", "SIP"),
    m.get("invested_amount", 0), m.get("current_value", 0),
    m.get("expected_return", 0), m.get("start_date") or None))


def _upsert_loan(eid, m, asset_entry_id=None):
    db_exec("DELETE FROM nw_loan WHERE entry_id=%s", (eid,))
    db_exec("""INSERT INTO nw_loan
    (entry_id,loan_type,principal_original,principal_paid,principal_outstanding,
    interest_rate,rate_period,interest_type,loan_status,start_date,end_date,
    emi_amount,asset_entry_id)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
    (eid, m.get("loan_type", "General Loan"), m.get("principal_original", 0),
    m.get("principal_paid", 0), m.get("principal_outstanding", 0),
    m.get("interest_rate", 0), m.get("rate_period", "annual"),
    m.get("interest_type", "reducing"), m.get("loan_status", "current"),
    m.get("start_date") or None, m.get("end_date") or None,
    m.get("emi_amount", 0), asset_entry_id))


def _upsert_credit_card(eid, m):
    db_exec("DELETE FROM nw_credit_card WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_credit_card (entry_id,bank_name,outstanding,min_due,due_date,interest_rate) VALUES(%s,%s,%s,%s,%s,%s)",
    (eid, m.get("bank_name", ""), m.get("outstanding", 0),
    m.get("min_due", 0), m.get("due_date") or None, m.get("interest_rate", 36)))


def _upsert_emi(eid, m):
    db_exec("DELETE FROM nw_emi WHERE entry_id=%s", (eid,))
    db_exec("INSERT INTO nw_emi (entry_id,provider,item_name,total_amount,paid_amount,emi_amount,months_total,months_paid,start_date,end_date) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    (eid, m.get("provider", ""), m.get("item_name", ""), m.get("total_amount", 0),
    m.get("paid_amount", 0), m.get("emi_amount", 0), m.get("months_total", 0),
    m.get("months_paid", 0), m.get("start_date") or None, m.get("end_date") or None))


def _upsert_mortgage(eid, m):
    db_exec("DELETE FROM nw_mortgage WHERE entry_id=%s", (eid,))
    db_exec("""INSERT INTO nw_mortgage
    (entry_id,collateral_type,collateral_value,loan_amount,principal_paid,
    principal_outstanding,interest_rate,rate_period,interest_type,
    start_date,end_date,emi_amount,lender_name)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
    (eid, m.get("collateral_type", ""), m.get("collateral_value", 0),
    m.get("loan_amount", 0), m.get("principal_paid", 0),
    m.get("principal_outstanding", 0), m.get("interest_rate", 0),
    m.get("rate_period", "annual"), m.get("interest_type", "reducing"),
    m.get("start_date") or None, m.get("end_date") or None,
    m.get("emi_amount", 0), m.get("lender_name", "")))


_DETAIL_SAVERS = {
    "Gold & Jewellery":   _upsert_gold,
    "Real Estate":        _upsert_real_estate,
    "Vehicle":            _upsert_vehicle,
    "Fixed Deposit":      _upsert_deposit,
    "Market Investments": _upsert_investment,
    "Home Loan":          _upsert_loan,
    "Personal Loan":      _upsert_loan,
    "Vehicle Loan":       _upsert_loan,
    "Education Loan":     _upsert_loan,
    "Loan":               _upsert_loan,
    "Credit Card Due":    _upsert_credit_card,
    "EMI / BNPL":         _upsert_emi,
    "Mortgage Loan":      _upsert_mortgage,
}


# ──────────────────────────────────────────────────────────────────────────────
#  AUTO-CREATE / UPDATE LINKED LIABILITY
#  FIX 2: Properly compute outstanding and create liability
# ──────────────────────────────────────────────────────────────────────────────
def _sync_attached_loan(user_id: int, asset_entry_id: int, asset_name: str,
                        loan_meta: dict) -> Optional[int]:
    # FIX: handle both field naming conventions
    outstanding = float(
        loan_meta.get("loan_pending_amount") or
        loan_meta.get("principal_outstanding") or
        0
    )
    if outstanding <= 0:
        return None

    liab_name  = f"Loan on {asset_name}"
    liab_cat   = loan_meta.get("loan_category") or loan_meta.get("loan_type") or "Personal Loan"
    start_date = loan_meta.get("loan_start_date") or loan_meta.get("start_date") or None
    end_date   = loan_meta.get("loan_end_date") or loan_meta.get("end_date") or None

    existing = db_exec(
        "SELECT linked_liab_id FROM assets_liabilities WHERE id=%s", (asset_entry_id,), fetch=True)
    linked_id = existing[0]["linked_liab_id"] if existing else None

    loan_detail = {
        "loan_type":             liab_cat,
        "principal_original":    float(loan_meta.get("loan_total_amount") or outstanding),
        "principal_paid":        float(loan_meta.get("loan_paid_amount") or loan_meta.get("principal_paid") or 0),
        "principal_outstanding": outstanding,
        "interest_rate":         float(loan_meta.get("loan_interest_rate") or loan_meta.get("interest_rate") or 0),
        "rate_period":           loan_meta.get("loan_rate_period") or loan_meta.get("rate_period") or "annual",
        "interest_type":         loan_meta.get("loan_interest_type") or loan_meta.get("interest_type") or "reducing",
        "loan_status":           "current",
        "start_date":            start_date,
        "end_date":              end_date,
        "emi_amount":            float(loan_meta.get("loan_emi") or loan_meta.get("emi_amount") or 0),
    }
    meta_json = json.dumps({"name": liab_name, **loan_detail,
        "auto_generated": True, "asset_entry_id": asset_entry_id})

    if linked_id:
        db_exec("""UPDATE assets_liabilities
        SET name=%s, category=%s, current_value=%s, raw_meta=%s,
        start_date=%s, end_date=%s
        WHERE id=%s AND user_id=%s""",
        (liab_name, liab_cat, outstanding, meta_json,
        start_date, end_date, linked_id, user_id))
        _upsert_loan(linked_id, loan_detail, asset_entry_id=asset_entry_id)
        return linked_id
    else:
        new_id = db_exec(
            """INSERT INTO assets_liabilities
            (user_id,entry_type,category,name,current_value,raw_meta,start_date,end_date)
            VALUES(%s,'liability',%s,%s,%s,%s,%s,%s)""",
            (user_id, liab_cat, liab_name, outstanding, meta_json, start_date, end_date))
        _upsert_loan(new_id, loan_detail, asset_entry_id=asset_entry_id)
        db_exec("UPDATE assets_liabilities SET linked_liab_id=%s WHERE id=%s",
            (new_id, asset_entry_id))
        return new_id


def _remove_linked_liability(asset_entry_id: int, user_id: int):
    rows = db_exec("SELECT linked_liab_id FROM assets_liabilities WHERE id=%s AND user_id=%s",
    (asset_entry_id, user_id), fetch=True)
    if rows and rows[0]["linked_liab_id"]:
        lid = rows[0]["linked_liab_id"]
        db_exec("DELETE FROM assets_liabilities WHERE id=%s AND user_id=%s", (lid, user_id))
        db_exec("UPDATE assets_liabilities SET linked_liab_id=NULL WHERE id=%s", (asset_entry_id,))


# ──────────────────────────────────────────────────────────────────────────────
#  SUMMARY BUILDER
# ──────────────────────────────────────────────────────────────────────────────
def _item_from_row(r: dict) -> dict:
    return {
        "id":             r["id"],
        "entry_type":     r["entry_type"],
        "category":       r["category"],
        "name":           r["name"],
        "current_value":  float(r["current_value"]),
        "raw_meta":       json.loads(r["raw_meta"]) if r.get("raw_meta") else {},
        "start_date":     r["start_date"].isoformat()  if r.get("start_date")  else None,
        "end_date":       r["end_date"].isoformat()    if r.get("end_date")    else None,
        "linked_liab_id": r.get("linked_liab_id"),
        "created_at":     r["created_at"].strftime("%d %b %Y") if r.get("created_at") else "",
    }


def _build_summary(user_id: int) -> dict:
    rows = db_exec(
        """SELECT id,entry_type,category,name,current_value,raw_meta,
        start_date,end_date,linked_liab_id,created_at
        FROM assets_liabilities WHERE user_id=%s
        ORDER BY entry_type, created_at DESC""",
        (user_id,), fetch=True)
    assets, liabilities = [], []
    for r in (rows or []):
        (assets if r["entry_type"] == "asset" else liabilities).append(_item_from_row(r))
    ta = round(sum(a["current_value"] for a in assets), 2)
    tl = round(sum(l["current_value"] for l in liabilities), 2)
    return {"assets": assets, "liabilities": liabilities,
            "total_assets": ta, "total_liabilities": tl,
            "net_worth": round(ta - tl, 2)}


# ──────────────────────────────────────────────────────────────────────────────
#  ROUTES
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/all")
def get_all(user_id: int = Query(...)):
    return _build_summary(user_id)


@router.get("/summary")
def get_summary(user_id: int = Query(...)):
    s = _build_summary(user_id)
    return {k: s[k] for k in ("total_assets", "total_liabilities", "net_worth")}


# FIX 4: get_detail — all indentation fixed, item/detail code no longer inside 'if' block
@router.get("/detail/{entry_id}")
def get_detail(entry_id: int, user_id: int = Query(...)):
    rows = db_exec(
        "SELECT * FROM assets_liabilities WHERE id=%s AND user_id=%s",
        (entry_id, user_id), fetch=True)
    if not rows:
        raise HTTPException(404, "Entry not found")

    item = _item_from_row(rows[0])
    cat  = item["category"]

    detail_queries = {
        "Gold & Jewellery":   "SELECT * FROM nw_gold WHERE entry_id=%s",
        "Real Estate":        "SELECT * FROM nw_real_estate WHERE entry_id=%s",
        "Vehicle":            "SELECT * FROM nw_vehicle WHERE entry_id=%s",
        "Fixed Deposit":      "SELECT * FROM nw_deposit WHERE entry_id=%s",
        "Market Investments": "SELECT * FROM nw_investment WHERE entry_id=%s",
        "Home Loan":          "SELECT * FROM nw_loan WHERE entry_id=%s",
        "Personal Loan":      "SELECT * FROM nw_loan WHERE entry_id=%s",
        "Vehicle Loan":       "SELECT * FROM nw_loan WHERE entry_id=%s",
        "Education Loan":     "SELECT * FROM nw_loan WHERE entry_id=%s",
        "Loan":               "SELECT * FROM nw_loan WHERE entry_id=%s",
        "Credit Card Due":    "SELECT * FROM nw_credit_card WHERE entry_id=%s",
        "EMI / BNPL":         "SELECT * FROM nw_emi WHERE entry_id=%s",
        "Mortgage Loan":      "SELECT * FROM nw_mortgage WHERE entry_id=%s",
    }
    if cat in detail_queries:
        d_rows = db_exec(detail_queries[cat], (entry_id,), fetch=True)
        if d_rows:
            detail = {k: (v.isoformat() if isinstance(v, date) else
                          float(v) if hasattr(v, '__float__') and not isinstance(v, (int, bool)) else v)
                      for k, v in d_rows[0].items()}
            item["detail"] = detail

    return item


@router.post("/add")
async def add_entry(
    user_id:       int   = Form(...),
    entry_type:    str   = Form(...),
    category:      str   = Form(...),
    name:          str   = Form(...),
    current_value: float = Form(...),
    raw_meta:      str   = Form("{}"),
    start_date:    str   = Form(""),
    end_date:      str   = Form(""),
):
    entry_type = entry_type.strip().lower()
    if entry_type not in ("asset", "liability"):
        raise HTTPException(400, detail="'entry_type' must be 'asset' or 'liability'.")
    if current_value < 0:
        raise HTTPException(400, detail="'current_value' cannot be negative.")
    if not name.strip():
        raise HTTPException(400, detail="'name' is required.")

    entry_id = db_exec(
        """INSERT INTO assets_liabilities
        (user_id,entry_type,category,name,current_value,raw_meta,start_date,end_date)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
        (user_id, entry_type, category.strip(), name.strip(),
        round(current_value, 2), raw_meta, start_date or None, end_date or None))

    try:
        meta   = json.loads(raw_meta)
        saver  = _DETAIL_SAVERS.get(category)
        if saver:
            saver(entry_id, meta)
            # FIX: check both has_loan flag and direct liability categories
            if entry_type == "asset" and meta.get("has_loan"):
                pending = float(meta.get("loan_pending_amount") or meta.get("principal_outstanding") or 0)
                if pending > 0:
                    _sync_attached_loan(user_id, entry_id, name.strip(), meta)
    except Exception as e:
        print(f"[NetWorth add detail] {e}")

    return {"message": "Entry added ✓", **_build_summary(user_id)}


# FIX 2: update_entry — return now properly at function level
@router.put("/update/{entry_id}")
async def update_entry(
    entry_id:      int,
    user_id:       int   = Form(...),
    entry_type:    str   = Form(...),
    category:      str   = Form(...),
    name:          str   = Form(...),
    current_value: float = Form(...),
    raw_meta:      str   = Form("{}"),
    start_date:    str   = Form(""),
    end_date:      str   = Form(""),
):
    existing = db_exec("SELECT id FROM assets_liabilities WHERE id=%s AND user_id=%s",
    (entry_id, user_id), fetch=True)
    if not existing:
        raise HTTPException(404, "Entry not found.")

    entry_type = entry_type.strip().lower()
    db_exec("""UPDATE assets_liabilities
    SET entry_type=%s,category=%s,name=%s,current_value=%s,
    raw_meta=%s,start_date=%s,end_date=%s
    WHERE id=%s AND user_id=%s""",
    (entry_type, category.strip(), name.strip(), round(current_value, 2),
    raw_meta, start_date or None, end_date or None, entry_id, user_id))

    try:
        meta  = json.loads(raw_meta)
        saver = _DETAIL_SAVERS.get(category)
        if saver:
            saver(entry_id, meta)
            if entry_type == "asset":
                if meta.get("has_loan"):
                    pending = float(meta.get("loan_pending_amount") or meta.get("principal_outstanding") or 0)
                    if pending > 0:
                        _sync_attached_loan(user_id, entry_id, name.strip(), meta)
                    else:
                        _remove_linked_liability(entry_id, user_id)
                else:
                    _remove_linked_liability(entry_id, user_id)
    except Exception as e:
        print(f"[NetWorth update detail] {e}")

    return {"message": "Entry updated ✓", **_build_summary(user_id)}


@router.delete("/delete/{entry_id}")
def delete_entry(entry_id: int, user_id: int = Query(...)):
    existing = db_exec("SELECT id FROM assets_liabilities WHERE id=%s AND user_id=%s",
    (entry_id, user_id), fetch=True)
    if not existing:
        raise HTTPException(404, "Entry not found.")
    _remove_linked_liability(entry_id, user_id)
    db_exec("DELETE FROM assets_liabilities WHERE id=%s AND user_id=%s", (entry_id, user_id))
    return {"message": "Entry deleted ✓", **_build_summary(user_id)}


@router.post("/ai-classify")
async def ai_classify_endpoint(description: str = Form(...)):
    if not description.strip():
        raise HTTPException(400, detail="'description' is required.")
    return await ai_classify_other(description)


# ── COMPUTE HELPERS ───────────────────────────────────────────────────────────
@router.post("/compute/vehicle")
def compute_vehicle(
    purchase_price: float = Form(...),
    purchase_date:  str   = Form(...),
    vehicle_type:   str   = Form("Car"),
):
    rates = {"Car": 15, "Bike": 20, "Scooter": 20, "Truck": 15, "Van": 15, "Other": 15}
    dep   = rates.get(vehicle_type, 15)
    try:
        cv = calc_vehicle_depreciation(purchase_price, date.fromisoformat(purchase_date), dep)
    except Exception:
        cv = purchase_price
    return {"current_value": cv, "depreciation_rate": dep}


@router.post("/compute/fd")
def compute_fd(
    principal:     float = Form(...),
    interest_rate: float = Form(...),
    compounding:   str   = Form("quarterly"),
    start_date:    str   = Form(...),
    maturity_date: str   = Form(...),
):
    try:
        sd, md = date.fromisoformat(start_date), date.fromisoformat(maturity_date)
        mv = calc_fd_maturity(principal, interest_rate, sd, md, compounding)
        today = date.today()
        cv = mv if today >= md else calc_fd_maturity(principal, interest_rate, sd, today, compounding)
        return {"maturity_value": mv, "current_value": cv}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/compute/loan")
def compute_loan(
    principal:      float = Form(...),
    principal_paid: float = Form(0),
    interest_rate:  float = Form(...),
    rate_period:    str   = Form("annual"),
    interest_type:  str   = Form("reducing"),
    tenure_months:  int   = Form(0),
):
    result = calc_loan_outstanding(principal, interest_rate, rate_period, interest_type, principal_paid)
    annual = interest_rate if rate_period == "annual" else interest_rate * 12
    result["emi_amount"] = calc_emi(principal, annual, tenure_months) if tenure_months > 0 else 0
    return result
@router.post("/record-payment/{item_id}")
async def record_payment(
    item_id: int,
    user_id: int = Form(...),
    amount_paid: float = Form(...),
):
    print(f"[PAYMENT] Received request - item_id={item_id}, user_id={user_id}, amount_paid={amount_paid}")

    # Validate inputs
    if item_id <= 0:
        print(f"[PAYMENT] Invalid item_id: {item_id}")
        raise HTTPException(400, detail="Invalid item_id")
    if user_id <= 0:
        print(f"[PAYMENT] Invalid user_id: {user_id}")
        raise HTTPException(400, detail="Invalid user_id")
    if amount_paid <= 0:
        print(f"[PAYMENT] Invalid amount_paid: {amount_paid}")
        raise HTTPException(400, detail="amount_paid must be positive")

    rows = db_exec(
        "SELECT * FROM assets_liabilities WHERE id=%s AND user_id=%s",
        (item_id, user_id), fetch=True
    )
    print(f"[PAYMENT] Database query result: {rows}")

    if not rows:
        print(f"[PAYMENT] Entry not found for item_id={item_id}, user_id={user_id}")
        raise HTTPException(404, detail=f"Entry not found (item_id={item_id}, user_id={user_id})")

    item = rows[0]
    print(f"[PAYMENT] Found item: {item['name']}, current_value={item['current_value']}")

    new_value = max(0, float(item['current_value']) - amount_paid)
    print(f"[PAYMENT] Updating value from {item['current_value']} to {new_value}")

    update_result = db_exec(
        "UPDATE assets_liabilities SET current_value=%s WHERE id=%s AND user_id=%s",
        (new_value, item_id, user_id)
    )
    print(f"[PAYMENT] Update result: {update_result}")

    try:
        meta = json.loads(item.get('raw_meta') or '{}')
        if 'principal_outstanding' in meta:
            meta['principal_outstanding'] = max(0, float(meta['principal_outstanding']) - amount_paid)
        if 'outstanding' in meta:
            meta['outstanding'] = max(0, float(meta['outstanding']) - amount_paid)
        if 'current_balance' in meta:
            meta['current_balance'] = max(0, float(meta['current_balance']) - amount_paid)
        db_exec(
            "UPDATE assets_liabilities SET raw_meta=%s WHERE id=%s AND user_id=%s",
            (json.dumps(meta), item_id, user_id)
        )
        print(f"[PAYMENT] Meta updated successfully")
    except Exception as e:
        print(f"[Payment meta update] {e}")

    summary = db_exec(
        """SELECT
            SUM(CASE WHEN entry_type='asset' THEN current_value ELSE 0 END) as total_assets,
            SUM(CASE WHEN entry_type='liability' THEN current_value ELSE 0 END) as total_liabilities
           FROM assets_liabilities WHERE user_id=%s""",
        (user_id,), fetch=True
    )[0]

    ta = float(summary['total_assets'] or 0)
    tl = float(summary['total_liabilities'] or 0)

    result = {
        "message": "Payment recorded",
        "item_id": item_id,
        "new_value": new_value,
        "amount_paid": amount_paid,
        "total_assets": ta,
        "total_liabilities": tl,
        "net_worth": ta - tl,
    }
    print(f"[PAYMENT] Returning result: {result}")
    return result