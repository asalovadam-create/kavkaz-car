-- KAVKAZ-CAR — справочная SQL-схема для PostgreSQL.
--
-- Это НЕ основной механизм миграций проекта: основной механизм — Flask-Migrate
-- (Alembic), см. README, раздел «Миграции». Этот файл — читаемый снимок схемы,
-- который соответствует models.py на момент первого релиза, и который можно
-- использовать для ручного разворачивания БД, ревью или обучения нового
-- разработчика, если по каким-то причинам Alembic недоступен.
--
-- Схема должна оставаться синхронной с models.py. Изменения вносите ТОЛЬКО
-- через `flask db migrate` — не редактируйте боевую БД вручную (раздел 82 ТЗ).

CREATE TABLE IF NOT EXISTS cities (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(60) UNIQUE NOT NULL,
    name VARCHAR(80) NOT NULL,
    region VARCHAR(120),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS ix_cities_slug ON cities (slug);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(20) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_phone ON users (phone);

CREATE TABLE IF NOT EXISTS owner_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name VARCHAR(120) NOT NULL,
    company_name VARCHAR(150),
    logo_url VARCHAR(500),
    description TEXT,
    work_hours VARCHAR(200),
    phone_verified BOOLEAN NOT NULL DEFAULT FALSE,
    contact_consent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS owner_cities (
    owner_profile_id INTEGER NOT NULL REFERENCES owner_profiles(id) ON DELETE CASCADE,
    city_id INTEGER NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    PRIMARY KEY (owner_profile_id, city_id)
);

CREATE TABLE IF NOT EXISTS cars (
    id SERIAL PRIMARY KEY,
    public_id VARCHAR(12) UNIQUE NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    city_id INTEGER NOT NULL REFERENCES cities(id),
    brand VARCHAR(80) NOT NULL,
    model VARCHAR(80) NOT NULL,
    year INTEGER,
    body_type VARCHAR(30),
    seats INTEGER,
    transmission_auto BOOLEAN NOT NULL DEFAULT TRUE,
    price_per_day INTEGER,
    min_rental_days INTEGER NOT NULL DEFAULT 1,
    with_driver BOOLEAN NOT NULL DEFAULT FALSE,
    for_wedding BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_available BOOLEAN NOT NULL DEFAULT FALSE,
    is_premium BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    extra_terms TEXT,
    phone VARCHAR(20) NOT NULL,
    telegram VARCHAR(120),
    whatsapp VARCHAR(20),
    contact_consent BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    is_boosted_until TIMESTAMP,
    last_confirmed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_cars_public_id ON cars (public_id);
CREATE INDEX IF NOT EXISTS ix_cars_owner_id ON cars (owner_id);
CREATE INDEX IF NOT EXISTS ix_cars_city_id ON cars (city_id);
CREATE INDEX IF NOT EXISTS ix_cars_brand ON cars (brand);
CREATE INDEX IF NOT EXISTS ix_cars_model ON cars (model);
CREATE INDEX IF NOT EXISTS ix_cars_price_per_day ON cars (price_per_day);
CREATE INDEX IF NOT EXISTS ix_cars_status ON cars (status);
CREATE INDEX IF NOT EXISTS ix_cars_created_at ON cars (created_at);

CREATE TABLE IF NOT EXISTS car_photos (
    id SERIAL PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    url VARCHAR(500) NOT NULL,
    thumbnail_url VARCHAR(500),
    storage_public_id VARCHAR(255),
    position INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_car_photos_car_id ON car_photos (car_id);

CREATE TABLE IF NOT EXISTS car_features (
    id SERIAL PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    key VARCHAR(60) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_car_features_car_id ON car_features (car_id);

CREATE TABLE IF NOT EXISTS promo_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(40) UNIQUE NOT NULL,
    plan VARCHAR(20) NOT NULL,
    duration_days INTEGER NOT NULL,
    expires_at TIMESTAMP,
    used_at TIMESTAMP,
    used_by_id INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    created_by_admin_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_promo_codes_code ON promo_codes (code);

CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP,
    payment_provider VARCHAR(40),
    payment_status VARCHAR(20),
    transaction_id VARCHAR(120),
    activated_by_admin_id INTEGER,
    promo_code_id INTEGER REFERENCES promo_codes(id),
    boosts_used_this_period INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_subscriptions_owner_id ON subscriptions (owner_id);

CREATE TABLE IF NOT EXISTS favorites (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_favorite_user_car UNIQUE (user_id, car_id)
);
CREATE INDEX IF NOT EXISTS ix_favorites_user_id ON favorites (user_id);
CREATE INDEX IF NOT EXISTS ix_favorites_car_id ON favorites (car_id);

CREATE TABLE IF NOT EXISTS contact_clicks (
    id SERIAL PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_contact_clicks_car_id ON contact_clicks (car_id);
CREATE INDEX IF NOT EXISTS ix_contact_clicks_created_at ON contact_clicks (created_at);

CREATE TABLE IF NOT EXISTS views (
    id SERIAL PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    viewer_hash VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_views_car_id ON views (car_id);
CREATE INDEX IF NOT EXISTS ix_views_created_at ON views (created_at);

CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    car_id INTEGER NOT NULL REFERENCES cars(id) ON DELETE CASCADE,
    reason VARCHAR(30) NOT NULL,
    message TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'new',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP,
    resolved_by_admin_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_reports_car_id ON reports (car_id);

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(60) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_super BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_logs (
    id SERIAL PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admin_users(id),
    action VARCHAR(80) NOT NULL,
    target_type VARCHAR(40),
    target_id INTEGER,
    ip VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
