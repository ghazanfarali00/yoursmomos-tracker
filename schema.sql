CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'STAFF' CHECK (role IN ('OWNER','STAFF')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    email TEXT,
    google_sub TEXT,
    auth_provider TEXT NOT NULL DEFAULT 'password',
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO settings(key,value) VALUES ('business_cutoff','04:00'),('shop_open','18:00'),('default_rent','100000') ON CONFLICT(key) DO NOTHING;

CREATE TABLE IF NOT EXISTS business_days (
    business_date DATE PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','CLOSED','HOLIDAY','MISSED')),
    holiday_reason TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales (
    business_date DATE PRIMARY KEY REFERENCES business_days(business_date),
    cash BIGINT NOT NULL DEFAULT 0 CHECK (cash >= 0),
    online BIGINT NOT NULL DEFAULT 0 CHECK (online >= 0),
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS menu_categories (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
INSERT INTO menu_categories(name) VALUES ('Momos'),('Pasta'),('Fries'),('Loaded Fries') ON CONFLICT(name) DO NOTHING;
CREATE TABLE IF NOT EXISTS menu_qty (
    business_date DATE NOT NULL REFERENCES business_days(business_date),
    category_id INTEGER NOT NULL REFERENCES menu_categories(id),
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by INTEGER REFERENCES users(id),
    PRIMARY KEY (business_date, category_id)
);

CREATE TABLE IF NOT EXISTS expense_categories (id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
INSERT INTO expense_categories(name) VALUES ('Ingredients'),('Chicken'),('Vegetables'),('Cheese'),('Flour'),('Oil'),('Sauces'),('Disposable'),('Packaging'),('Gas'),('Electricity'),('Cleaning'),('Staff'),('Marketing'),('Maintenance/Repair'),('Other') ON CONFLICT(name) DO NOTHING;

CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    business_date DATE NOT NULL REFERENCES business_days(business_date),
    category_id INTEGER NOT NULL REFERENCES expense_categories(id),
    amount BIGINT NOT NULL CHECK (amount >= 0),
    method TEXT NOT NULL CHECK (method IN ('Cash','Bank')),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by INTEGER REFERENCES users(id),
    deleted BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS bulk_expenses (
    id SERIAL PRIMARY KEY,
    purchase_date DATE NOT NULL,
    business_date DATE NOT NULL REFERENCES business_days(business_date),
    description TEXT NOT NULL,
    category_id INTEGER REFERENCES expense_categories(id),
    total BIGINT NOT NULL CHECK (total >= 0),
    method TEXT NOT NULL CHECK (method IN ('Cash','Bank')),
    start_date DATE NOT NULL,
    weeks INTEGER NOT NULL CHECK (weeks > 0),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by INTEGER REFERENCES users(id),
    deleted BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS bulk_alloc (id SERIAL PRIMARY KEY, bulk_id INTEGER NOT NULL REFERENCES bulk_expenses(id) ON DELETE CASCADE, business_date DATE NOT NULL, amount BIGINT NOT NULL CHECK (amount >= 0), UNIQUE(bulk_id, business_date));

CREATE TABLE IF NOT EXISTS rent (
    business_date DATE PRIMARY KEY REFERENCES business_days(business_date),
    amount BIGINT NOT NULL DEFAULT 100000 CHECK (amount >= 0),
    paid BOOLEAN NOT NULL DEFAULT FALSE,
    method TEXT CHECK (method IN ('Cash','Bank')),
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS withdrawals (id SERIAL PRIMARY KEY, business_date DATE NOT NULL REFERENCES business_days(business_date), amount BIGINT NOT NULL CHECK (amount >= 0), method TEXT NOT NULL CHECK (method IN ('Cash','Bank')), notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), created_by INTEGER REFERENCES users(id), deleted BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE IF NOT EXISTS audit (id SERIAL PRIMARY KEY, username TEXT, action TEXT NOT NULL, entity TEXT, entity_id TEXT, at TIMESTAMPTZ NOT NULL DEFAULT NOW(), details TEXT);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(business_date);
CREATE INDEX IF NOT EXISTS idx_bulk_alloc_date ON bulk_alloc(business_date);
CREATE INDEX IF NOT EXISTS idx_withdrawals_date ON withdrawals(business_date);
CREATE INDEX IF NOT EXISTS idx_menu_qty_date ON menu_qty(business_date);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL;
