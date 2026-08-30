import os
from io import BytesIO
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import bcrypt
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

APP_DIR = os.path.dirname(__file__)
TZ = ZoneInfo("Asia/Karachi")
CURRENCY = "PKR"
CURRENCY_SYMBOL = "Rs."

st.set_page_config(
    page_title="Yours Momos — Business Tracker",
    page_icon="🥟",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------- THEME ----------------

def inject_css():
    st.markdown(
        """
        <style>
        :root { --ym-orange:#ff7a00; --ym-orange2:#ff9d2e; --ym-dark:#111111; }
        .stApp { background: #0b0b0d; }
        [data-testid="stHeader"] { background: rgba(0,0,0,0); }
        [data-testid="stSidebar"] { background: #121214; border-right: 1px solid #2a2a2d; }
        [data-testid="stMetric"] { background: #151518; border:1px solid #29292d; padding: 14px; border-radius: 14px; }
        div.stButton > button, div.stFormSubmitButton > button { border-radius: 10px; font-weight: 700; }
        div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] { background: var(--ym-orange); color: #111; border:0; }
        .ym-title { font-size: 2.2rem; font-weight: 900; margin-bottom: 0; }
        .ym-subtitle { color:#aaa; margin-top:0; }
        .ym-pill { display:inline-block; padding:4px 10px; border-radius:999px; background:#2a1b0d; color:#ff9d2e; font-size:.8rem; font-weight:700; }
        .ym-logo { max-width: 180px; margin-bottom: 8px; }
        @media (max-width: 700px) {
            .ym-title { font-size: 1.65rem; }
            [data-testid="stMetricValue"] { font-size: 1.25rem; }
            section.main > div { padding-left: .7rem; padding-right: .7rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def logo_path():
    for name in ["yours_momos_logo.png", "logo.png", "4c3595a8-67de-4717-8026-ef040e54c64e.png"]:
        p = os.path.join(APP_DIR, name)
        if os.path.exists(p):
            return p
    return None

inject_css()

# ---------------- DATABASE ----------------
@st.cache_resource
def engine():
    url = None
    try:
        url = st.secrets["database"]["url"]
    except Exception:
        try:
            url = st.secrets["DATABASE_URL"]
        except Exception:
            pass
    if not url:
        raise RuntimeError("Database secret missing. Add [database] url in Streamlit Cloud Secrets.")
    return create_engine(url, pool_pre_ping=True, pool_recycle=300)


def sql(q, params=None, fetch=False):
    with engine().begin() as conn:
        result = conn.execute(text(q), params or {})
        if fetch:
            return [dict(row._mapping) for row in result.fetchall()]
        return result.rowcount


def scalar(q, params=None, default=None):
    with engine().begin() as conn:
        value = conn.execute(text(q), params or {}).scalar()
        return default if value is None else value


def init_db():
    schema_path = os.path.join(APP_DIR, "schema.sql")
    with open(schema_path, encoding="utf-8") as f:
        statements = [x.strip() for x in f.read().split(";") if x.strip()]
    with engine().begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        # Safe migrations for databases created by earlier versions.
        migrations = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS google_sub TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_provider TEXT DEFAULT 'password'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
            "ALTER TABLE business_days DROP CONSTRAINT IF EXISTS business_days_status_check",
            "ALTER TABLE business_days ADD CONSTRAINT business_days_status_check CHECK (status IN ('OPEN','CLOSED','HOLIDAY','MISSED'))",
        ]
        for statement in migrations:
            try:
                conn.execute(text(statement))
            except Exception:
                pass
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))


def money(cents):
    return f"{CURRENCY_SYMBOL} {int(cents or 0)/100:,.2f}"


def money_float(v):
    return f"{CURRENCY_SYMBOL} {float(v or 0):,.2f}"


def now_local():
    return datetime.now(TZ)


def current_business_date(dt=None):
    dt = dt or now_local()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    dt = dt.astimezone(TZ)
    d = dt.date()
    if dt.hour < 4:
        d -= timedelta(days=1)
    return d


def ensure_day(d):
    sql(
        """INSERT INTO business_days(business_date,status) VALUES(:d,'OPEN')
           ON CONFLICT(business_date) DO NOTHING""",
        {"d": d},
    )


def audit(action, entity="", entity_id="", details=""):
    user = st.session_state.get("user", {})
    sql(
        """INSERT INTO audit(username,action,entity,entity_id,details)
           VALUES(:u,:a,:e,:i,:d)""",
        {"u": user.get("username", user.get("email", "")), "a": action, "e": entity, "i": str(entity_id), "d": details},
    )

# ---------------- AUTH ----------------

def google_configured():
    try:
        _ = st.secrets["auth"]["client_id"]
        _ = st.secrets["auth"]["client_secret"]
        _ = st.secrets["auth"]["redirect_uri"]
        return True
    except Exception:
        return False


def login_google():
    try:
        st.login()
    except Exception as e:
        st.error(f"Google login is not configured correctly: {e}")
        st.info("Add the [auth] settings shown in the README to Streamlit Cloud Secrets.")


def attach_google_user():
    """Create or find a local user for any successfully authenticated Google account.
    New Google accounts are STAFF by default. Owner can later change role/linkage.
    """
    info = st.user.to_dict()
    email = str(info.get("email", "")).strip().lower()
    sub = str(info.get("sub", "")).strip()
    name = str(info.get("name", info.get("given_name", "Google User"))).strip() or "Google User"
    if not email or not sub:
        st.error("Google did not return a usable email/identity.")
        st.stop()

    rows = sql("SELECT * FROM users WHERE google_sub=:s OR LOWER(COALESCE(email,''))=:e LIMIT 1", {"s": sub, "e": email}, True)
    if rows:
        user = rows[0]
        sql(
            """UPDATE users SET google_sub=:s,email=:e,auth_provider='google',last_login_at=NOW(),name=:n
               WHERE id=:id""",
            {"s": sub, "e": email, "n": name or user["name"], "id": user["id"]},
        )
        user.update({"google_sub": sub, "email": email, "auth_provider": "google", "name": name or user["name"]})
    else:
        username_base = "google_" + sub[:16]
        username = username_base
        suffix = 1
        while scalar("SELECT COUNT(*) FROM users WHERE username=:u", {"u": username}, 0):
            suffix += 1
            username = f"{username_base}_{suffix}"
        unusable = "!google-only!" + sub
        user_id = sql(
            """INSERT INTO users(username,name,password_hash,role,active,email,google_sub,auth_provider,last_login_at)
               VALUES(:u,:n,:h,'STAFF',TRUE,:e,:s,'google',NOW()) RETURNING id""",
            {"u": username, "n": name, "h": unusable, "e": email, "s": sub},
        )
        # sql() doesn't expose RETURNING unless fetch=True; get it back by sub.
        user = sql("SELECT * FROM users WHERE google_sub=:s", {"s": sub}, True)[0]
        audit("CREATE", "google_user", user["id"], f"email={email}")

    if not user.get("active", True):
        st.error("Your Google account is linked to an inactive user. Contact the owner.")
        try:
            st.logout()
        except Exception:
            pass
        st.stop()
    st.session_state.user = user
    audit("LOGIN", "user", user["id"], "provider=google")


def password_login():
    rows = sql("SELECT * FROM users WHERE username=:u AND active=TRUE", {"u": st.session_state.get("login_username", "")}, True)
    if not rows:
        st.error("Invalid username or password.")
        return
    stored = rows[0].get("password_hash", "") or ""
    if not stored.startswith("$2"):
        return
    try:
        ok = bcrypt.checkpw(st.session_state.get("login_password", "").encode(), stored.encode())
    except Exception:
        ok = False
    if ok:
        st.session_state.user = rows[0]
        sql("UPDATE users SET last_login_at=NOW() WHERE id=:id", {"id": rows[0]["id"]})
        audit("LOGIN", "user", rows[0]["id"], "provider=password")
        st.rerun()
    else:
        st.error("Invalid username or password.")


def login_screen():
    st.markdown('<div class="ym-title">Yours Momos</div>', unsafe_allow_html=True)
    st.markdown('<div class="ym-subtitle">Daily sales, expenses & profit tracker</div>', unsafe_allow_html=True)
    p = logo_path()
    if p:
        st.image(p, width=150)
    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Sign in")
        if google_configured():
            st.caption("Any Google account can sign in. New Google accounts are STAFF by default.")
        else:
            st.warning("Google login is not configured yet. Username/password login is available.")
        st.divider()
        with st.form("password_login"):
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Login", use_container_width=True)
        if submitted:
            password_login()

    with c2:
        st.subheader("First-time setup")
        if scalar("SELECT COUNT(*) FROM users", default=0) == 0:
            with st.form("setup"):
                name = st.text_input("Owner name")
                username = st.text_input("Owner username")
                pw = st.text_input("Password", type="password")
                cpw = st.text_input("Confirm password", type="password")
                ok = st.form_submit_button("Create Owner", use_container_width=True)
            if ok:
                if not name or not username or not pw:
                    st.error("All fields are required.")
                elif pw != cpw:
                    st.error("Passwords do not match.")
                elif len(pw) < 8:
                    st.error("Use at least 8 characters for the owner password.")
                else:
                    try:
                        sql(
                            """INSERT INTO users(username,name,password_hash,role,email,auth_provider)
                               VALUES(:u,:n,:h,'OWNER',NULL,'password')""",
                            {"u": username, "n": name, "h": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()},
                        )
                        st.success("Owner account created. Please log in.")
                    except Exception as e:
                        st.error(f"Could not create owner: {e}")
        else:
            st.info("Owner setup is already complete.")
            st.markdown("**Need Google access?** Log in with your owner account first, then link your Google email under Settings → User Management.")

# ---------------- BUSINESS LOGIC ----------------

def totals(d):
    ensure_day(d)
    p = {"d": d}
    s = sql("SELECT cash,online FROM sales WHERE business_date=:d", p, True)
    s = s[0] if s else {"cash": 0, "online": 0}
    normal = scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE business_date=:d AND deleted=FALSE", p, 0)
    alloc = scalar(
        """SELECT COALESCE(SUM(a.amount),0) FROM bulk_alloc a
           JOIN bulk_expenses b ON b.id=a.bulk_id
           WHERE a.business_date=:d AND b.deleted=FALSE""", p, 0)
    rent = sql("SELECT * FROM rent WHERE business_date=:d", p, True)
    rent = rent[0] if rent else {"amount": 100000, "paid": False, "method": "Cash"}
    wd = scalar("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE business_date=:d AND deleted=FALSE", p, 0)
    ce = scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE business_date=:d AND method='Cash' AND deleted=FALSE", p, 0)
    be = scalar("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE business_date=:d AND method='Bank' AND deleted=FALSE", p, 0)
    bc = scalar("SELECT COALESCE(SUM(total),0) FROM bulk_expenses WHERE business_date=:d AND method='Cash' AND deleted=FALSE", p, 0)
    bb = scalar("SELECT COALESCE(SUM(total),0) FROM bulk_expenses WHERE business_date=:d AND method='Bank' AND deleted=FALSE", p, 0)
    wc = scalar("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE business_date=:d AND method='Cash' AND deleted=FALSE", p, 0)
    wb = scalar("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE business_date=:d AND method='Bank' AND deleted=FALSE", p, 0)
    cash, online = s["cash"], s["online"]
    rent_paid = rent["amount"] if rent["paid"] else 0
    gross = cash + online - normal - alloc
    after = gross - rent_paid
    return {
        "cash": cash, "online": online, "sales": cash + online,
        "normal": normal, "alloc": alloc, "expenses_total": normal + alloc,
        "rent": rent["amount"], "rent_paid": bool(rent["paid"]), "before": gross,
        "after": after, "withdrawal": wd,
        "net_cash": cash - ce - bc - (rent["amount"] if rent["paid"] and rent["method"] == "Cash" else 0) - wc,
        "net_bank": online - be - bb - (rent["amount"] if rent["paid"] and rent["method"] == "Bank" else 0) - wb,
    }


def day_picker(key, label="Business Date"):
    d = st.date_input(label, current_business_date(), key=key)
    ensure_day(d)
    st.caption(f"Business period: **{d:%d-%b-%Y} 6:00 PM → {(d + timedelta(days=1)):%d-%b-%Y} 4:00 AM**")
    return d


def get_menu(d):
    rows = sql(
        """SELECT c.name,COALESCE(q.quantity,0) quantity
           FROM menu_categories c LEFT JOIN menu_qty q
           ON q.category_id=c.id AND q.business_date=:d
           WHERE c.active=TRUE ORDER BY c.id""", {"d": d}, True)
    return rows

# ---------------- DASHBOARD / DAILY ENTRY ----------------

def dashboard():
    st.markdown('<div class="ym-title">Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="ym-subtitle">Everything for one business day in one place</div>', unsafe_allow_html=True)
    d = day_picker("dash_date")
    status = scalar("SELECT status FROM business_days WHERE business_date=:d", {"d": d}, "OPEN")
    st.markdown(f'<span class="ym-pill">{status}</span>', unsafe_allow_html=True)

    t = totals(d)
    cols = st.columns(4)
    for col, label, val in zip(cols, ["Total Sales", "Expenses", "Profit Before Rent", "Profit After Rent"], [t["sales"], t["expenses_total"], t["before"], t["after"]]):
        col.metric(label, money(val))
    cols = st.columns(4)
    for col, label, val in zip(cols, ["Cash Sales", "Online Sales", "Rent Paid", "Net Cash Remaining"], [t["cash"], t["online"], t["rent"] if t["rent_paid"] else 0, t["net_cash"]]):
        col.metric(label, money(val))

    st.subheader("Daily Entry")
    with st.form("daily_entry"):
        st.markdown("**Sales**")
        old_sales = sql("SELECT * FROM sales WHERE business_date=:d", {"d": d}, True)
        old_sales = old_sales[0] if old_sales else {"cash": 0, "online": 0, "notes": ""}
        a, b = st.columns(2)
        cash = a.number_input("Cash Sales (PKR)", min_value=0.0, value=old_sales["cash"] / 100, step=100.0)
        online = b.number_input("Online / Bank Sales (PKR)", min_value=0.0, value=old_sales["online"] / 100, step=100.0)

        st.markdown("**Menu quantities sold**")
        cats = sql("SELECT * FROM menu_categories WHERE active=TRUE ORDER BY id", fetch=True)
        old_q = {r["category_id"]: r["quantity"] for r in sql("SELECT category_id,quantity FROM menu_qty WHERE business_date=:d", {"d": d}, True)}
        qvals = {}
        qcols = st.columns(4)
        for i, c in enumerate(cats):
            qvals[c["id"]] = qcols[i % 4].number_input(c["name"], min_value=0, value=old_q.get(c["id"], 0), step=1, key=f"daily_q_{c['id']}")

        st.markdown("**Daily rent**")
        old_rent = sql("SELECT * FROM rent WHERE business_date=:d", {"d": d}, True)
        default_rent = int(scalar("SELECT value FROM settings WHERE key='default_rent'", default="100000"))
        old_rent = old_rent[0] if old_rent else {"amount": default_rent, "paid": False, "method": "Cash", "notes": ""}
        rc1, rc2, rc3 = st.columns(3)
        rent_amount = rc1.number_input("Rent (PKR)", min_value=0.0, value=old_rent["amount"] / 100, step=100.0)
        rent_paid = rc2.checkbox("Rent Paid", value=old_rent["paid"])
        rent_method = rc3.selectbox("Rent Method", ["Cash", "Bank"], index=0 if old_rent.get("method") != "Bank" else 1)
        notes = st.text_area("Daily notes", value=old_sales.get("notes") or "")
        save = st.form_submit_button("Save Daily Entry", type="primary", use_container_width=True)

    if save:
        sql(
            """INSERT INTO sales(business_date,cash,online,notes,updated_by) VALUES(:d,:c,:o,:n,:u)
               ON CONFLICT(business_date) DO UPDATE SET cash=EXCLUDED.cash,online=EXCLUDED.online,notes=EXCLUDED.notes,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
            {"d": d, "c": round(cash * 100), "o": round(online * 100), "n": notes, "u": st.session_state.user["id"]},
        )
        for c in cats:
            sql(
                """INSERT INTO menu_qty(business_date,category_id,quantity,updated_by) VALUES(:d,:c,:q,:u)
                   ON CONFLICT(business_date,category_id) DO UPDATE SET quantity=EXCLUDED.quantity,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
                {"d": d, "c": c["id"], "q": qvals[c["id"]], "u": st.session_state.user["id"]},
            )
        sql(
            """INSERT INTO rent(business_date,amount,paid,method,notes,updated_by) VALUES(:d,:a,:p,:m,:n,:u)
               ON CONFLICT(business_date) DO UPDATE SET amount=EXCLUDED.amount,paid=EXCLUDED.paid,method=EXCLUDED.method,notes=EXCLUDED.notes,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
            {"d": d, "a": round(rent_amount * 100), "p": rent_paid, "m": rent_method, "n": notes, "u": st.session_state.user["id"]},
        )
        audit("UPSERT", "daily_entry", d)
        st.success("Daily entry saved.")
        st.rerun()

    st.subheader("Menu Sold")
    menu = get_menu(d)
    if menu:
        cols = st.columns(min(4, len(menu)))
        for i, row in enumerate(menu):
            cols[i % len(cols)].metric(row["name"], int(row["quantity"]))

# ---------------- SALES ----------------

def sales_page():
    st.header("Daily Sales")
    d = day_picker("sales_date")
    rows = sql("SELECT * FROM sales WHERE business_date=:d", {"d": d}, True)
    r = rows[0] if rows else {"cash": 0, "online": 0, "notes": ""}
    with st.form("sales_edit"):
        cash = st.number_input("Cash Sales (PKR)", min_value=0.0, value=r["cash"] / 100, step=100.0)
        online = st.number_input("Online / Bank Sales (PKR)", min_value=0.0, value=r["online"] / 100, step=100.0)
        notes = st.text_area("Notes", value=r.get("notes") or "")
        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Save / Edit Sales", type="primary", use_container_width=True)
        delete = c2.form_submit_button("Delete Sales Record", use_container_width=True)
    if save:
        sql(
            """INSERT INTO sales(business_date,cash,online,notes,updated_by) VALUES(:d,:c,:o,:n,:u)
               ON CONFLICT(business_date) DO UPDATE SET cash=EXCLUDED.cash,online=EXCLUDED.online,notes=EXCLUDED.notes,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
            {"d": d, "c": round(cash * 100), "o": round(online * 100), "n": notes, "u": st.session_state.user["id"]},
        )
        audit("UPDATE", "sales", d); st.success("Sales updated."); st.rerun()
    if delete:
        sql("DELETE FROM sales WHERE business_date=:d", {"d": d})
        audit("DELETE", "sales", d); st.success("Sales record deleted."); st.rerun()

# ---------------- MENU ----------------

def menu_page():
    st.header("Menu Quantities")
    d = day_picker("menu_date")
    cats = sql("SELECT * FROM menu_categories WHERE active=TRUE ORDER BY id", fetch=True)
    old = {r["category_id"]: r["quantity"] for r in sql("SELECT category_id,quantity FROM menu_qty WHERE business_date=:d", {"d": d}, True)}
    with st.form("menu"):
        vals = {}
        cols = st.columns(2)
        for i, c in enumerate(cats):
            vals[c["id"]] = cols[i % 2].number_input(c["name"], min_value=0, value=old.get(c["id"], 0), step=1)
        ok = st.form_submit_button("Save / Edit Quantities", type="primary", use_container_width=True)
    if ok:
        for c in cats:
            sql(
                """INSERT INTO menu_qty(business_date,category_id,quantity,updated_by) VALUES(:d,:c,:q,:u)
                   ON CONFLICT(business_date,category_id) DO UPDATE SET quantity=EXCLUDED.quantity,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
                {"d": d, "c": c["id"], "q": vals[c["id"]], "u": st.session_state.user["id"]},
            )
        audit("UPDATE", "menu_qty", d); st.success("Menu quantities updated."); st.rerun()

# ---------------- EXPENSES ----------------

def expenses_page():
    st.header("Expenses")
    d = day_picker("expense_date")
    cats = sql("SELECT * FROM expense_categories WHERE active=TRUE ORDER BY name", fetch=True)
    with st.expander("Add a new expense category"):
        with st.form("new_cat"):
            new_name = st.text_input("Category name")
            okcat = st.form_submit_button("Add Category")
        if okcat and new_name.strip():
            try:
                sql("INSERT INTO expense_categories(name) VALUES(:n)", {"n": new_name.strip()})
                audit("CREATE", "expense_category", new_name.strip()); st.success("Category added."); st.rerun()
            except Exception as e:
                st.error(f"Could not add category: {e}")

    st.subheader("Add multiple expenses at once")
    with st.form("multi_expenses"):
        count = st.number_input("Number of expense lines", min_value=1, max_value=12, value=2, step=1)
        lines = []
        for i in range(int(count)):
            c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
            cat = c1.selectbox("Category", cats, format_func=lambda x: x["name"], key=f"excat_{i}")
            amount = c2.number_input("Amount", min_value=0.0, step=50.0, key=f"examt_{i}")
            method = c3.selectbox("Method", ["Cash", "Bank"], key=f"exmethod_{i}")
            note = c4.text_input("Note", key=f"exnote_{i}")
            lines.append((cat, amount, method, note))
        add = st.form_submit_button("Add All Expenses", type="primary", use_container_width=True)
    if add:
        added = 0
        for cat, amount, method, note in lines:
            if amount > 0:
                sql(
                    """INSERT INTO expenses(business_date,category_id,amount,method,notes,created_by) VALUES(:d,:c,:a,:m,:n,:u)""",
                    {"d": d, "c": cat["id"], "a": round(amount * 100), "m": method, "n": note, "u": st.session_state.user["id"]},
                )
                added += 1
        if added:
            audit("CREATE", "expenses", d, f"count={added}"); st.success(f"Added {added} expense(s)."); st.rerun()

    st.subheader("Existing expenses")
    rows = sql(
        """SELECT e.id,e.category_id,c.name category,e.amount,e.method,e.notes,e.created_at
           FROM expenses e JOIN expense_categories c ON c.id=e.category_id
           WHERE e.business_date=:d AND e.deleted=FALSE ORDER BY e.id DESC""", {"d": d}, True)
    if not rows:
        st.info("No expenses for this business day.")
        return
    search = st.text_input("Search expenses", key="expense_search")
    method_filter = st.selectbox("Filter payment method", ["All", "Cash", "Bank"], key="expense_method_filter")
    df = pd.DataFrame(rows)
    if search:
        mask = df["category"].str.contains(search, case=False, na=False) | df["notes"].fillna("").str.contains(search, case=False, na=False)
        df = df[mask]
    if method_filter != "All":
        df = df[df["method"] == method_filter]
    st.dataframe(df.assign(amount=df["amount"] / 100), use_container_width=True, hide_index=True)

    st.markdown("**Edit or delete an expense**")
    ids = df["id"].tolist() if not df.empty else []
    if ids:
        selected = st.selectbox("Select expense", ids, format_func=lambda x: f"Expense #{x}")
        row = next(x for x in rows if x["id"] == selected)
        cat_idx = next((i for i, c in enumerate(cats) if c["id"] == row["category_id"]) if "category_id" in row else 0)
        with st.form("edit_expense"):
            cat = st.selectbox("Category", cats, index=cat_idx, format_func=lambda x: x["name"])
            amount = st.number_input("Amount", min_value=0.0, value=row["amount"] / 100, step=50.0)
            method = st.selectbox("Method", ["Cash", "Bank"], index=0 if row["method"] == "Cash" else 1)
            note = st.text_area("Notes", value=row["notes"] or "")
            c1, c2 = st.columns(2)
            upd = c1.form_submit_button("Update Expense", type="primary", use_container_width=True)
            dele = c2.form_submit_button("Delete Expense", use_container_width=True)
        if upd:
            sql("UPDATE expenses SET category_id=:c,amount=:a,method=:m,notes=:n WHERE id=:id", {"c": cat["id"], "a": round(amount * 100), "m": method, "n": note, "id": selected})
            audit("UPDATE", "expense", selected); st.success("Expense updated."); st.rerun()
        if dele:
            sql("UPDATE expenses SET deleted=TRUE WHERE id=:id", {"id": selected})
            audit("DELETE", "expense", selected); st.success("Expense deleted."); st.rerun()

# ---------------- BULK ----------------

def bulk_page():
    st.header("Bulk / Weekly Expenses")
    st.info("Bulk purchases are recorded on their purchase date for cash/bank movement; weekly allocations affect profit across the selected weeks.")
    cats = sql("SELECT * FROM expense_categories WHERE active=TRUE ORDER BY name", fetch=True)
    with st.form("bulk_add"):
        purchase = st.date_input("Purchase Date", current_business_date())
        desc = st.text_input("Description")
        cat = st.selectbox("Category", cats, format_func=lambda x: x["name"])
        total = st.number_input("Total Purchase (PKR)", min_value=0.0, step=500.0)
        method = st.selectbox("Payment Method", ["Cash", "Bank"])
        start = st.date_input("Allocation Start Date", purchase)
        weeks = st.number_input("Number of Weeks", min_value=1, max_value=104, value=4, step=1)
        notes = st.text_area("Notes")
        ok = st.form_submit_button("Save Bulk Expense", type="primary", use_container_width=True)
    if ok and total > 0 and desc.strip():
        purchase_bd = purchase
        ensure_day(purchase_bd)
        amount = round(total * 100); weeks = int(weeks)
        sql(
            """INSERT INTO bulk_expenses(purchase_date,business_date,description,category_id,total,method,start_date,weeks,notes,created_by)
               VALUES(:p,:bd,:desc,:cat,:total,:method,:start,:weeks,:notes,:u)""",
            {"p": purchase, "bd": purchase_bd, "desc": desc.strip(), "cat": cat["id"], "total": amount, "method": method, "start": start, "weeks": weeks, "notes": notes, "u": st.session_state.user["id"]},
        )
        bulk_id = scalar("SELECT id FROM bulk_expenses WHERE purchase_date=:p AND description=:d ORDER BY id DESC LIMIT 1", {"p": purchase, "d": desc.strip()})
        base, rem = divmod(amount, weeks)
        for i in range(weeks):
            sql("INSERT INTO bulk_alloc(bulk_id,business_date,amount) VALUES(:id,:d,:a)", {"id": bulk_id, "d": start + timedelta(days=7 * i), "a": base + (1 if i < rem else 0)})
        audit("CREATE", "bulk_expense", bulk_id); st.success("Bulk expense saved."); st.rerun()

    rows = sql(
        """SELECT b.id,b.purchase_date,b.description,c.name category,b.total,b.method,b.start_date,b.weeks,b.notes
           FROM bulk_expenses b LEFT JOIN expense_categories c ON c.id=b.category_id
           WHERE b.deleted=FALSE ORDER BY b.id DESC LIMIT 100""", fetch=True)
    if rows:
        st.subheader("Bulk expense history")
        q = st.text_input("Search bulk expenses")
        df = pd.DataFrame(rows)
        if q:
            df = df[df["description"].str.contains(q, case=False, na=False) | df["category"].fillna("").str.contains(q, case=False, na=False)]
        st.dataframe(df.assign(total=df["total"] / 100), use_container_width=True, hide_index=True)
        selected = st.selectbox("Edit / delete bulk expense", df["id"].tolist(), format_func=lambda x: f"Bulk #{x}")
        row = next(x for x in rows if x["id"] == selected)
        with st.form("edit_bulk"):
            desc2 = st.text_input("Description", value=row["description"])
            total2 = st.number_input("Total", min_value=0.0, value=row["total"] / 100, step=500.0)
            start2 = st.date_input("Allocation Start", value=row["start_date"])
            weeks2 = st.number_input("Weeks", min_value=1, max_value=104, value=int(row["weeks"]), step=1)
            c1, c2 = st.columns(2)
            upd = c1.form_submit_button("Update Bulk", type="primary", use_container_width=True)
            dele = c2.form_submit_button("Delete Bulk", use_container_width=True)
        if upd:
            sql("UPDATE bulk_expenses SET description=:d,total=:t,start_date=:s,weeks=:w WHERE id=:id", {"d": desc2, "t": round(total2 * 100), "s": start2, "w": int(weeks2), "id": selected})
            sql("DELETE FROM bulk_alloc WHERE bulk_id=:id", {"id": selected})
            amt = round(total2 * 100); w = int(weeks2); base, rem = divmod(amt, w)
            for i in range(w):
                sql("INSERT INTO bulk_alloc(bulk_id,business_date,amount) VALUES(:id,:d,:a)", {"id": selected, "d": start2 + timedelta(days=7 * i), "a": base + (1 if i < rem else 0)})
            audit("UPDATE", "bulk_expense", selected); st.success("Bulk expense updated."); st.rerun()
        if dele:
            sql("UPDATE bulk_expenses SET deleted=TRUE WHERE id=:id", {"id": selected})
            audit("DELETE", "bulk_expense", selected); st.success("Bulk expense deleted."); st.rerun()

# ---------------- RENT / WITHDRAWAL ----------------

def rent_page():
    st.header("Daily Rent")
    d = day_picker("rent_date")
    rows = sql("SELECT * FROM rent WHERE business_date=:d", {"d": d}, True)
    default = int(scalar("SELECT value FROM settings WHERE key='default_rent'", default="100000"))
    r = rows[0] if rows else {"amount": default, "paid": False, "method": "Cash", "notes": ""}
    with st.form("rent"):
        amount = st.number_input("Rent Amount (PKR)", min_value=0.0, value=r["amount"] / 100, step=100.0)
        paid = st.checkbox("Rent Paid", value=r["paid"])
        method = st.selectbox("Payment Method", ["Cash", "Bank"], index=0 if r.get("method") != "Bank" else 1)
        notes = st.text_area("Notes", value=r.get("notes") or "")
        ok = st.form_submit_button("Save / Edit Rent", type="primary", use_container_width=True)
    if ok:
        sql(
            """INSERT INTO rent(business_date,amount,paid,method,notes,updated_by) VALUES(:d,:a,:p,:m,:n,:u)
               ON CONFLICT(business_date) DO UPDATE SET amount=EXCLUDED.amount,paid=EXCLUDED.paid,method=EXCLUDED.method,notes=EXCLUDED.notes,updated_at=NOW(),updated_by=EXCLUDED.updated_by""",
            {"d": d, "a": round(amount * 100), "p": paid, "m": method, "n": notes, "u": st.session_state.user["id"]},
        )
        audit("UPDATE", "rent", d); st.success("Rent saved."); st.rerun()


def withdrawal_page():
    st.header("Owner Personal Withdrawal")
    d = day_picker("withdrawal_date")
    with st.form("withdrawal"):
        amount = st.number_input("Amount (PKR)", min_value=0.0, step=100.0)
        method = st.selectbox("Payment Method", ["Cash", "Bank"])
        notes = st.text_area("Notes")
        ok = st.form_submit_button("Add Withdrawal", type="primary", use_container_width=True)
    if ok and amount > 0:
        sql("INSERT INTO withdrawals(business_date,amount,method,notes,created_by) VALUES(:d,:a,:m,:n,:u)", {"d": d, "a": round(amount * 100), "m": method, "n": notes, "u": st.session_state.user["id"]})
        audit("CREATE", "withdrawal", d); st.success("Withdrawal added."); st.rerun()
    rows = sql("SELECT id,amount,method,notes,created_at FROM withdrawals WHERE business_date=:d AND deleted=FALSE ORDER BY id DESC", {"d": d}, True)
    if rows:
        st.dataframe(pd.DataFrame(rows).assign(amount=lambda x: x["amount"] / 100), use_container_width=True, hide_index=True)
        selected = st.selectbox("Select withdrawal", [r["id"] for r in rows])
        r = next(x for x in rows if x["id"] == selected)
        with st.form("edit_withdrawal"):
            amount2 = st.number_input("Amount", min_value=0.0, value=r["amount"] / 100, step=100.0)
            method2 = st.selectbox("Method", ["Cash", "Bank"], index=0 if r["method"] == "Cash" else 1)
            note2 = st.text_area("Notes", value=r["notes"] or "")
            c1, c2 = st.columns(2)
            upd = c1.form_submit_button("Update", type="primary", use_container_width=True)
            dele = c2.form_submit_button("Delete", use_container_width=True)
        if upd:
            sql("UPDATE withdrawals SET amount=:a,method=:m,notes=:n WHERE id=:id", {"a": round(amount2 * 100), "m": method2, "n": note2, "id": selected}); audit("UPDATE", "withdrawal", selected); st.rerun()
        if dele:
            sql("UPDATE withdrawals SET deleted=TRUE WHERE id=:id", {"id": selected}); audit("DELETE", "withdrawal", selected); st.rerun()

# ---------------- BUSINESS DAYS ----------------

def business_days_page():
    st.header("Business Days")
    st.caption("You can enter or correct any previous business date. The business day runs from 6:00 PM until 4:00 AM next calendar day.")
    d = st.date_input("Business Date", current_business_date(), key="business_day")
    ensure_day(d)
    r = sql("SELECT * FROM business_days WHERE business_date=:d", {"d": d}, True)[0]
    with st.form("businessday"):
        status = st.selectbox("Status", ["OPEN", "CLOSED", "HOLIDAY", "MISSED"], index=["OPEN", "CLOSED", "HOLIDAY", "MISSED"].index(r["status"]))
        reason = st.text_input("Holiday / missed-day reason", value=r["holiday_reason"] or "")
        notes = st.text_area("Notes", value=r["notes"] or "")
        ok = st.form_submit_button("Save Day Status", type="primary")
    if ok:
        sql("UPDATE business_days SET status=:s,holiday_reason=:r,notes=:n WHERE business_date=:d", {"s": status, "r": reason, "n": notes, "d": d})
        audit("UPDATE", "business_day", d); st.success("Business day updated."); st.rerun()
    rows = sql("SELECT business_date,status,holiday_reason,notes FROM business_days ORDER BY business_date DESC LIMIT 180", fetch=True)
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------- REPORTS ----------------

def report_data(start, end):
    dates = [start + timedelta(days=i) for i in range((end - start).days)]
    rows = []
    for d in dates:
        t = totals(d)
        menu = get_menu(d)
        m = {x["name"]: x["quantity"] for x in menu}
        status = scalar("SELECT status FROM business_days WHERE business_date=:d", {"d": d}, "NOT ENTERED")
        rows.append({
            "Business Date": d, "Status": status, "Sales": t["sales"] / 100,
            "Cash Sales": t["cash"] / 100, "Online Sales": t["online"] / 100,
            "Normal Expenses": t["normal"] / 100, "Bulk Allocation": t["alloc"] / 100,
            "Profit Before Rent": t["before"] / 100, "Rent Paid": (t["rent"] if t["rent_paid"] else 0) / 100,
            "Profit After Rent": t["after"] / 100, "Withdrawals": t["withdrawal"] / 100,
            "Net Cash": t["net_cash"] / 100, "Net Bank": t["net_bank"] / 100,
            "Momos": m.get("Momos", 0), "Pasta": m.get("Pasta", 0), "Fries": m.get("Fries", 0), "Loaded Fries": m.get("Loaded Fries", 0),
        })
    return pd.DataFrame(rows)


def excel_bytes(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Report")
    return output.getvalue()


def reports_page():
    st.header("Reports")
    tab1, tab2, tab3 = st.tabs(["Daily", "Weekly", "Monthly"])
    with tab1:
        d = day_picker("report_daily")
        df = report_data(d, d + timedelta(days=1))
        report_render(df, "Daily Report")
    with tab2:
        start = st.date_input("Week Start", current_business_date(), key="report_week")
        df = report_data(start, start + timedelta(days=7))
        report_render(df, "Weekly Report")
    with tab3:
        c1, c2 = st.columns(2)
        year = c1.number_input("Year", 2020, 2100, now_local().year, key="report_year")
        month = c2.number_input("Month", 1, 12, now_local().month, key="report_month")
        start = date(int(year), int(month), 1)
        end = date(int(year) + 1, 1, 1) if int(month) == 12 else date(int(year), int(month) + 1, 1)
        df = report_data(start, end)
        report_render(df, "Monthly Report")


def report_render(df, title):
    if df.empty:
        st.info("No dates in this report.")
        return
    numeric = ["Sales", "Normal Expenses", "Bulk Allocation", "Profit Before Rent", "Rent Paid", "Profit After Rent", "Withdrawals", "Net Cash", "Net Bank"]
    c = st.columns(4)
    c[0].metric("Sales", money_float(df["Sales"].sum()))
    c[1].metric("Expenses", money_float((df["Normal Expenses"] + df["Bulk Allocation"]).sum()))
    c[2].metric("Profit After Rent", money_float(df["Profit After Rent"].sum()))
    c[3].metric("Momos Sold", int(df["Momos"].sum()))

    st.subheader("Trend")
    chart = df.set_index("Business Date")[["Sales", "Profit After Rent"]]
    st.line_chart(chart, use_container_width=True)
    st.subheader("Menu Quantities")
    st.bar_chart(df.set_index("Business Date")[["Momos", "Pasta", "Fries", "Loaded Fries"]], use_container_width=True)
    st.dataframe(df, use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    c1.download_button("Export CSV", df.to_csv(index=False).encode("utf-8"), file_name="yours_momos_report.csv", mime="text/csv", use_container_width=True)
    c2.download_button("Export Excel", excel_bytes(df), file_name="yours_momos_report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

# ---------------- SETTINGS / USERS ----------------

def settings_page():
    user = st.session_state.user
    if user["role"] != "OWNER":
        st.warning("Owner access required.")
        return
    st.header("Settings")
    st.subheader("Business")
    default = int(scalar("SELECT value FROM settings WHERE key='default_rent'", default="100000"))
    with st.form("settings"):
        rent = st.number_input("Default Daily Rent (PKR)", min_value=0.0, value=default / 100, step=100.0)
        ok = st.form_submit_button("Save Business Settings", type="primary")
    if ok:
        sql("UPDATE settings SET value=:v WHERE key='default_rent'", {"v": str(round(rent * 100))})
        audit("UPDATE", "settings", "default_rent"); st.success("Saved.")

    st.subheader("User Management")
    users = sql("SELECT id,username,name,email,role,active,auth_provider,last_login_at,created_at FROM users ORDER BY id", fetch=True)
    st.dataframe(pd.DataFrame(users), use_container_width=True, hide_index=True)

    with st.expander("Create / link user"):
        with st.form("newuser"):
            name = st.text_input("Name")
            username = st.text_input("Username")
            email = st.text_input("Google email (optional)")
            password = st.text_input("Password (optional if Google only)", type="password")
            role = st.selectbox("Role", ["STAFF", "OWNER"])
            ok = st.form_submit_button("Create User")
        if ok:
            try:
                if not name or not username:
                    st.error("Name and username are required.")
                elif not password and not email:
                    st.error("Provide a password or a Google email.")
                else:
                    ph = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else "!google-only!"
                    sql("INSERT INTO users(username,name,password_hash,role,email,auth_provider) VALUES(:u,:n,:h,:r,:e,:p)", {"u": username, "n": name, "h": ph, "r": role, "e": email.strip().lower() or None, "p": "password" if password else "google"})
                    audit("CREATE", "user", username); st.success("User created."); st.rerun()
            except Exception as e:
                st.error(f"Could not create user: {e}")

    if users:
        st.markdown("**Edit employee access**")
        uid = st.selectbox("Select user", [u["id"] for u in users], format_func=lambda x: next(u["name"] for u in users if u["id"] == x))
        u = next(x for x in users if x["id"] == uid)
        with st.form("edit_user"):
            name2 = st.text_input("Name", value=u["name"])
            email2 = st.text_input("Google email", value=u.get("email") or "")
            role2 = st.selectbox("Role", ["STAFF", "OWNER"], index=0 if u["role"] == "STAFF" else 1)
            active2 = st.checkbox("Active", value=u["active"])
            reset_pw = st.text_input("New password (leave blank to keep)", type="password")
            c1, c2 = st.columns(2)
            save = c1.form_submit_button("Save User", type="primary", use_container_width=True)
            del_user = c2.form_submit_button("Deactivate User", use_container_width=True)
        if save:
            sql("UPDATE users SET name=:n,email=:e,role=:r,active=:a WHERE id=:id", {"n": name2, "e": email2.strip().lower() or None, "r": role2, "a": active2, "id": uid})
            if reset_pw:
                sql("UPDATE users SET password_hash=:h,auth_provider='password' WHERE id=:id", {"h": bcrypt.hashpw(reset_pw.encode(), bcrypt.gensalt()).decode(), "id": uid})
            audit("UPDATE", "user", uid); st.success("User updated."); st.rerun()
        if del_user:
            if uid == user["id"]:
                st.error("You cannot deactivate your own account.")
            else:
                sql("UPDATE users SET active=FALSE WHERE id=:id", {"id": uid}); audit("DEACTIVATE", "user", uid); st.success("User deactivated."); st.rerun()

    st.subheader("Google login setup")
    if google_configured():
        st.success("Google login configuration is present. Any Google account can authenticate; new accounts are STAFF by default.")
    else:
        st.warning("Google login is not configured in Streamlit Secrets yet.")
        st.code('[auth]\nredirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"\ncookie_secret = "GENERATE-A-LONG-RANDOM-SECRET"\nclient_id = "YOUR-GOOGLE-CLIENT-ID"\nclient_secret = "YOUR-GOOGLE-CLIENT-SECRET"\nserver_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"', language="toml")

# ---------------- MAIN ----------------

def main():
    init_db()

    # Native Streamlit OIDC session: any Google account is accepted by Google; app-level role comes from DB.
    try:
        oidc_logged_in = bool(st.user.is_logged_in)
    except Exception:
        oidc_logged_in = False

    if oidc_logged_in and "user" not in st.session_state:
        attach_google_user()

    if "user" not in st.session_state:
        login_screen()
        return

    user = st.session_state.user
    p = logo_path()
    with st.sidebar:
        if p:
            st.image(p, width=130)
        st.markdown("### Yours Momos")
        st.caption(f"{user['name']} · {user['role']}")
        st.divider()
        page = st.radio("Navigation", ["Dashboard", "Expenses", "Bulk Expenses", "Owner Withdrawal", "Reports", "Business Days", "Settings"], index=0)
        st.divider()
        if st.button("Logout", use_container_width=True):
            audit("LOGOUT")
            st.session_state.pop("user", None)
            try:
                if st.user.is_logged_in:
                    st.logout()
            except Exception:
                pass
            st.rerun()

    if page == "Dashboard":
        dashboard()
    elif page == "Expenses":
        expenses_page()
    elif page == "Bulk Expenses":
        bulk_page()
    elif page == "Owner Withdrawal":
        withdrawal_page()
    elif page == "Reports":
        reports_page()
    elif page == "Business Days":
        business_days_page()
    elif page == "Settings":
        settings_page()


if __name__ == "__main__":
    main()
