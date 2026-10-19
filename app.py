import os
from functools import wraps
from datetime import date
from decimal import Decimal, InvalidOperation

import pyodbc
from flask import Flask, render_template, request, redirect, url_for, flash, session

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-before-use")

SQL_SERVER = os.getenv("SQL_SERVER", r".\SQLEXPRESS")
SQL_DATABASE = os.getenv("SQL_DATABASE", "CashExpenseDB")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
SQL_USERNAME = os.getenv("SQL_USERNAME", "")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")
SQL_TRUSTED = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in ("yes", "true", "1")


def get_connection():
    parts = [f"DRIVER={{{SQL_DRIVER}}}", f"SERVER={SQL_SERVER}",
             f"DATABASE={SQL_DATABASE}", "Encrypt=no", "TrustServerCertificate=yes"]
    if SQL_TRUSTED:
        parts.append("Trusted_Connection=yes")
    else:
        parts.extend([f"UID={SQL_USERNAME}", f"PWD={SQL_PASSWORD}"])
    return pyodbc.connect(";".join(parts), timeout=5)


def init_db():
    # Existing installations are upgraded by creating the two additional tables.
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
        IF OBJECT_ID('dbo.Expenses', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.Expenses (
                ExpenseId INT IDENTITY(1,1) PRIMARY KEY,
                PersonName NVARCHAR(150) NOT NULL,
                ExpenseDate DATE NOT NULL,
                PurchaseFor NVARCHAR(250) NOT NULL,
                Remark NVARCHAR(1000) NULL,
                TotalAmount DECIMAL(18,2) NOT NULL CHECK (TotalAmount > 0),
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            );
        END;
        IF OBJECT_ID('dbo.ExpensePayments', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.ExpensePayments (
                PaymentId INT IDENTITY(1,1) PRIMARY KEY,
                ExpenseId INT NOT NULL,
                PaymentDate DATE NOT NULL,
                Amount DECIMAL(18,2) NOT NULL CHECK (Amount > 0),
                Remark NVARCHAR(500) NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                CONSTRAINT FK_ExpensePayments_Expenses FOREIGN KEY (ExpenseId)
                    REFERENCES dbo.Expenses(ExpenseId) ON DELETE CASCADE
            );
        END;
        IF OBJECT_ID('dbo.PersonAdvances', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.PersonAdvances (
                AdvanceId INT IDENTITY(1,1) PRIMARY KEY,
                PersonName NVARCHAR(150) NOT NULL,
                AdvanceDate DATE NOT NULL,
                Amount DECIMAL(18,2) NOT NULL CHECK (Amount > 0),
                Remark NVARCHAR(500) NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            );
        END;
        IF OBJECT_ID('dbo.ExpenseAdvanceAdjustments', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.ExpenseAdvanceAdjustments (
                AdjustmentId INT IDENTITY(1,1) PRIMARY KEY,
                AdvanceId INT NOT NULL,
                ExpenseId INT NOT NULL,
                AdjustmentDate DATE NOT NULL,
                Amount DECIMAL(18,2) NOT NULL CHECK (Amount > 0),
                Remark NVARCHAR(500) NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                CONSTRAINT FK_Adjustments_Advances FOREIGN KEY (AdvanceId)
                    REFERENCES dbo.PersonAdvances(AdvanceId),
                CONSTRAINT FK_Adjustments_Expenses FOREIGN KEY (ExpenseId)
                    REFERENCES dbo.Expenses(ExpenseId) ON DELETE CASCADE
            );
        END;
        IF COL_LENGTH('dbo.ExpensePayments', 'PaymentMode') IS NULL
            ALTER TABLE dbo.ExpensePayments ADD PaymentMode NVARCHAR(40) NOT NULL
                CONSTRAINT DF_ExpensePayments_PaymentMode DEFAULT 'Direct Cash';
        IF OBJECT_ID('dbo.DirectCashReceipts', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.DirectCashReceipts (
                ReceiptId INT IDENTITY(1,1) PRIMARY KEY,
                SourceName NVARCHAR(150) NOT NULL,
                ReceiptDate DATE NOT NULL,
                Amount DECIMAL(18,2) NOT NULL CHECK (Amount > 0),
                Remark NVARCHAR(500) NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
            );
        END;
        IF OBJECT_ID('dbo.ExpenseDirectCashAdjustments', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.ExpenseDirectCashAdjustments (
                AdjustmentId INT IDENTITY(1,1) PRIMARY KEY,
                ReceiptId INT NOT NULL,
                ExpenseId INT NOT NULL,
                AdjustmentDate DATE NOT NULL,
                Amount DECIMAL(18,2) NOT NULL CHECK (Amount > 0),
                Remark NVARCHAR(500) NULL,
                CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                CONSTRAINT FK_DirectCashAdjustments_Receipts FOREIGN KEY (ReceiptId)
                    REFERENCES dbo.DirectCashReceipts(ReceiptId),
                CONSTRAINT FK_DirectCashAdjustments_Expenses FOREIGN KEY (ExpenseId)
                    REFERENCES dbo.Expenses(ExpenseId) ON DELETE CASCADE
            );
        END;
        """)
        conn.commit()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def get_expense(expense_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.ExpenseId,e.PersonName,e.ExpenseDate,e.PurchaseFor,e.Remark,e.TotalAmount,
                   COALESCE(p.PaidAmount,0),COALESCE(a.AdjustedAmount,0),COALESCE(d.DirectAdjustedAmount,0)
            FROM dbo.Expenses e
            LEFT JOIN (SELECT ExpenseId,SUM(Amount) PaidAmount FROM dbo.ExpensePayments GROUP BY ExpenseId) p
              ON p.ExpenseId=e.ExpenseId
            LEFT JOIN (SELECT ExpenseId,SUM(Amount) AdjustedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY ExpenseId) a
              ON a.ExpenseId=e.ExpenseId
            LEFT JOIN (SELECT ExpenseId,SUM(Amount) DirectAdjustedAmount FROM dbo.ExpenseDirectCashAdjustments GROUP BY ExpenseId) d
              ON d.ExpenseId=e.ExpenseId
            WHERE e.ExpenseId=?
        """, expense_id)
        row = cur.fetchone()
        if not row:
            return None
        amount, paid, adjusted, direct_adjusted = Decimal(row[5]), Decimal(row[6]), Decimal(row[7]), Decimal(row[8])
        return {"id":row[0],"person":row[1],"date":row[2],"purchase":row[3],"remark":row[4] or "",
                "amount":amount,"paid":paid,"adjusted":adjusted,"direct_adjusted":direct_adjusted,
                "balance":max(Decimal("0"),amount-paid-adjusted-direct_adjusted)}


@app.route("/login", methods=["GET", "POST"])
def login():
    expected_user = os.getenv("APP_USERNAME", "admin")
    expected_password = os.getenv("APP_PASSWORD", "")
    if request.method == "POST":
        if (request.form.get("username", "") == expected_user and expected_password
                and request.form.get("password", "") == expected_password):
            session["logged_in"] = True
            session["username"] = expected_user
            return redirect(request.args.get("next") or url_for("index"))
        flash("Login failed. Check username/password and ensure APP_PASSWORD is configured.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    search = f"%{q}%"
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.ExpenseId,e.PersonName,e.ExpenseDate,e.PurchaseFor,e.Remark,e.TotalAmount,
                   COALESCE(p.PaidAmount,0),COALESCE(a.AdjustedAmount,0),COALESCE(d.DirectAdjustedAmount,0)
            FROM dbo.Expenses e
            LEFT JOIN (SELECT ExpenseId,SUM(Amount) PaidAmount FROM dbo.ExpensePayments GROUP BY ExpenseId) p
              ON p.ExpenseId=e.ExpenseId
            LEFT JOIN (SELECT ExpenseId,SUM(Amount) AdjustedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY ExpenseId) a
              ON a.ExpenseId=e.ExpenseId
            LEFT JOIN (SELECT ExpenseId,SUM(Amount) DirectAdjustedAmount FROM dbo.ExpenseDirectCashAdjustments GROUP BY ExpenseId) d
              ON d.ExpenseId=e.ExpenseId
            WHERE (?='' OR e.PersonName LIKE ? OR e.PurchaseFor LIKE ? OR COALESCE(e.Remark,'') LIKE ?)
            ORDER BY e.ExpenseDate DESC,e.ExpenseId DESC
        """, q, search, search, search)
        rows = cur.fetchall()
        records=[]
        for r in rows:
            amount, paid, adjusted, direct_adjusted = Decimal(r[5]), Decimal(r[6]), Decimal(r[7]), Decimal(r[8])
            balance=max(Decimal("0"),amount-paid-adjusted-direct_adjusted)
            status="Paid" if balance == 0 else ("Partially Paid" if paid+adjusted+direct_adjusted > 0 else "Unpaid")
            records.append({"id":r[0],"person":r[1],"date":r[2],"purchase":r[3],"remark":r[4] or "",
                            "amount":amount,"paid":paid,"adjusted":adjusted,"direct_adjusted":direct_adjusted,"balance":balance,"status":status,"history":[]})
        history_by_expense={r["id"]:r for r in records}
        cur.execute("""
          SELECT ExpenseId,PaymentDate,Amount,PaymentMode,Remark FROM dbo.ExpensePayments
          ORDER BY PaymentDate,PaymentId
        """)
        for h in cur.fetchall():
            if h[0] in history_by_expense:
                history_by_expense[h[0]]["history"].append({"date":h[1],"amount":Decimal(h[2]),"mode":h[3] or "Direct Cash","source":"Purchase-specific","remark":h[4] or ""})
        cur.execute("""
          SELECT a.ExpenseId,a.AdjustmentDate,a.Amount,a.Remark,p.PersonName
          FROM dbo.ExpenseAdvanceAdjustments a JOIN dbo.PersonAdvances p ON p.AdvanceId=a.AdvanceId
          ORDER BY a.AdjustmentDate,a.AdjustmentId
        """)
        for h in cur.fetchall():
            if h[0] in history_by_expense:
                history_by_expense[h[0]]["history"].append({"date":h[1],"amount":Decimal(h[2]),"mode":"Petty Cash Fund","source":h[4] or "Petty Cash","remark":h[3] or ""})
        cur.execute("""
          SELECT a.ExpenseId,a.AdjustmentDate,a.Amount,a.Remark,r.SourceName
          FROM dbo.ExpenseDirectCashAdjustments a JOIN dbo.DirectCashReceipts r ON r.ReceiptId=a.ReceiptId
          ORDER BY a.AdjustmentDate,a.AdjustmentId
        """)
        for h in cur.fetchall():
            if h[0] in history_by_expense:
                history_by_expense[h[0]]["history"].append({"date":h[1],"amount":Decimal(h[2]),"mode":"Direct Cash Fund","source":h[4] or "Direct Cash Fund","remark":h[3] or ""})
        for rec in records:
            rec["history"].sort(key=lambda x: str(x["date"]))
        cur.execute("""
          SELECT COALESCE(SUM(e.TotalAmount),0),
                 COALESCE((SELECT SUM(Amount) FROM dbo.ExpensePayments),0),
                 COALESCE((SELECT SUM(Amount) FROM dbo.ExpenseAdvanceAdjustments),0)
          FROM dbo.Expenses e
        """)
        summary=cur.fetchone()
        total_expense,total_paid,total_adjusted=Decimal(summary[0]),Decimal(summary[1]),Decimal(summary[2])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.ExpenseDirectCashAdjustments")
        total_direct_fund_used=Decimal(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.ExpensePayments")
        total_direct_paid=Decimal(cur.fetchone()[0])
        cur.execute("""
          SELECT a.AdvanceId,a.PersonName,a.AdvanceDate,a.Amount,a.Remark,
                 COALESCE(x.UsedAmount,0)
          FROM dbo.PersonAdvances a
          LEFT JOIN (SELECT AdvanceId,SUM(Amount) UsedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY AdvanceId) x
            ON x.AdvanceId=a.AdvanceId
          ORDER BY a.AdvanceDate DESC,a.AdvanceId DESC
        """)
        advance_rows=cur.fetchall()
        advances=[]
        for a in advance_rows:
            amount, used=Decimal(a[3]),Decimal(a[5])
            advances.append({"id":a[0],"person":a[1],"date":a[2],"amount":amount,
                             "used":used,"balance":max(Decimal("0"),amount-used),"remark":a[4] or ""})
        # Dashboard total: all separately tracked person-wise petty-cash balances combined.
        cur.execute("""
          SELECT COALESCE((SELECT SUM(Amount) FROM dbo.PersonAdvances),0),
                 COALESCE((SELECT SUM(Amount) FROM dbo.ExpenseAdvanceAdjustments),0)
        """)
        petty_cash_row = cur.fetchone()
        petty_cash_received = Decimal(petty_cash_row[0])
        petty_cash_used = Decimal(petty_cash_row[1])
        petty_cash_available = max(Decimal("0"), petty_cash_received - petty_cash_used)
        cur.execute("SELECT COALESCE(SUM(Amount),0), COALESCE((SELECT SUM(Amount) FROM dbo.ExpenseDirectCashAdjustments),0) FROM dbo.DirectCashReceipts")
        direct_fund_summary=cur.fetchone()
        direct_cash_received=Decimal(direct_fund_summary[0])
        direct_cash_fund_used=Decimal(direct_fund_summary[1])
        direct_cash_available=max(Decimal("0"),direct_cash_received-direct_cash_fund_used)
        cur.execute("""
          SELECT r.ReceiptId,r.SourceName,r.ReceiptDate,r.Amount,r.Remark,COALESCE(x.UsedAmount,0)
          FROM dbo.DirectCashReceipts r
          LEFT JOIN (SELECT ReceiptId,SUM(Amount) UsedAmount FROM dbo.ExpenseDirectCashAdjustments GROUP BY ReceiptId) x
            ON x.ReceiptId=r.ReceiptId
          ORDER BY r.ReceiptDate DESC,r.ReceiptId DESC
        """)
        direct_cash_rows=cur.fetchall()
        direct_cash_receipts=[{"id":r[0],"source":r[1],"date":r[2],"amount":Decimal(r[3]),"remark":r[4] or "",
                               "used":Decimal(r[5]),"balance":max(Decimal("0"),Decimal(r[3])-Decimal(r[5]))} for r in direct_cash_rows]
        cur.execute("""
          SELECT a.PersonName,
                 SUM(a.Amount-COALESCE(x.UsedAmount,0)) AS AvailableAmount
          FROM dbo.PersonAdvances a
          LEFT JOIN (SELECT AdvanceId,SUM(Amount) UsedAmount
                     FROM dbo.ExpenseAdvanceAdjustments GROUP BY AdvanceId) x
            ON x.AdvanceId=a.AdvanceId
          GROUP BY a.PersonName
          HAVING SUM(a.Amount-COALESCE(x.UsedAmount,0)) > 0
          ORDER BY a.PersonName
        """)
        available_advances=[{"person":r[0],"balance":Decimal(r[1])} for r in cur.fetchall()]
    return render_template("index.html",records=records,q=q,today=date.today().isoformat(),
        total_expense=total_expense,total_paid=total_paid,total_adjusted=total_adjusted,
        total_due=max(Decimal("0"),total_expense-total_paid-total_adjusted-total_direct_fund_used),
        advances=advances,available_advances=available_advances,
        petty_cash_received=petty_cash_received,petty_cash_available=petty_cash_available,
        direct_cash_received=direct_cash_received,direct_cash_fund_used=direct_cash_fund_used,
        direct_cash_available=direct_cash_available,direct_cash_receipts=direct_cash_receipts,
        total_direct_paid=total_direct_paid,total_direct_fund_used=total_direct_fund_used)


@app.route("/advance/add", methods=["POST"])
@login_required
def add_advance():
    person=request.form.get("person","").strip()
    advance_date=request.form.get("date") or date.today().isoformat()
    remark=request.form.get("remark","").strip()
    try:
        amount=Decimal(request.form.get("amount","0"))
        if amount <= 0:
            raise ValueError
    except (InvalidOperation,ValueError):
        flash("Enter a petty-cash amount greater than zero.","error")
        return redirect(url_for("index"))
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute("INSERT INTO dbo.PersonAdvances(PersonName,AdvanceDate,Amount,Remark) VALUES(?,?,?,?)",
                    person,advance_date,amount,remark or None)
        conn.commit()
    flash(f"Petty cash received: ₹{amount:,.2f} recorded for {person or 'Unassigned source'}.","success")
    return redirect(url_for("index"))


@app.route("/direct-cash/receive", methods=["POST"])
@login_required
def receive_direct_cash():
    source=request.form.get("source","").strip() or "Direct Cash Fund"
    receipt_date=request.form.get("date") or date.today().isoformat()
    remark=request.form.get("remark","").strip()
    try:
        amount=Decimal(request.form.get("amount","0"))
        if amount <= 0: raise ValueError
    except (InvalidOperation,ValueError):
        flash("Enter a direct-cash fund receipt greater than zero.","error")
        return redirect(url_for("index"))
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute("INSERT INTO dbo.DirectCashReceipts(SourceName,ReceiptDate,Amount,Remark) VALUES(?,?,?,?)",
                    source,receipt_date,amount,remark or None)
        conn.commit()
    flash(f"Direct cash fund received: ₹{amount:,.2f}.","success")
    return redirect(url_for("index"))


@app.route("/expense/<int:expense_id>/adjust-direct-fund", methods=["POST"])
@login_required
def adjust_direct_cash_fund(expense_id):
    try:
        amount=Decimal(request.form.get("amount","0"))
        adjustment_date=request.form.get("date") or date.today().isoformat()
        remark=request.form.get("remark","").strip()
        if amount <= 0: raise ValueError
    except (InvalidOperation,ValueError):
        flash("Enter a valid direct-cash fund adjustment amount greater than zero.","error")
        return redirect(url_for("index"))
    e=get_expense(expense_id)
    if not e:
        flash("Expense not found.","error"); return redirect(url_for("index"))
    if amount > e["balance"]:
        flash(f"Adjustment cannot exceed expense balance ₹{e['balance']:,.2f}.","error")
        return redirect(url_for("index"))
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute("""
          SELECT r.ReceiptId,r.Amount,COALESCE(x.UsedAmount,0)
          FROM dbo.DirectCashReceipts r WITH (UPDLOCK,HOLDLOCK)
          LEFT JOIN (SELECT ReceiptId,SUM(Amount) UsedAmount FROM dbo.ExpenseDirectCashAdjustments GROUP BY ReceiptId) x
            ON x.ReceiptId=r.ReceiptId
          ORDER BY r.ReceiptDate,r.ReceiptId
        """)
        receipt_rows=cur.fetchall()
        available=sum((Decimal(r[1])-Decimal(r[2]) for r in receipt_rows),Decimal("0"))
        if amount > available:
            flash(f"Direct cash fund has only ₹{available:,.2f} available.","error")
            return redirect(url_for("index"))
        remaining=amount
        for receipt_id, receipt_amount, used_amount in receipt_rows:
            free=Decimal(receipt_amount)-Decimal(used_amount)
            take=min(free,remaining)
            if take > 0:
                cur.execute("INSERT INTO dbo.ExpenseDirectCashAdjustments(ReceiptId,ExpenseId,AdjustmentDate,Amount,Remark) VALUES(?,?,?,?,?)",
                            receipt_id,expense_id,adjustment_date,take,remark or "Direct cash fund adjusted")
                remaining-=take
            if remaining <= 0: break
        conn.commit()
    flash(f"Direct cash fund of ₹{amount:,.2f} adjusted against this expense.","success")
    return redirect(url_for("index"))


@app.route("/expense/add", methods=["POST"])
@login_required
def add_expense():
    person=request.form.get("person","").strip()
    expense_date=request.form.get("date","")
    purchase=request.form.get("purchase","").strip()
    remark=request.form.get("remark","").strip()
    try:
        amount=Decimal(request.form.get("amount","0"))
        initial_paid=Decimal(request.form.get("initial_paid","0") or "0")
        if not person or not expense_date or not purchase or amount <= 0 or initial_paid < 0 or initial_paid > amount:
            raise ValueError
    except (InvalidOperation,ValueError):
        flash("Enter valid details. Paid now must be between ₹0 and the total amount.","error")
        return redirect(url_for("index"))
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute("""INSERT INTO dbo.Expenses(PersonName,ExpenseDate,PurchaseFor,Remark,TotalAmount)
                       OUTPUT INSERTED.ExpenseId VALUES(?,?,?,?,?)""",
                    person,expense_date,purchase,remark or None,amount)
        expense_id=cur.fetchone()[0]
        if initial_paid > 0:
            cur.execute("INSERT INTO dbo.ExpensePayments(ExpenseId,PaymentDate,Amount,Remark,PaymentMode) VALUES(?,?,?,?,?)",
                        expense_id,date.today(),initial_paid,"Initial payment","Direct Cash")
        conn.commit()
    flash("Expense saved.","success")
    return redirect(url_for("index"))


@app.route("/expense/<int:expense_id>/payment", methods=["POST"])
@login_required
def add_payment(expense_id):
    try:
        amount=Decimal(request.form.get("amount","0"))
        payment_date=request.form.get("date") or date.today().isoformat()
        remark=request.form.get("remark","").strip()
        payment_mode="Direct Cash"
        if amount <= 0: raise ValueError
    except (InvalidOperation,ValueError):
        flash("Enter a valid payment amount greater than zero.","error")
        return redirect(url_for("index"))
    e=get_expense(expense_id)
    if not e:
        flash("Expense not found.","error"); return redirect(url_for("index"))
    if amount > e["balance"]:
        flash(f"Payment exceeds outstanding balance of ₹{e['balance']:,.2f}.","error")
        return redirect(url_for("index"))
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute("INSERT INTO dbo.ExpensePayments(ExpenseId,PaymentDate,Amount,Remark,PaymentMode) VALUES(?,?,?,?,?)",
                    expense_id,payment_date,amount,remark or None,payment_mode)
        conn.commit()
    flash(f"{payment_mode} payment recorded.","success")
    return redirect(url_for("index"))


@app.route("/expense/<int:expense_id>/adjust-advance", methods=["POST"])
@login_required
def adjust_advance(expense_id):
    person=request.form.get("advance_person", "").strip()
    try:
        amount=Decimal(request.form.get("amount","0"))
        adjustment_date=request.form.get("date") or date.today().isoformat()
        remark=request.form.get("remark","").strip()
        if amount <= 0 or not person: raise ValueError
    except (InvalidOperation,ValueError):
        flash("Select whose petty cash to deduct and enter a valid amount greater than zero.","error")
        return redirect(url_for("index"))
    e=get_expense(expense_id)
    if not e:
        flash("Expense not found.","error"); return redirect(url_for("index"))
    if amount > e["balance"]:
        flash(f"Adjustment cannot exceed expense balance ₹{e['balance']:,.2f}.","error")
        return redirect(url_for("index"))
    with get_connection() as conn:
        cur=conn.cursor()
        # Deduct only from the selected person's advances, spreading across that person's receipts if needed.
        cur.execute("""
          SELECT a.AdvanceId,a.Amount,COALESCE(x.UsedAmount,0)
          FROM dbo.PersonAdvances a WITH (UPDLOCK,HOLDLOCK)
          LEFT JOIN (SELECT AdvanceId,SUM(Amount) UsedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY AdvanceId) x
            ON x.AdvanceId=a.AdvanceId
          WHERE a.PersonName=?
          ORDER BY a.AdvanceDate,a.AdvanceId
        """, person)
        advance_rows=cur.fetchall()
        available=sum((max(Decimal("0"),Decimal(r[1])-Decimal(r[2])) for r in advance_rows),Decimal("0"))
        if amount > available:
            flash(f"{person} has only ₹{available:,.2f} petty cash available. Select another person or enter a smaller amount.","error")
            return redirect(url_for("index"))
        remaining=amount
        for advance_id, adv_amount, used_amount in advance_rows:
            free=max(Decimal("0"),Decimal(adv_amount)-Decimal(used_amount))
            take=min(free,remaining)
            if take > 0:
                cur.execute("""INSERT INTO dbo.ExpenseAdvanceAdjustments(AdvanceId,ExpenseId,AdjustmentDate,Amount,Remark)
                               VALUES(?,?,?,?,?)""",advance_id,expense_id,adjustment_date,take,remark or f"Petty cash from {person}")
                remaining-=take
            if remaining <= 0: break
        conn.commit()
    flash(f"₹{amount:,.2f} deducted from {person}'s petty cash for this expense.","success")
    return redirect(url_for("index"))


@app.route("/expense/<int:expense_id>/delete", methods=["POST"])
@login_required
def delete_expense(expense_id):
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute("DELETE FROM dbo.Expenses WHERE ExpenseId=?",expense_id)
        conn.commit()
    flash("Expense deleted. Any advance adjustment on it is released back to the person's available advance.","success")
    return redirect(url_for("index"))


@app.route("/reports")
@login_required
def reports():
    start_date = request.args.get("start", "").strip()
    end_date = request.args.get("end", "").strip()
    status_filter = request.args.get("status", "All").strip()
    if status_filter not in ("All", "Paid", "Partially Paid", "Unpaid"):
        status_filter = "All"

    where = []
    params = []
    if start_date:
        where.append("e.ExpenseDate >= ?")
        params.append(start_date)
    if end_date:
        where.append("e.ExpenseDate <= ?")
        params.append(end_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
          SELECT e.ExpenseId,e.ExpenseDate,e.PersonName,e.PurchaseFor,e.Remark,e.TotalAmount,
                 COALESCE(p.PaidAmount,0),COALESCE(a.UsedAmount,0),COALESCE(d.UsedAmount,0)
          FROM dbo.Expenses e
          LEFT JOIN (SELECT ExpenseId,SUM(Amount) PaidAmount FROM dbo.ExpensePayments GROUP BY ExpenseId) p ON p.ExpenseId=e.ExpenseId
          LEFT JOIN (SELECT ExpenseId,SUM(Amount) UsedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY ExpenseId) a ON a.ExpenseId=e.ExpenseId
          LEFT JOIN (SELECT ExpenseId,SUM(Amount) UsedAmount FROM dbo.ExpenseDirectCashAdjustments GROUP BY ExpenseId) d ON d.ExpenseId=e.ExpenseId
          {where_sql}
          ORDER BY e.ExpenseDate DESC,e.ExpenseId DESC
        """, *params)
        expense_rows = cur.fetchall()

        cur.execute(f"""
          SELECT e.ExpenseId,p.PaymentDate,p.Amount,'Purchase-specific direct cash' AS SourceName
          FROM dbo.ExpensePayments p JOIN dbo.Expenses e ON e.ExpenseId=p.ExpenseId
          {where_sql}
          UNION ALL
          SELECT e.ExpenseId,a.AdjustmentDate,a.Amount,pa.PersonName
          FROM dbo.ExpenseAdvanceAdjustments a
          JOIN dbo.Expenses e ON e.ExpenseId=a.ExpenseId
          JOIN dbo.PersonAdvances pa ON pa.AdvanceId=a.AdvanceId
          {where_sql}
          UNION ALL
          SELECT e.ExpenseId,a.AdjustmentDate,a.Amount,COALESCE(r.SourceName,'Direct Cash Fund')
          FROM dbo.ExpenseDirectCashAdjustments a
          JOIN dbo.Expenses e ON e.ExpenseId=a.ExpenseId
          JOIN dbo.DirectCashReceipts r ON r.ReceiptId=a.ReceiptId
          {where_sql}
          ORDER BY ExpenseId
        """, *(params + params + params))
        transactions = cur.fetchall()

        by_expense = {}
        account_totals = {}
        for expense_id, tx_date, tx_amount, source_name in transactions:
            by_expense.setdefault(expense_id, []).append((tx_date, Decimal(tx_amount), source_name))
            account_totals[source_name] = account_totals.get(source_name, Decimal("0")) + Decimal(tx_amount)

        expenses = []
        for r in expense_rows:
            expense_id = r[0]
            total, paid, petty, direct_fund = Decimal(r[5]), Decimal(r[6]), Decimal(r[7]), Decimal(r[8])
            settled = paid + petty + direct_fund
            due = max(Decimal("0"), total - settled)
            status = "Paid" if due == 0 else ("Partially Paid" if settled > 0 else "Unpaid")
            txs = sorted(by_expense.get(expense_id, []), key=lambda x: (str(x[0]), x[2]))
            paid_date = "—"
            if due == 0 and txs:
                running = Decimal("0")
                for tx_date, tx_amount, _source in txs:
                    running += tx_amount
                    if running >= total:
                        paid_date = tx_date
                        break
            source_names = sorted({t[2] for t in txs})
            item = {"id":expense_id,"date":r[1],"person":r[2],"purchase":r[3],"remark":r[4] or "",
                    "total":total,"cash_paid":paid,"petty":petty,"direct_fund":direct_fund,"settled":settled,
                    "due":due,"status":status,"paid_date":paid_date,"sources":", ".join(source_names) or "—"}
            if status_filter == "All" or status == status_filter:
                expenses.append(item)

        cur.execute(f"""
          SELECT pa.PersonName,pa.AdvanceDate,pa.Amount,pa.Remark,
                 COALESCE(u.UsedAmount,0),pa.AdvanceId
          FROM dbo.PersonAdvances pa
          LEFT JOIN (SELECT AdvanceId,SUM(Amount) UsedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY AdvanceId) u
            ON u.AdvanceId=pa.AdvanceId
          ORDER BY pa.PersonName,pa.AdvanceDate,pa.AdvanceId
        """)
        advance_rows = cur.fetchall()
        advances = [{"person":r[0],"date":r[1],"amount":Decimal(r[2]),"remark":r[3] or "",
                     "used":Decimal(r[4]),"balance":max(Decimal("0"),Decimal(r[2])-Decimal(r[4]))} for r in advance_rows]

        cur.execute(f"""
          SELECT e.PersonName,COUNT(*) ExpenseCount,SUM(e.TotalAmount) TotalAmount
          FROM dbo.Expenses e
          {where_sql}
          GROUP BY e.PersonName
          ORDER BY SUM(e.TotalAmount) DESC,COUNT(*) DESC,e.PersonName
        """, *params)
        person_rows = cur.fetchall()
        people_rank = [{"person":r[0],"count":int(r[1]),"total":Decimal(r[2])} for r in person_rows]

        cur.execute("""
          SELECT pa.PersonName,SUM(a.Amount) UsedAmount
          FROM dbo.ExpenseAdvanceAdjustments a
          JOIN dbo.PersonAdvances pa ON pa.AdvanceId=a.AdvanceId
          GROUP BY pa.PersonName
          ORDER BY SUM(a.Amount) DESC,pa.PersonName
        """)
        petty_account_rows = cur.fetchall()
        petty_accounts = [{"name":r[0],"used":Decimal(r[1])} for r in petty_account_rows]

        cur.execute("""
          SELECT COALESCE(SUM(TotalAmount),0) FROM dbo.Expenses
          """)
        overall_expense_total = Decimal(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.ExpensePayments")
        overall_purchase_cash = Decimal(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.ExpenseAdvanceAdjustments")
        overall_petty_used = Decimal(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.ExpenseDirectCashAdjustments")
        overall_direct_fund_used = Decimal(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.PersonAdvances")
        overall_advances = Decimal(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(Amount),0) FROM dbo.DirectCashReceipts")
        overall_direct_received = Decimal(cur.fetchone()[0])

    # The source ranking separates person-wise petty cash from the shared direct fund and
    # purchase-specific cash payments, so users can see both account usage and payment type.
    payment_sources = [{"name":name,"used":amount} for name,amount in sorted(account_totals.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
    advance_by_person = {}
    for a in advances:
        x = advance_by_person.setdefault(a["person"], {"person":a["person"],"given":Decimal("0"),"used":Decimal("0"),"balance":Decimal("0")})
        x["given"] += a["amount"]
        x["used"] += a["used"]
        x["balance"] += a["balance"]
    advance_summary = sorted(advance_by_person.values(), key=lambda x: (-x["given"], x["person"].lower()))
    return render_template("reports.html", expenses=expenses, advances=advances, advance_summary=advance_summary,
        people_rank=people_rank, petty_accounts=petty_accounts, payment_sources=payment_sources,
        start_date=start_date, end_date=end_date, status_filter=status_filter,
        total_expenses=sum((e["total"] for e in expenses), Decimal("0")),
        total_due=sum((e["due"] for e in expenses), Decimal("0")),
        total_paid=sum((e["settled"] for e in expenses), Decimal("0")),
        overall_expense_total=overall_expense_total, overall_purchase_cash=overall_purchase_cash,
        overall_petty_used=overall_petty_used, overall_direct_fund_used=overall_direct_fund_used,
        overall_advances=overall_advances, overall_direct_received=overall_direct_received)


@app.route("/reports.csv")
@login_required
def reports_csv():
    import csv
    from io import StringIO
    from flask import make_response
    output=StringIO()
    writer=csv.writer(output)
    writer.writerow(["Expense Date","Person Who Made Expense","Purchase For","Remark","Total Amount",
                     "Purchase-specific Cash Paid","Petty Cash Used","Direct Cash Fund Used","Paid From Account(s)",
                     "Outstanding Balance","Status","Paid Date"])
    start_date=request.args.get("start","").strip()
    end_date=request.args.get("end","").strip()
    status_filter=request.args.get("status","All").strip()
    where=[]; params=[]
    if start_date:
        where.append("e.ExpenseDate >= ?"); params.append(start_date)
    if end_date:
        where.append("e.ExpenseDate <= ?"); params.append(end_date)
    where_sql=("WHERE "+" AND ".join(where)) if where else ""
    with get_connection() as conn:
        cur=conn.cursor()
        cur.execute(f"""
          SELECT e.ExpenseId,e.ExpenseDate,e.PersonName,e.PurchaseFor,e.Remark,e.TotalAmount,
                 COALESCE(p.PaidAmount,0),COALESCE(a.UsedAmount,0),COALESCE(d.UsedAmount,0)
          FROM dbo.Expenses e
          LEFT JOIN (SELECT ExpenseId,SUM(Amount) PaidAmount FROM dbo.ExpensePayments GROUP BY ExpenseId) p ON p.ExpenseId=e.ExpenseId
          LEFT JOIN (SELECT ExpenseId,SUM(Amount) UsedAmount FROM dbo.ExpenseAdvanceAdjustments GROUP BY ExpenseId) a ON a.ExpenseId=e.ExpenseId
          LEFT JOIN (SELECT ExpenseId,SUM(Amount) UsedAmount FROM dbo.ExpenseDirectCashAdjustments GROUP BY ExpenseId) d ON d.ExpenseId=e.ExpenseId
          {where_sql}
          ORDER BY e.ExpenseDate DESC,e.ExpenseId DESC
        """,*params)
        rows=cur.fetchall()
        cur.execute(f"""
          SELECT e.ExpenseId,p.PaymentDate,p.Amount,'Purchase-specific direct cash' SourceName
          FROM dbo.ExpensePayments p JOIN dbo.Expenses e ON e.ExpenseId=p.ExpenseId {where_sql}
          UNION ALL
          SELECT e.ExpenseId,a.AdjustmentDate,a.Amount,'Petty cash - '+pa.PersonName
          FROM dbo.ExpenseAdvanceAdjustments a JOIN dbo.Expenses e ON e.ExpenseId=a.ExpenseId
          JOIN dbo.PersonAdvances pa ON pa.AdvanceId=a.AdvanceId {where_sql}
          UNION ALL
          SELECT e.ExpenseId,a.AdjustmentDate,a.Amount,'Direct cash fund - '+COALESCE(r.SourceName,'Cash')
          FROM dbo.ExpenseDirectCashAdjustments a JOIN dbo.Expenses e ON e.ExpenseId=a.ExpenseId
          JOIN dbo.DirectCashReceipts r ON r.ReceiptId=a.ReceiptId {where_sql}
          ORDER BY ExpenseId
        """,*(params+params+params))
        tx_by_expense={}
        for eid,tx_date,tx_amount,source in cur.fetchall():
            tx_by_expense.setdefault(eid,[]).append((tx_date,Decimal(tx_amount),source))
        for r in rows:
            eid=r[0]; total=Decimal(r[5]); cash=Decimal(r[6]); petty=Decimal(r[7]); direct=Decimal(r[8])
            settled=cash+petty+direct; due=max(Decimal("0"),total-settled)
            status="Paid" if due==0 else ("Partially Paid" if settled>0 else "Unpaid")
            if status_filter in ("Paid","Partially Paid","Unpaid") and status!=status_filter:
                continue
            txs=sorted(tx_by_expense.get(eid,[]),key=lambda x:(str(x[0]),x[2]))
            paid_date=""
            if due==0:
                running=Decimal("0")
                for tx_date,tx_amount,_source in txs:
                    running+=tx_amount
                    if running>=total:
                        paid_date=str(tx_date); break
            sources=", ".join(sorted({t[2] for t in txs}))
            writer.writerow([r[1],r[2],r[3],r[4] or "",f"{total:.2f}",f"{cash:.2f}",f"{petty:.2f}",
                             f"{direct:.2f}",sources,f"{due:.2f}",status,paid_date])
    response=make_response("\ufeff"+output.getvalue())
    response.headers["Content-Type"]="text/csv; charset=utf-8"
    response.headers["Content-Disposition"]="attachment; filename=cash-expense-report.csv"
    return response


@app.errorhandler(pyodbc.Error)
def database_error(error):
    app.logger.exception("Database error")
    return render_template("error.html",message="Could not connect to SQL Server or execute the database operation. Check README.txt and the terminal error."),500


if __name__ == "__main__":
    init_db()
    if not os.getenv("APP_PASSWORD"):
        print("WARNING: Set APP_PASSWORD before use. Login is disabled until configured.")
    print("Cash Expense Management is starting on http://127.0.0.1:5002")
    app.run(host="0.0.0.0",port=5002,debug=False)
