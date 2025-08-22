import os, re, io
import pandas as pd
import pdfplumber
from datetime import datetime, timedelta
from dateutil import parser as dateparser

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Text
from sqlalchemy.orm import sessionmaker, declarative_base

# ----------------- Flask Setup -----------------
app = Flask(__name__, static_folder="static", template_folder="../frontend")
CORS(app)

@app.route("/")
def home():
    return render_template("index.html")

# ----------------- Database Setup -----------------
DB_PATH = os.path.join(os.path.dirname(__file__), "finance.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    amount = Column(Float, nullable=False)  # positive=income, negative=expense
    mode = Column(String(64), default="")
    category = Column(String(64), default="Others")

class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    category = Column(String(64), unique=True, nullable=False)
    limit = Column(Float, nullable=False)

class Bill(Base):
    __tablename__ = "bills"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    due = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)

Base.metadata.create_all(engine)

# ----------------- Seed default budgets -----------------
def seed_budgets():
    with SessionLocal() as db:
        if db.query(Budget).count() == 0:
            defaults = [
                ("Food & Dining", 12000),
                ("Transport", 6000),
                ("Bills & Utilities", 10000),
                ("Shopping", 15000),
                ("Entertainment", 4000),
            ]
            for cat, lim in defaults:
                db.add(Budget(category=cat, limit=lim))
            db.commit()
seed_budgets()

# ----------------- Categorization -----------------
RULES = [
    (r"swiggy|zomato|domino|pizza|kfc|mcd", "Food & Dining", "Expenses"),
    (r"ola|uber|metro|irctc|railway|bus|fuel|petrol|diesel |metro fare", "Expenses","Transport"),
    (r"reliance jio|airtel|vodafone|vi|electricity|water bill|gas|dth|bbps|fastag|recharge", "Bills & Utilities", "Expenses"),
    (r"amazon|flipkart|myntra|ajio|tata cliq", "Shopping","Expenses"),
    (r"lic|insurance|emi|loan|hdfc loan|sbi loan", "EMI/Loans","Expenses"),
    (r"sip|mutual fund|index fund|nifty|ppf|nps|fd|rd", "Investments","Expenses"),
    (r"salary|neft|imps|upi inflow|refund|credited", "Income"),
    (r"credit card|debit card|citi|hdfc|icici|axis|sbi", "Expenses"),
    (r"netflix|prime|hotstar|spotify|youtube premium|Movie ", "Entertainment","Expenses"),
]
def categorize(desc: str):
    d = desc.lower()
    for pat, cat in RULES:
        if re.search(pat, d):
            return cat
    if "upi" in d:
        return "UPI Payment"
    return "Others"

# ----------------- File Parsers -----------------
def parse_csv(file_storage):
    df = pd.read_csv(file_storage)
    cols = {c.lower(): c for c in df.columns}
    date_col = cols.get("date") or list(df.columns)[0]
    desc_col = cols.get("description") or list(df.columns)[1]
    amt_col = cols.get("amount") or list(df.columns)[2]

    out = []
    for _, row in df.iterrows():
        try:
            dt = dateparser.parse(str(row[date_col])).date()
        except:
            continue
        desc = str(row[desc_col])
        amt_raw = row[amt_col]
        try:
            amt = float(amt_raw)
        except:
            m = re.findall(r"([-+]?[0-9]*\.?[0-9]+)", str(amt_raw))
            amt = float(m[0]) if m else 0.0
            if "cr" in str(amt_raw).lower(): amt = abs(amt)
            if "dr" in str(amt_raw).lower(): amt = -abs(amt)
        out.append(dict(date=dt, description=desc, amount=amt, mode="CSV Import", category=categorize(desc)))
    return out

def parse_pdf(file_storage):
    out = []
    with pdfplumber.open(file_storage) as pdf:
        for page in pdf.pages:
            lines = [ln.strip() for ln in (page.extract_text() or "").splitlines() if ln.strip()]
            for ln in lines:
                m = re.match(r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s+(.*)\s+([-+]?[0-9]*\.?[0-9]+)\s*(Cr|Dr)?", ln, re.I)
                if m:
                    dt = dateparser.parse(m.group(1)).date()
                    desc = m.group(2)[:200]
                    amt = float(m.group(3))
                    if (m.group(4) or "").lower() == "dr": amt = -abs(amt)
                    if (m.group(4) or "").lower() == "cr": amt = abs(amt)
                    out.append(dict(date=dt, description=desc, amount=amt, mode="PDF Import", category=categorize(desc)))
    return out

# ----------------- Insights -----------------
def compute_insights(df: pd.DataFrame):
    tips = []
    income = df[df.amount > 0]["amount"].sum()
    expense = abs(df[df.amount < 0]["amount"].sum())
    if expense > 0.8 * income:
        tips.append("High expense-to-income ratio (>80%). Consider trimming discretionary spends.")
    by_cat = df[df.amount < 0].groupby("category")["amount"].apply(lambda s: abs(s.sum())).sort_values(ascending=False)
    if "Food & Dining" in by_cat and by_cat["Food & Dining"] > 10000:
        tips.append("Food & Dining >₹10k. Try home-cooking to save more.")
    if not tips:
        tips.append("Spending is within typical range. Set a savings goal.")
    return tips

# ----------------- API Endpoints -----------------
@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    filename = f.filename.lower()
    try:
        if filename.endswith(".csv"):
            txs = parse_csv(f)
        elif filename.endswith(".pdf"):
            txs = parse_pdf(f)
        else:
            return jsonify({"error": "Unsupported file type"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    with SessionLocal() as db:
        for t in txs:
            db.add(Transaction(**t))
        db.commit()
        q = db.query(Transaction)
        rows = [{
            "id": tr.id,
            "date": tr.date.isoformat(),
            "description": tr.description,
            "amount": tr.amount,
            "mode": tr.mode,
            "category": tr.category
        } for tr in q.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(200)]
        df = pd.DataFrame([dict(date=r["date"], description=r["description"], amount=r["amount"], category=r["category"]) for r in rows])
        if df.empty: df = pd.DataFrame(columns=["date","description","amount","category"])
        income = df[df.amount.astype(float) > 0]["amount"].sum()
        expense = df[df.amount.astype(float) < 0]["amount"].sum()
        by_cat = df[df.amount.astype(float) < 0].groupby("category")["amount"].apply(lambda s: abs(s.sum())).to_dict() if not df.empty else {}

        # budgets
        b_map = {}
        for b in db.query(Budget).all():
            spent = sum(v for k,v in by_cat.items() if k == b.category)
            b_map[b.category] = {"limit": b.limit, "spent": spent}

        insights = compute_insights(df)

    return jsonify({
        "transactions": rows,
        "summary": {
            "income": float(income),
            "expense": float(abs(expense)),
            "net": float(income + expense)
        },
        "by_category": by_cat,
        "budgets": b_map,
        "insights": insights
    })

@app.route("/api/transactions")
def list_transactions():
    with SessionLocal() as db:
        q = db.query(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc()).limit(500)
        return jsonify([{
            "id": tr.id,
            "date": tr.date.isoformat(),
            "description": tr.description,
            "amount": tr.amount,
            "mode": tr.mode,
            "category": tr.category
        } for tr in q])

@app.route("/api/budgets", methods=["GET","POST"])
def budgets():
    with SessionLocal() as db:
        if request.method == "POST":
            data = request.get_json(force=True)
            cat, limit = data.get("category"), float(data.get("limit", 0))
            if not cat or limit <= 0:
                return jsonify({"error": "category and positive limit required"}), 400
            obj = db.query(Budget).filter_by(category=cat).first()
            if obj: obj.limit = limit
            else: db.add(Budget(category=cat, limit=limit))
            db.commit()
        items = db.query(Budget).all()
        return jsonify([{ "category": b.category, "limit": b.limit } for b in items])

@app.route("/api/bills", methods=["GET","POST","DELETE"])
def bills():
    with SessionLocal() as db:
        if request.method == "POST":
            data = request.get_json(force=True)
            name, due, amount = data.get("name"), dateparser.parse(data.get("due")).date(), float(data.get("amount", 0))
            db.add(Bill(name=name, due=due, amount=amount))
            db.commit()
        elif request.method == "DELETE":
            bid = int(request.args.get("id", "0"))
            obj = db.query(Bill).filter_by(id=bid).first()
            if obj: db.delete(obj); db.commit()
        items = db.query(Bill).order_by(Bill.due.asc()).all()
        return jsonify([{ "id": b.id, "name": b.name, "due": b.due.isoformat(), "amount": b.amount } for b in items])

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5000, debug=True)
