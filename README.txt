CASH EXPENSE MANAGEMENT - FLASK + SQL SERVER
=============================================

This project runs on port 5002 and uses SQL Server Express as a shared database.
The web interface is opened in a browser. Other PCs can connect to the server PC
when Windows Firewall and SQL Server networking are configured.

1. REQUIREMENTS
---------------
- Windows 10/11
- Python 3.10 or newer
- Microsoft SQL Server Express (or another SQL Server instance)
- Microsoft ODBC Driver 18 for SQL Server
- Python packages from requirements.txt

2. CREATE DATABASE
------------------
Open SQL Server Management Studio (SSMS), connect to your SQL Server instance,
and run:

    CREATE DATABASE CashExpenseDB;
    GO

The app creates missing tables automatically at startup. For an existing installation, keep the same CashExpenseDB database and data. On next startup, the app adds the PaymentMode column to ExpensePayments (existing payments default to Direct Cash) and creates DirectCashReceipts and ExpenseDirectCashAdjustments if missing. It does not drop or recreate existing tables.

MIGRATION SAFETY
----------------
1. Stop the running app with Ctrl+C.
2. Back up CashExpenseDB in SSMS (Tasks > Back Up) before upgrading.
3. Copy the new app.py and templates folder into your existing C:\CashExpenseServer folder, keeping your current database and environment settings. Alternatively, extract this ZIP to a new folder and use the same SQL_SERVER / SQL_DATABASE settings.
4. Start `python app.py`. The startup initialization adds only the new column/tables when absent.
5. Verify the dashboard, petty cash balance, direct cash fund balance, and old expense history before entering new records.

3. INSTALL PYTHON PACKAGES
--------------------------
Open Command Prompt in the extracted project folder:

    py -m venv .venv
    .venv\Scripts\activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt

If pyodbc installation fails, first install the Microsoft ODBC Driver 18 for
SQL Server, then retry `pip install pyodbc`. Use a Python version supported by
the installed pyodbc wheel.

4. CONFIGURE SQL SERVER CONNECTION
---------------------------------
Defaults assume a local SQL Express instance named .\SQLEXPRESS and Windows
Integrated Authentication. If your instance name is different, set SQL_SERVER.

In the same Command Prompt, before starting the app, set a real app password
and secret key:

    set APP_USERNAME=admin
    set APP_PASSWORD=Choose-A-Strong-Password
    set FLASK_SECRET_KEY=replace-this-with-a-long-random-secret
    set SQL_SERVER=.\SQLEXPRESS
    set SQL_DATABASE=CashExpenseDB
    set SQL_DRIVER=ODBC Driver 18 for SQL Server
    set SQL_TRUSTED_CONNECTION=yes

Then run:

    python app.py

Open this URL on the server PC:
    http://127.0.0.1:5002

IMPORTANT: APP_PASSWORD must be set; login is intentionally disabled when it is
empty. For a permanent setup, use Windows System Environment Variables or a
protected launch script and keep credentials private.

SQL AUTHENTICATION OPTION
-------------------------
If using a SQL Server login instead of Windows Authentication:

    set SQL_TRUSTED_CONNECTION=no
    set SQL_USERNAME=your_sql_login
    set SQL_PASSWORD=your_sql_password

Ensure that login has permission to create/use tables in CashExpenseDB.

5. USE FROM OTHER PCS ON THE SAME NETWORK
-----------------------------------------
A. On the server PC, find its LAN IPv4 address by running `ipconfig`.
B. Configure SQL Server TCP/IP and a fixed port in SQL Server Configuration
   Manager if necessary, and enable remote connections.
C. Ensure SQL Server firewall access is limited to your trusted local network.
D. On the server PC, allow inbound TCP port 5002 in Windows Defender Firewall
   for Private networks only. Do not expose this app directly to the public
   internet.
E. Start the app on the server PC with `python app.py`.
F. From another PC on the same trusted network, browse to:
       http://SERVER-PC-IP:5002
   Replace SERVER-PC-IP with the server PC's actual IPv4 address.

The browser clients all use the same central SQL Server database because the
Flask app runs on the server PC. Do not start separate copies on every PC if
you want everyone to share the same records.

6. FEATURES
-----------
- Record person, date, purchase-for, remark and total amount
- Enter an initial payment and add later partial payments
- Paid / Partially Paid / Unpaid status and outstanding balances
- Person-wise petty cash advances and deductions from the selected person’s account
- Purchase-specific Direct Cash payments (initial and partial payments), which do not affect fund balances
- Separate Direct Cash fund (receipts, expense deductions, source-by-source balance)
- Per-expense payment history showing mode, source, date, amount and remark
- Reports page with date/status filters, paid date, account/source used for each expense, account usage ranking, highest-spending people, advance summary/register, and filtered CSV export
- Search records and export a CSV report
- Delete an expense (also deletes its associated payment history)

7. SECURITY / OPERATION NOTES
-----------------------------
- This is a starter internal application. Use a strong password and secret key.
- It has a single shared application login configured by environment variables;
  it does not yet have individual employee accounts or role-based permissions.
- Back up SQL Server regularly. Export CSV reports as an additional backup.
- For production use, add HTTPS, individual user accounts, CSRF protection,
  auditing, and stronger deployment configuration before internet exposure.
