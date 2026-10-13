"""
networth_enhanced.py — Enhanced Assets & Liabilities / Net Worth Tracker
======================================================================
FastAPI router module for Finace with dynamic field support.

Mount in main.py:
    from networth_enhanced import router as networth_router, init_networth_tables
    # inside startup():
    init_networth_tables()
    # after middleware:
    app.include_router(networth_router)

Features:
    - Dynamic input fields per asset/liability type
    - Automatic calculations (FD returns, Gold value, Loan interest, etc.)
    - AI-powered 'Other' category analysis
    - Flat vs Reducing balance loan calculations
"""

import re
import json
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from decimal import Decimal

import mysql.connector
from fastapi import APIRouter, HTTPException, Form, Query, Body
from pydantic import BaseModel

if __package__:
    from .config import get_database_config
else:
    from config import get_database_config

# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def db_exec(sql: str, params=None, fetch: bool = False, multi: bool = False):
    conn = mysql.connector.connect(**get_database_config())
    cur = conn.cursor(dictionary=True)
    if multi:
        cur.execute(sql, params or (), multi=True)
    else:
        cur.execute(sql, params or ())
    rows = cur.fetchall() if fetch else None
    conn.commit()
    cur.close()
    conn.close()
    return rows

# ─────────────────────────────────────────────────────────────────────────────
#  ROUTER
# ─────────────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/networth", tags=["Net Worth"])

# ─────────────────────────────────────────────────────────────────────────────
#  PYDANTIC MODELS
# ─────────────────────────────────────────────────────────────────────────────
class AssetLiabilityItem(BaseModel):
    id: int
    user_id: int
    type: str
    category: str
    name: str
    value: float
    metadata: Optional[Dict[str, Any]] = None
    details: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None

class NetWorthSummary(BaseModel):
    assets: List[AssetLiabilityItem]
    liabilities: List[AssetLiabilityItem]
    total_assets: float
    total_liabilities: float
    net_worth: float

class AIDetectResult(BaseModel):
    detected_type: str
    suggested_category: str
    confidence: str
    notes: str
    extracted_data: Optional[Dict[str, Any]] = None

class CalculationResult(BaseModel):
    category: str
    calculated_value: float
    breakdown: Dict[str, Any]

# ─────────────────────────────────────────────────────────────────────────────
#  TABLE BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────
CREATE_TABLES_SQL = """
-- Main assets_liabilities table with metadata JSON support
CREATE TABLE IF NOT EXISTS assets_liabilities (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT            NOT NULL,
    type        VARCHAR(20)    NOT NULL COMMENT 'asset or liability',
    category    VARCHAR(80)    NOT NULL,
    name        VARCHAR(180)   NOT NULL,
    value       DECIMAL(16,2)  NOT NULL DEFAULT 0,
    metadata    JSON           NULL COMMENT 'Dynamic fields for each category',
    details     TEXT,
    created_at  DATETIME       DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_al_user (user_id),
    INDEX idx_al_type (type),
    INDEX idx_al_category (category)
);

-- Market rates reference table
CREATE TABLE IF NOT EXISTS market_rates (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    metal_type  VARCHAR(20)    NOT NULL,
    carat       VARCHAR(10)    NOT NULL,
    rate_per_gram DECIMAL(12,2) NOT NULL,
    currency    VARCHAR(3)     DEFAULT 'INR',
    updated_at  DATETIME       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_metal_carat (metal_type, carat)
);

-- Vehicle depreciation rates
CREATE TABLE IF NOT EXISTS vehicle_depreciation (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_type    VARCHAR(30)    NOT NULL,
    year_1          DECIMAL(5,2)   NOT NULL DEFAULT 20.00,
    year_2          DECIMAL(5,2)   NOT NULL DEFAULT 15.00,
    year_3          DECIMAL(5,2)   NOT NULL DEFAULT 12.00,
    year_4          DECIMAL(5,2)   NOT NULL DEFAULT 10.00,
    year_5_plus     DECIMAL(5,2)   NOT NULL DEFAULT 8.00,
    UNIQUE KEY uniq_vtype (vehicle_type)
);
"""

# Initialize default rates
DEFAULT_GOLD_RATES = [
    ("Gold", "24K", 7500.00),
    ("Gold", "22K", 6875.00),
    ("Gold", "18K", 5625.00),
    ("Silver", "Fine", 95.00),
]

DEFAULT_VEHICLE_DEPRECIATION = [
    ("Car", 20.00, 15.00, 12.00, 10.00, 8.00),
    ("Bike", 25.00, 18.00, 15.00, 12.00, 10.00),
    ("Van", 18.00, 14.00, 11.00, 9.00, 7.00),
    ("Truck", 15.00, 12.00, 10.00, 8.00, 6.00),
    ("Auto", 22.00, 16.00, 13.00, 11.00, 9.00),
]

def init_networth_tables():
    """Call this from main.py startup event."""
    try:
        db_exec(CREATE_TABLES_SQL, multi=True)

        # Insert default gold rates if empty
        existing = db_exec("SELECT COUNT(*) as cnt FROM market_rates", fetch=True)
        if existing and existing[0]['cnt'] == 0:
            for metal, carat, rate in DEFAULT_GOLD_RATES:
                db_exec(
                    "INSERT INTO market_rates (metal_type, carat, rate_per_gram) VALUES (%s, %s, %s)",
                    (metal, carat, rate)
                )

        # Insert default vehicle depreciation if empty
        existing_v = db_exec("SELECT COUNT(*) as cnt FROM vehicle_depreciation", fetch=True)
        if existing_v and existing_v[0]['cnt'] == 0:
            for row in DEFAULT_VEHICLE_DEPRECIATION:
                db_exec(
                    """INSERT INTO vehicle_depreciation
                    (vehicle_type, year_1, year_2, year_3, year_4, year_5_plus)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    row
                )

        print("[NetWorth Enhanced] Tables ready ✓")
    except Exception as e:
        print(f"[NetWorth Enhanced] Table init warning: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#  CALCULATION FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def calculate_gold_value(metal_type: str, carat: str, weight_grams: float) -> float:
    """Calculate gold/jewelry value based on current market rate."""
    rows = db_exec(
        "SELECT rate_per_gram FROM market_rates WHERE metal_type=%s AND carat=%s",
        (metal_type, carat), fetch=True
    )
    if rows:
        rate = float(rows[0]['rate_per_gram'])
        return round(weight_grams * rate, 2)
    return 0.0

def calculate_fd_maturity(principal: float, rate: float, years: float,
                          compound: bool = True, frequency: int = 4) -> Dict[str, Any]:
    """Calculate Fixed Deposit maturity value."""
    if compound:
        # A = P(1 + r/n)^(nt)
        r = rate / 100
        n = frequency
        t = years
        amount = principal * ((1 + r/n) ** (n*t))
        interest = amount - principal
    else:
        # Simple interest: A = P(1 + rt)
        r = rate / 100
        interest = principal * r * years
        amount = principal + interest

    return {
        "principal": round(principal, 2),
        "interest_earned": round(interest, 2),
        "maturity_value": round(amount, 2),
        "total_return_percent": round((amount - principal) / principal * 100, 2) if principal > 0 else 0,
        "calculation_type": "compound" if compound else "simple"
    }

def calculate_investment_returns(amount: float, rate: float, years: float,
                                investment_type: str, sip: bool = False) -> Dict[str, Any]:
    """Calculate expected returns for market investments."""
    if sip:
        # SIP calculation (monthly)
        monthly_rate = rate / 100 / 12
        months = int(years * 12)
        # FV = P × [(1 + r)^n - 1] / r × (1 + r)
        if monthly_rate > 0:
            future_value = amount * (((1 + monthly_rate) ** months - 1) / monthly_rate) * (1 + monthly_rate)
        else:
            future_value = amount * months
        total_invested = amount * months
    else:
        # Lumpsum
        future_value = amount * ((1 + rate / 100) ** years)
        total_invested = amount

    return {
        "investment_type": investment_type,
        "amount_invested": round(total_invested, 2),
        "expected_value": round(future_value, 2),
        "expected_returns": round(future_value - total_invested, 2),
        "return_percentage": round((future_value - total_invested) / total_invested * 100, 2) if total_invested > 0 else 0,
        "sip": sip
    }

def calculate_vehicle_depreciation(purchase_value: float, purchase_year: int,
                                   vehicle_type: str = "Car") -> Dict[str, Any]:
    """Calculate current vehicle value with depreciation."""
    current_year = datetime.now().year
    age = max(0, current_year - purchase_year)

    rows = db_exec(
        "SELECT * FROM vehicle_depreciation WHERE vehicle_type=%s",
        (vehicle_type,), fetch=True
    )

    if rows:
        rates = rows[0]
        if age == 0:
            depreciation_rate = 0
        elif age == 1:
            depreciation_rate = float(rates['year_1'])
        elif age == 2:
            depreciation_rate = float(rates['year_1']) + float(rates['year_2'])
        elif age == 3:
            depreciation_rate = float(rates['year_1']) + float(rates['year_2']) + float(rates['year_3'])
        elif age == 4:
            depreciation_rate = float(rates['year_1']) + float(rates['year_2']) + float(rates['year_3']) + float(rates['year_4'])
        else:
            base_rate = float(rates['year_1']) + float(rates['year_2']) + float(rates['year_3']) + float(rates['year_4'])
            additional_years = age - 4
            depreciation_rate = base_rate + (float(rates['year_5_plus']) * additional_years)
    else:
        # Default rates if not found
        depreciation_rate = min(80, age * 15)  # Cap at 80%

    depreciation_rate = min(depreciation_rate, 90)  # Cap at 90%
    current_value = purchase_value * (1 - depreciation_rate / 100)

    return {
        "purchase_value": round(purchase_value, 2),
        "current_value": round(current_value, 2),
        "depreciation_percent": round(depreciation_rate, 2),
        "vehicle_age": age,
        "vehicle_type": vehicle_type
    }

def calculate_loan_details(principal: float, rate: float, tenure_months: int,
                           interest_type: str = "reducing",
                           rate_per: str = "year") -> Dict[str, Any]:
    """Calculate loan EMI and total payment."""

    # Convert rate to monthly if needed
    if rate_per == "year":
        monthly_rate = rate / 100 / 12
    else:
        monthly_rate = rate / 100

    if interest_type == "flat":
        # Flat interest: Interest calculated on original principal throughout
        total_interest = principal * (rate / 100 if rate_per == "year" else rate * 12 / 100) * (tenure_months / 12 if rate_per == "year" else tenure_months)
        total_payment = principal + total_interest
        emi = total_payment / tenure_months

        schedule = []
        remaining = principal
        for month in range(1, tenure_months + 1):
            interest = total_interest / tenure_months
            principal_paid = principal / tenure_months
            remaining -= principal_paid
            schedule.append({
                "month": month,
                "emi": round(emi, 2),
                "principal": round(principal_paid, 2),
                "interest": round(interest, 2),
                "remaining": round(max(0, remaining), 2)
            })
    else:
        # Reducing balance: EMI = [P × R × (1+R)^N] / [(1+R)^N-1]
        if monthly_rate > 0:
            emi = principal * monthly_rate * ((1 + monthly_rate) ** tenure_months) / (((1 + monthly_rate) ** tenure_months) - 1)
        else:
            emi = principal / tenure_months

        total_payment = emi * tenure_months
        total_interest = total_payment - principal

        # Generate amortization schedule
        schedule = []
        remaining = principal
        for month in range(1, min(tenure_months + 1, 12 * 30 + 1)):  # Limit to 30 years for display
            interest = remaining * monthly_rate
            principal_paid = emi - interest
            remaining -= principal_paid
            if month <= tenure_months:
                schedule.append({
                    "month": month,
                    "emi": round(emi, 2),
                    "principal": round(principal_paid, 2),
                    "interest": round(interest, 2),
                    "remaining": round(max(0, remaining), 2)
                })

    return {
        "principal": round(principal, 2),
        "emi": round(emi, 2),
        "total_interest": round(total_interest, 2),
        "total_payment": round(total_payment, 2),
        "interest_type": interest_type,
        "rate_per": rate_per,
        "tenure_months": tenure_months,
        "schedule_preview": schedule[:6]  # First 6 months
    }

def calculate_current_loan_status(principal: float, rate: float, tenure_months: int,
                                  start_date: str, amount_paid: float,
                                  interest_type: str = "reducing",
                                  rate_per: str = "year") -> Dict[str, Any]:
    """Calculate current loan status based on payments made."""
    loan = calculate_loan_details(principal, rate, tenure_months, interest_type, rate_per)

    # Calculate months elapsed since start
    start = datetime.strptime(start_date, "%Y-%m-%d")
    now = datetime.now()
    months_elapsed = (now.year - start.year) * 12 + (now.month - start.month)

    # Find current status
    total_emi_paid = loan["emi"] * months_elapsed
    remaining_principal = principal - (amount_paid - (total_emi_paid - loan["emi"] * months_elapsed + loan["total_interest"] * months_elapsed / tenure_months))

    if interest_type == "flat":
        remaining = principal - (amount_paid - (loan["total_interest"] * months_elapsed / tenure_months))
    else:
        remaining = loan["schedule_preview"][min(months_elapsed - 1, len(loan["schedule_preview"]) - 1)]["remaining"] if months_elapsed <= len(loan["schedule_preview"]) else 0

    remaining_months = max(0, tenure_months - months_elapsed)

    return {
        "original_loan": loan,
        "months_elapsed": months_elapsed,
        "months_remaining": remaining_months,
        "amount_paid": round(amount_paid, 2),
        "remaining_principal": round(remaining, 2),
        "current_progress_percent": round((amount_paid / loan["total_payment"]) * 100, 2),
        "is_completed": remaining <= 0
    }

# ─────────────────────────────────────────────────────────────────────────────
#  AI KEYWORD DETECTOR (Enhanced with data extraction)
# ─────────────────────────────────────────────────────────────────────────────
AI_RULES = [
    # ASSETS
    (r"\bgold\b|\bjewel(?:le?ry)?\b|\bnecklace\b|\bbangle\b|\bcoin\b|\bsilver\b|\bgrams?\b|\btola\b",
     "asset", "Gold & Jewellery",
     "Gold and jewellery are tangible assets. Extract weight in grams and purity/carat."),

    (r"\bland\b|\bplot\b|\bsite\b|\bproperty\b|\breal\s*estate\b|\bapartment\b|\bflat\b|\bhouse\b|\bvilla\b",
     "asset", "Real Estate",
     "Real estate typically appreciates over time. Record property type, location, and current market value."),

    (r"\bcar\b|\bvehicle\b|\bbike\b|\bscooter\b|\bmotorcycle\b|\bsuv\b|\btruck\b|\bauto\b",
     "asset", "Vehicle",
     "Vehicles depreciate over time. Extract vehicle type, purchase year, and current resale value."),

    (r"\bfixed\s*deposit\b|\bfd\b|\brecurring\b|\brd\b|\bterm\s*deposit\b",
     "asset", "Fixed Deposit",
     "Bank deposits earn guaranteed interest. Extract principal, interest rate, tenure, and compounding type."),

    (r"\bmutual\s*fund\b|\bsip\b|\bswp\b|\bequity\b|\bstocks?\b|\bshares?\b|\bdemat\b",
     "asset", "Market Investments",
     "Market-linked investments. Extract investment type (MF/Stocks), amount, returns rate, and SIP/lumpsum format."),

    (r"\bpf\b|\bprovident\s*fund\b|\bepf\b|\bppf\b|\bnps\b|\bgratuity\b|\bpension\b",
     "asset", "Retirement Fund",
     "Retirement corpus. Extract fund type and current balance."),

    (r"\bcash\b|\bsavings?\b|\bcurrent\s*account\b|\bwallet\b|\bbank\s*balance\b",
     "asset", "Cash & Savings",
     "Liquid assets. Extract account type and current balance."),

    (r"\binsurance\b|\blic\b|\bterm\s*plan\b|\bulip\b|\bsurrender\s*value\b",
     "asset", "Insurance (Surrender Value)",
     "Life policies with surrender value. Extract policy type and surrender value."),

    # LIABILITIES
    (r"\bhome\s*loan\b|\bhousing\s*loan\b|\bmortgage\b|\bhouse\s*loan\b",
     "liability", "Home Loan",
     "Extract principal, interest rate, tenure, interest type (flat/reducing), and start date."),

    (r"\bpersonal\s*loan\b|\bunsecured\s*loan\b",
     "liability", "Personal Loan",
     "High-interest unsecured debt. Extract principal, rate, tenure, interest calculation type."),

    (r"\bcar\s*loan\b|\bvehicle\s*loan\b|\bauto\s*loan\b|\bbike\s*loan\b",
     "liability", "Vehicle Loan",
     "Outstanding vehicle finance. Extract principal, rate, tenure, and current status."),

    (r"\bstudent\s*loan\b|\beducation\s*loan\b|\bcollege\s*loan\b",
     "liability", "Education Loan",
     "Educational debt. Extract principal, interest rate, and moratorium status."),

    (r"\bcredit\s*card\b|\bcc\s*due\b|\bcard\s*due\b",
     "liability", "Credit Card Due",
     "Extract outstanding balance, bank name, and due date."),

    (r"\bemi\b|\binstalment\b|\bbnpl\b",
     "liability", "EMI / BNPL",
     "Extract total outstanding, monthly EMI, and remaining months."),

    (r"\bloan\b|\blent\b|\bborrowed\b",
     "liability", "Loan",
     "General loan. Extract principal, interest rate, and tenure."),
]

def extract_numbers(text: str) -> List[float]:
    """Extract all numbers from text."""
    numbers = re.findall(r'\d+(?:\.\d+)?', text)
    return [float(n) for n in numbers]

def extract_dates(text: str) -> List[str]:
    """Extract dates in various formats."""
    # YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, Month DD YYYY
    patterns = [
        r'\d{4}-\d{2}-\d{2}',
        r'\d{2}/\d{2}/\d{4}',
        r'\d{2}-\d{2}-\d{4}',
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}',
    ]
    dates = []
    for pattern in patterns:
        dates.extend(re.findall(pattern, text, re.IGNORECASE))
    return dates

def ai_detect_with_extraction(description: str) -> AIDetectResult:
    """Enhanced AI detection with data extraction."""
    text = description.lower().strip()
    numbers = extract_numbers(text)
    dates = extract_dates(text)

    extracted_data = {}

    for pattern, kind, category, notes in AI_RULES:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            confidence = "high" if len(matches) >= 2 else "medium"

            # Extract relevant data based on category
            if category in ["Gold & Jewellery"]:
                # Look for grams, carat
                carat_match = re.search(r'(\d{2})\s*[kc]', text)
                if carat_match:
                    extracted_data["carat"] = carat_match.group(1) + "K"
                gram_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:grams?|g)\b', text)
                if gram_match:
                    extracted_data["weight_grams"] = float(gram_match.group(1))
                if "silver" in text:
                    extracted_data["metal_type"] = "Silver"
                elif "gold" in text:
                    extracted_data["metal_type"] = "Gold"

            elif category in ["Fixed Deposit", "Market Investments", "Retirement Fund"]:
                # Look for amounts and rates
                if len(numbers) >= 1:
                    extracted_data["amount"] = max(numbers)  # Assume largest is principal
                if len(numbers) >= 2:
                    # Find rate (typically smaller number with %)
                    rate_match = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
                    if rate_match:
                        extracted_data["rate"] = float(rate_match.group(1))

            elif category in ["Vehicle"]:
                year_match = re.search(r'\b(20\d{2}|19\d{2})\b', text)
                if year_match:
                    extracted_data["purchase_year"] = int(year_match.group(1))

            elif category in ["Home Loan", "Personal Loan", "Vehicle Loan", "Education Loan", "Loan"]:
                if len(numbers) >= 1:
                    extracted_data["principal"] = max(numbers)
                rate_match = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
                if rate_match:
                    extracted_data["rate"] = float(rate_match.group(1))
                year_match = re.search(r'(\d+)\s*years?', text)
                if year_match:
                    extracted_data["tenure_years"] = int(year_match.group(1))
                if dates:
                    extracted_data["start_date"] = dates[0]
                if "flat" in text:
                    extracted_data["interest_type"] = "flat"
                elif "reduc" in text:
                    extracted_data["interest_type"] = "reducing"

            return AIDetectResult(
                detected_type=kind,
                suggested_category=category,
                confidence=confidence,
                notes=notes,
                extracted_data=extracted_data
            )

    return AIDetectResult(
        detected_type="unknown",
        suggested_category="Others",
        confidence="low",
        notes="Could not auto-detect type. Please select manually or provide more details.",
        extracted_data={}
    )

# ─────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _row_to_item(row: dict) -> AssetLiabilityItem:
    created = row.get("created_at")
    updated = row.get("updated_at")
    metadata = row.get("metadata")

    return AssetLiabilityItem(
        id=row["id"],
        user_id=row["user_id"],
        type=row["type"],
        category=row["category"],
        name=row["name"],
        value=float(row["value"]),
        metadata=json.loads(metadata) if metadata else None,
        details=row.get("details"),
        created_at=created.strftime("%d %b %Y, %I:%M %p") if isinstance(created, datetime) else str(created or ""),
        updated_at=updated.strftime("%d %b %Y, %I:%M %p") if isinstance(updated, datetime) else str(updated or "")
    )

def _build_summary(rows: list) -> dict:
    assets = [_row_to_item(r) for r in rows if r["type"] == "asset"]
    liabilities = [_row_to_item(r) for r in rows if r["type"] == "liability"]
    total_assets = round(sum(a.value for a in assets), 2)
    total_liabilities = round(sum(l.value for l in liabilities), 2)
    net_worth = round(total_assets - total_liabilities, 2)
    return {
        "assets": [a.model_dump() for a in assets],
        "liabilities": [l.model_dump() for l in liabilities],
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "net_worth": net_worth,
    }

# ─────────────────────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/add", summary="Add an asset or liability with dynamic metadata")
def add_entry(
    user_id: int = Form(...),
    type: str = Form(...),
    category: str = Form(...),
    name: str = Form(...),
    value: float = Form(...),
    details: str = Form(""),
    metadata: str = Form("{}"),  # JSON string
):
    """Add entry with optional metadata for category-specific fields."""
    type = type.strip().lower()
    if type not in ("asset", "liability"):
        raise HTTPException(400, detail="'type' must be 'asset' or 'liability'.")
    if value < 0:
        raise HTTPException(400, detail="'value' cannot be negative.")
    if not name.strip():
        raise HTTPException(400, detail="'name' is required.")

    # Validate and store metadata
    try:
        meta_dict = json.loads(metadata) if metadata else {}
    except:
        meta_dict = {}

    db_exec(
        """INSERT INTO assets_liabilities
           (user_id, type, category, name, value, metadata, details)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (user_id, type, category.strip(), name.strip(),
         round(value, 2), json.dumps(meta_dict) if meta_dict else None,
         details.strip() or None)
    )

    rows = db_exec(
        "SELECT * FROM assets_liabilities WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,), fetch=True
    )
    return {"message": "Entry added successfully ✓", **_build_summary(rows or [])}

@router.get("/all", summary="Get all assets & liabilities")
def get_all(user_id: int = Query(...)):
    rows = db_exec(
        "SELECT * FROM assets_liabilities WHERE user_id=%s ORDER BY type, created_at DESC",
        (user_id,), fetch=True
    )
    return _build_summary(rows or [])

@router.put("/update/{entry_id}", summary="Update entry with metadata")
def update_entry(
    entry_id: int,
    user_id: int = Form(...),
    type: str = Form(...),
    category: str = Form(...),
    name: str = Form(...),
    value: float = Form(...),
    details: str = Form(""),
    metadata: str = Form("{}"),
):
    type = type.strip().lower()
    if type not in ("asset", "liability"):
        raise HTTPException(400, detail="'type' must be 'asset' or 'liability'.")

    try:
        meta_dict = json.loads(metadata) if metadata else {}
    except:
        meta_dict = {}

    existing = db_exec(
        "SELECT id FROM assets_liabilities WHERE id=%s AND user_id=%s",
        (entry_id, user_id), fetch=True
    )
    if not existing:
        raise HTTPException(404, detail="Entry not found or access denied.")

    db_exec(
        """UPDATE assets_liabilities
           SET type=%s, category=%s, name=%s, value=%s, metadata=%s, details=%s
           WHERE id=%s AND user_id=%s""",
        (type, category.strip(), name.strip(), round(value, 2),
         json.dumps(meta_dict) if meta_dict else None, details.strip() or None,
         entry_id, user_id)
    )

    rows = db_exec(
        "SELECT * FROM assets_liabilities WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,), fetch=True
    )
    return {"message": "Entry updated successfully ✓", **_build_summary(rows or [])}

@router.delete("/delete/{entry_id}")
def delete_entry(entry_id: int, user_id: int = Query(...)):
    existing = db_exec(
        "SELECT id FROM assets_liabilities WHERE id=%s AND user_id=%s",
        (entry_id, user_id), fetch=True
    )
    if not existing:
        raise HTTPException(404, detail="Entry not found or access denied.")

    db_exec("DELETE FROM assets_liabilities WHERE id=%s AND user_id=%s", (entry_id, user_id))

    rows = db_exec(
        "SELECT * FROM assets_liabilities WHERE user_id=%s ORDER BY created_at DESC",
        (user_id,), fetch=True
    )
    return {"message": "Entry deleted successfully ✓", **_build_summary(rows or [])}

# ─────────────────────────────────────────────────────────────────────────────
#  CALCULATION ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/calculate/gold", summary="Calculate gold value from weight and purity")
def calc_gold(metal_type: str = Form(...), carat: str = Form(...), weight_grams: float = Form(...)):
    value = calculate_gold_value(metal_type, carat, weight_grams)
    rates = db_exec(
        "SELECT rate_per_gram FROM market_rates WHERE metal_type=%s AND carat=%s",
        (metal_type, carat), fetch=True
    )
    rate = float(rates[0]['rate_per_gram']) if rates else 0
    return {
        "metal_type": metal_type,
        "carat": carat,
        "weight_grams": weight_grams,
        "rate_per_gram": rate,
        "calculated_value": value
    }

@router.post("/calculate/fd", summary="Calculate Fixed Deposit returns")
def calc_fd(
    principal: float = Form(...),
    rate: float = Form(...),
    years: float = Form(...),
    compound: bool = Form(True),
    frequency: int = Form(4)
):
    result = calculate_fd_maturity(principal, rate, years, compound, frequency)
    return result

@router.post("/calculate/investment", summary="Calculate investment returns")
def calc_investment(
    amount: float = Form(...),
    rate: float = Form(...),
    years: float = Form(...),
    investment_type: str = Form("mutual_fund"),
    sip: bool = Form(False)
):
    result = calculate_investment_returns(amount, rate, years, investment_type, sip)
    return result

@router.post("/calculate/vehicle", summary="Calculate depreciated vehicle value")
def calc_vehicle(
    purchase_value: float = Form(...),
    purchase_year: int = Form(...),
    vehicle_type: str = Form("Car")
):
    result = calculate_vehicle_depreciation(purchase_value, purchase_year, vehicle_type)
    return result

@router.post("/calculate/loan", summary="Calculate loan EMI and schedule")
def calc_loan(
    principal: float = Form(...),
    rate: float = Form(...),
    tenure_months: int = Form(...),
    interest_type: str = Form("reducing"),
    rate_per: str = Form("year")
):
    result = calculate_loan_details(principal, rate, tenure_months, interest_type, rate_per)
    return result

@router.post("/calculate/loan-status", summary="Calculate current loan status")
def calc_loan_status(
    principal: float = Form(...),
    rate: float = Form(...),
    tenure_months: int = Form(...),
    start_date: str = Form(...),  # YYYY-MM-DD
    amount_paid: float = Form(...),
    interest_type: str = Form("reducing"),
    rate_per: str = Form("year")
):
    result = calculate_current_loan_status(principal, rate, tenure_months, start_date,
                                           amount_paid, interest_type, rate_per)
    return result

# ─────────────────────────────────────────────────────────────────────────────
#  AI ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/ai-detect", summary="AI detection with data extraction")
def ai_detect_endpoint(description: str = Form(...)):
    if not description.strip():
        raise HTTPException(400, detail="'description' is required.")
    return ai_detect_with_extraction(description).model_dump()

@router.post("/ai-analyze-other", summary="AI analysis for 'Other' category entries")
def ai_analyze_other(
    description: str = Form(...),
    entry_type: str = Form(...)  # 'asset' or 'liability'
):
    """
    Advanced AI analysis for custom/other entries.
    Returns suggested category, extracted values, and structured data.
    """
    text = description.lower().strip()

    # First try standard detection
    detection = ai_detect_with_extraction(description)

    # If still unknown, use broader patterns
    if detection.detected_type == "unknown":
        # Asset patterns
        asset_indicators = [
            r'\bown\b', r'\bhave\b', r'\bpossess\b', r'\basset\b',
            r'\bworth\b', r'\bvalue\b', r'\binvestment\b'
        ]
        # Liability patterns
        liability_indicators = [
            r'\bowe\b', r'\bloan\b', r'\bdebt\b', r'\bliability\b',
            r'\bpay\b', r'\bdue\b', r'\bemi\b', r'\bborrowed\b'
        ]

        is_asset = any(re.search(p, text) for p in asset_indicators)
        is_liability = any(re.search(p, text) for p in liability_indicators)

        if entry_type == "asset" or (is_asset and not is_liability):
            detection.detected_type = "asset"
            detection.suggested_category = "Others"
            detection.confidence = "medium"
            detection.notes = "Detected as asset based on language patterns."
        elif entry_type == "liability" or is_liability:
            detection.detected_type = "liability"
            detection.suggested_category = "Others"
            detection.confidence = "medium"
            detection.notes = "Detected as liability based on language patterns."

    # Extract any numbers as potential values
    numbers = extract_numbers(text)
    if numbers and not detection.extracted_data.get("amount"):
        detection.extracted_data["suggested_value"] = max(numbers)

    return detection.model_dump()

@router.get("/market-rates", summary="Get current market rates for gold/silver")
def get_market_rates():
    rows = db_exec("SELECT * FROM market_rates ORDER BY metal_type, carat", fetch=True)
    return {"rates": rows}

@router.get("/vehicle-depreciation", summary="Get vehicle depreciation rates")
def get_vehicle_rates():
    rows = db_exec("SELECT * FROM vehicle_depreciation", fetch=True)
    return {"rates": rows}
