# main.py
# ─────────────────────────────────────────────
# Flask backend for VaultCore Wallet
# ─────────────────────────────────────────────
from __future__ import annotations
import logging
from datetime import datetime, timedelta
import requests
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
import re
from dotenv import load_dotenv
import pathlib
import os
from cryptography.fernet import Fernet
import hmac, hashlib, time, urllib.parse, secrets
import io
import qrcode
import base64
from functools import wraps
import pyotp
from model import (
    Notification, User, db, UserSettings,
    Wallet, Transaction, MarketData, WalletHash,
    SupportTicket, SupportMessage, Trade, ConnectedWallet, LinkedBank,PasswordResetToken
)
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.utils import secure_filename
from sqlalchemy import inspect
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

_base_dir = pathlib.Path(__file__).parent.resolve()
load_dotenv(dotenv_path=_base_dir / '.env')


DB_PATH = _base_dir / 'instance' / 'user.db'

env_path = pathlib.Path('.env')

# ── logging setup ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY")
app.config["API_KEY"]   = os.getenv("NEWS_API_KEY")
CMC_PRO_API_KEY         = os.getenv("CMC_PRO_API_KEY")
CRYPTO_MARKET_URL       = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
NEWS_API_URL            = "https://newsapi.org/v2/everything"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
PAPER_TRADE    = os.getenv("PAPER_TRADE", "true").lower() == "true"
BINANCE_BASE   = "https://api.binance.com"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
# CRITICAL: Render always serves over HTTPS — without Secure flag the
# session cookie is silently dropped by the browser after every login.
_on_render = bool(os.getenv("RENDER", ""))
app.config["SESSION_COOKIE_SECURE"] = _on_render
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///user.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config["SQLALCHEMY_ECHO"] = False
MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
MAIL_FROM     = os.getenv('MAIL_FROM',     MAIL_USERNAME)
APP_BASE_URL = os.getenv('APP_BASE_URL', 'http://127.0.0.1:5000').rstrip('/')


log.info("Flask app configured")

# Coming soon feature: read from env or instance flag file
app.config["COMING_SOON_ENV"] = os.getenv("COMING_SOON", "false").lower() == "true"
COMING_SOON_FLAG = pathlib.Path(_base_dir) / 'instance' / 'COMING_SOON'

def is_coming_soon_enabled():
    """Return True if coming-soon mode should be active.

    Priority: instance flag file > environment variable.
    """
    try:
        if COMING_SOON_FLAG.exists():
            return True
    except Exception:
        pass
    return bool(app.config.get("COMING_SOON_ENV"))

db.init_app(app)
# main.py - Replace the context processor section
log.info("Database initialized")


@app.context_processor
def inject_user():
    """Make current user available in all templates."""
    return dict(current_user=get_current_user_dict())

# Create tables
with app.app_context():
    try:
        db.create_all()
        log.info("Database tables created successfully")

        # Verify tables exist
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        log.info("Existing tables: %s", tables)

        # ── Self-healing schema patch ──
        # db.create_all() only creates MISSING tables, it never adds columns
        # to tables that already exist. Since 'users' and 'linked_banks'
        # already existed before the verification fields were added below,
        # this runs on every boot and adds any columns that are missing —
        # so this can't silently drift out of sync with model.py again,
        # regardless of which machine/DB the app is actually running against.
        _VERIFICATION_COLUMNS = {
            "users": [
                ("is_foreign_user", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("kyc_status", "VARCHAR(20) NOT NULL DEFAULT 'unverified'"),
                ("kyc_provider", "VARCHAR(20) DEFAULT 'persona'"),
                ("kyc_inquiry_id", "VARCHAR(100)"),
                ("kyc_verified_name", "VARCHAR(200)"),
                ("kyc_verified_at", "TIMESTAMP"),
            ],
            "linked_banks": [
                ("match_score", "FLOAT"),
                ("flagged_for_review", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("reviewed_by", "INTEGER"),
                ("reviewed_at", "TIMESTAMP"),
                ("review_notes", "TEXT"),
            ],
        }
        for _table, _cols in _VERIFICATION_COLUMNS.items():
            if _table not in tables:
                continue  # db.create_all() will have made it fresh with all columns already
            _existing_cols = {c["name"] for c in inspector.get_columns(_table)}
            for _col_name, _col_def in _cols:
                if _col_name in _existing_cols:
                    continue
                try:
                    db.session.execute(db.text(f"ALTER TABLE {_table} ADD COLUMN {_col_name} {_col_def}"))
                    db.session.commit()
                    log.info("schema patch: added %s.%s", _table, _col_name)
                except Exception as _patch_err:
                    db.session.rollback()
                    log.error("schema patch: failed to add %s.%s: %s", _table, _col_name, _patch_err)

    except Exception as e:
        log.error("Failed to create database tables: %s", e, exc_info=True)

if os.environ.get("WERKZEUG_RUN_MAIN") != "false":
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: persist_market_snapshot(200), 'interval', seconds=60, id='market_persist')
    scheduler.start()

POPULAR_NETWORKS = ["bitcoin", "ethereum", "bnb", "solana", "usdt_trc20", "xrp", "ada", "doge", "dot", "trx"]

# Complete network list (add more as needed)
NETWORKS = {
    "bitcoin": {
        "label":        "Bitcoin",
        "symbol":       "BTC",
        "address_re":   re.compile(r"^(1|3|bc1)[a-zA-HJ-NP-Z0-9]{25,62}$"),
        "address_hint": "Starts with 1, 3, or bc1 — 26 to 62 characters",
        "min_amount":   0.00001,
        "fee":          2.50,
    },
    "ethereum": {
        "label":        "Ethereum",
        "symbol":       "ETH",
        "address_re":   re.compile(r"^0x[a-fA-F0-9]{40}$"),
        "address_hint": "Starts with 0x — 42 characters total",
        "min_amount":   0.0001,
        "fee":          1.80,
    },
    "bnb": {
        "label":        "BNB Smart Chain",
        "symbol":       "BNB",
        "address_re":   re.compile(r"^0x[a-fA-F0-9]{40}$"),
        "address_hint": "Starts with 0x — 42 characters total (BEP-20)",
        "min_amount":   0.001,
        "fee":          0.20,
    },
    "solana": {
        "label":        "Solana",
        "symbol":       "SOL",
        "address_re":   re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"),
        "address_hint": "Base58 — 32 to 44 characters",
        "min_amount":   0.001,
        "fee":          0.00025,
    },
    "usdt_trc20": {
        "label":        "Tether (TRC-20)",
        "symbol":       "USDT",
        "address_re":   re.compile(r"^T[a-zA-Z0-9]{33}$"),
        "address_hint": "Starts with T — 34 characters",
        "min_amount":   1.0,
        "fee":          1.00,
    },
    "xrp": {
        "label":        "XRP Ledger",
        "symbol":       "XRP",
        "address_re":   re.compile(r"^r[a-zA-Z0-9]{24,34}$"),
        "address_hint": "Starts with r — 25 to 35 characters",
        "min_amount":   0.1,
        "fee":          0.01,
    },
    "ada": {
        "label":        "Cardano",
        "symbol":       "ADA",
        "address_re":   re.compile(r"^addr1[a-z0-9]{58,}$"),
        "address_hint": "Starts with addr1 — Shelley address",
        "min_amount":   1.0,
        "fee":          0.17,
    },
    "doge": {
        "label":        "Dogecoin",
        "symbol":       "DOGE",
        "address_re":   re.compile(r"^D[5-9A-HJ-NP-U][1-9A-HJ-NP-Za-km-z]{32}$"),
        "address_hint": "Starts with D — 34 characters",
        "min_amount":   1.0,
        "fee":          0.50,
    },
    "dot": {
        "label":        "Polkadot",
        "symbol":       "DOT",
        "address_re":   re.compile(r"^1[0-9a-zA-Z]{46,47}$"),
        "address_hint": "Starts with 1 — 47-48 characters",
        "min_amount":   0.1,
        "fee":          0.01,
    },
    "trx": {
        "label":        "Tron",
        "symbol":       "TRX",
        "address_re":   re.compile(r"^T[a-zA-Z0-9]{33}$"),
        "address_hint": "Starts with T — 34 characters",
        "min_amount":   1.0,
        "fee":          0.10,
    },
    "ltc": {
        "label":        "Litecoin",
        "symbol":       "LTC",
        "address_re":   re.compile(r"^(L|M|ltc1)[a-zA-HJ-NP-Z0-9]{26,42}$"),
        "address_hint": "Starts with L, M, or ltc1",
        "min_amount":   0.001,
        "fee":          0.05,
    },
    "link": {
        "label":        "Chainlink",
        "symbol":       "LINK",
        "address_re":   re.compile(r"^0x[a-fA-F0-9]{40}$"),
        "address_hint": "ERC-20 — starts with 0x",
        "min_amount":   0.1,
        "fee":          1.50,
    },
    "matic": {
        "label":        "Polygon",
        "symbol":       "MATIC",
        "address_re":   re.compile(r"^0x[a-fA-F0-9]{40}$"),
        "address_hint": "Polygon network — starts with 0x",
        "min_amount":   1.0,
        "fee":          0.01,
    },
    "avax": {
        "label":        "Avalanche",
        "symbol":       "AVAX",
        "address_re":   re.compile(r"^(X-avax1|C-avax1|0x)[a-zA-Z0-9]{38,42}$"),
        "address_hint": "X-Chain or C-Chain address",
        "min_amount":   0.01,
        "fee":          0.001,
    },
    "atom": {
        "label":        "Cosmos",
        "symbol":       "ATOM",
        "address_re":   re.compile(r"^cosmos1[a-z0-9]{38}$"),
        "address_hint": "Starts with cosmos1 — 45 characters",
        "min_amount":   0.01,
        "fee":          0.01,
    },
}

NETWORK_INFO = {
    "BTC": {
        "name":     "Bitcoin",
        "symbol":   "BTC",
        "network":  "Bitcoin Mainnet",
        "explorer": "https://blockchair.com/bitcoin/address/",
    },
    "ETH": {
        "name":     "Ethereum",
        "symbol":   "ETH",
        "network":  "Ethereum Mainnet (ERC-20)",
        "explorer": "https://etherscan.io/address/",
    },
    "BNB": {
        "name":     "BNB Smart Chain",
        "symbol":   "BNB",
        "network":  "BSC Mainnet (BEP-20)",
        "explorer": "https://bscscan.com/address/",
    },
    "SOL": {
        "name":     "Solana",
        "symbol":   "SOL",
        "network":  "Solana Mainnet",
        "explorer": "https://explorer.solana.com/address/",
    },
    "USDT": {
        "name":     "Tether",
        "symbol":   "USDT",
        "network":  "Tron (TRC-20)",
        "explorer": "https://tronscan.org/#/address/",
    },
    "XRP": {
        "name":     "XRP",
        "symbol":   "XRP",
        "network":  "XRP Ledger",
        "explorer": "https://xrpscan.com/account/",
    },
    "ADA": {
        "name":     "Cardano",
        "symbol":   "ADA",
        "network":  "Cardano Mainnet",
        "explorer": "https://cardanoscan.io/address/",
    },
    "DOGE": {
        "name":     "Dogecoin",
        "symbol":   "DOGE",
        "network":  "Dogecoin Mainnet",
        "explorer": "https://dogechain.info/address/",
    },
    "DOT": {
        "name":     "Polkadot",
        "symbol":   "DOT",
        "network":  "Polkadot Relay Chain",
        "explorer": "https://polkadot.subscan.io/account/",
    },
    "TRX": {
        "name":     "Tron",
        "symbol":   "TRX",
        "network":  "Tron Mainnet",
        "explorer": "https://tronscan.org/#/address/",
    },
}

# ══════════════════════════════════════════════
#  SUPPORTED COUNTRIES + CURRENCIES
# ══════════════════════════════════════════════

COUNTRIES = {
    "NG": {"name": "Nigeria",        "currency": "NGN", "symbol": "₦"},
    "US": {"name": "United States",  "currency": "USD", "symbol": "$"},
    "GB": {"name": "United Kingdom", "currency": "GBP", "symbol": "£"},
    "CAD": {"name": "Canada",         "currency": "CAD", "symbol": "$"},
    "KE": {"name": "Kenya",          "currency": "KES", "symbol": "KSh"},
    "GH": {"name": "Ghana",          "currency": "GHS", "symbol": "₵"},
    "ZA": {"name": "South Africa",   "currency": "ZAR", "symbol": "R"},
    "IN": {"name": "India",          "currency": "INR", "symbol": "₹"},
    "PH": {"name": "Philippines",    "currency": "PHP", "symbol": "₱"},
    "ID": {"name": "Indonesia",      "currency": "IDR", "symbol": "Rp"},
    "BR": {"name": "Brazil",         "currency": "BRL", "symbol": "R$"},
    "MX": {"name": "Mexico",         "currency": "MXN", "symbol": "$"},
}
# Mock exchange rates (USDT → local currency)
EXCHANGE_RATES = {
    "NGN": 1650.00,
    "USD": 1.00,
    "GBP": 0.79,
    "CAD": 1.35,
    "KES": 129.50,
    "GHS": 15.80,
    "ZAR": 18.45,
    "INR": 83.20,
    "PHP": 56.30,
    "IDR": 15780.00,
    "BRL": 5.02,
    "MXN": 17.15,
    "USDT": 1.00,
    "BTC": 78000,
    "ETH": 3200,
    "BNB": 600
}
# ══════════════════════════════════════════════
#  LOCAL BANKS DATABASE (per country)
# ══════════════════════════════════════════════

LOCAL_BANKS = {
    "NG": [
        {"code": "044", "name": "Access Bank"},
        {"code": "063", "name": "Access Bank (Diamond)"},
        {"code": "050", "name": "Ecobank Nigeria"},
        {"code": "070", "name": "Fidelity Bank"},
        {"code": "011", "name": "First Bank of Nigeria"},
        {"code": "214", "name": "First City Monument Bank"},
        {"code": "058", "name": "Guaranty Trust Bank"},
        {"code": "030", "name": "Heritage Bank"},
        {"code": "301", "name": "Jaiz Bank"},
        {"code": "082", "name": "Keystone Bank"},
        {"code": "526", "name": "Parallex Bank"},
        {"code": "076", "name": "Polaris Bank"},
        {"code": "101", "name": "Providus Bank"},
        {"code": "221", "name": "Stanbic IBTC Bank"},
        {"code": "068", "name": "Standard Chartered Bank"},
        {"code": "232", "name": "Sterling Bank"},
        {"code": "100", "name": "Suntrust Bank"},
        {"code": "032", "name": "Union Bank of Nigeria"},
        {"code": "033", "name": "United Bank For Africa"},
        {"code": "215", "name": "Unity Bank"},
        {"code": "035", "name": "Wema Bank"},
        {"code": "057", "name": "Zenith Bank"},
    ],
    "KE": [
        {"code": "KE01", "name": "KCB Bank Kenya"},
        {"code": "KE02", "name": "Equity Bank"},
        {"code": "KE03", "name": "Co-operative Bank of Kenya"},
        {"code": "KE04", "name": "Standard Chartered Kenya"},
        {"code": "KE05", "name": "Barclays Bank Kenya (Absa)"},
        {"code": "KE06", "name": "Stanbic Bank Kenya"},
        {"code": "KE07", "name": "Diamond Trust Bank"},
    ],
    "GH": [
        {"code": "GH01", "name": "GCB Bank Limited"},
        {"code": "GH02", "name": "Ecobank Ghana"},
        {"code": "GH03", "name": "Stanbic Bank Ghana"},
        {"code": "GH04", "name": "Standard Chartered Bank Ghana"},
        {"code": "GH05", "name": "Zenith Bank Ghana"},
        {"code": "GH06", "name": "Fidelity Bank Ghana"},
    ],
    "ZA": [
        {"code": "ZA01", "name": "Standard Bank"},
        {"code": "ZA02", "name": "First National Bank (FNB)"},
        {"code": "ZA03", "name": "ABSA Bank"},
        {"code": "ZA04", "name": "Nedbank"},
        {"code": "ZA05", "name": "Capitec Bank"},
    ],
    # Add more countries as needed
}

# These run once when Flask starts and warn you immediately
# if any key is missing before a single request is made.
if not app.config["API_KEY"]:
    log.warning("STARTUP: NEWS_API_KEY is not set — news feed will not work.")
else:
    log.info("STARTUP: NEWS_API_KEY loaded OK.")

if not CMC_PRO_API_KEY:
    log.warning("STARTUP: CMC_PRO_API_KEY is not set — market data will not work.")
else:
    log.info("STARTUP: CMC_PRO_API_KEY loaded OK.")


# ── in-memory cache ───────────────────────────
_market_cache: list[dict] = []
_last_fetch:   float      = 0.0
CACHE_TTL = 60  # seconds


fernet = Fernet(ENCRYPTION_KEY.encode()) if ENCRYPTION_KEY else None

if not ENCRYPTION_KEY:
    log.warning("STARTUP: ENCRYPTION_KEY not set — user API key storage will not work.")
else:
    log.info("STARTUP: ENCRYPTION_KEY loaded OK.")

log.info("STARTUP: PAPER_TRADE mode is %s.", "ON" if PAPER_TRADE else "OFF — REAL ORDERS ACTIVE")


# ── DB-backed helpers (replaces previous in-memory mocks) ──
# These helpers read balances, addresses and USDT balance from the database
# using the SQLAlchemy models defined in `model.py`.

# ══════════════════════════════════════════════
#  VALIDATION HELPERS
# ══════════════════════════════════════════════
def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """
    Hash password with SHA-256 + salt.
    Returns (hash, salt).
    """
    if not salt:
        salt = secrets.token_hex(16)

    pw_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return pw_hash, salt

def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify password against stored hash."""
    computed_hash, _ = hash_password(password, salt)
    return computed_hash == stored_hash

def validate_email(email: str) -> str | None:
    """
    Validate email format.
    """
    email = email.strip()

    if not email:
        return "Email is required."

    if len(email) > 54:
        return "Email is too long."

    # Basic regex pattern
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return "Invalid email format."

    return None


def validate_password(password: str) -> str | None:
    """
    Validate password strength.
    Returns error message if invalid, None if valid.
    """
    if not password:
        return "Password is required."

    if len(password) < 6:
        return "Password must be at least 6 characters long."

    if len(password) > 128:
        return "Password is too long."

    # Check for at least one letter and one number
    if not re.search(r'[a-zA-Z]', password):
        return "Password must contain at least one letter."

    if not re.search(r'[0-9]', password):
        return "Password must contain at least one number."

    return None


def validate_full_name(name: str) -> str | None:
    """
    Validate full name.
    Returns error message if invalid, None if valid.
    """
    name = name.strip()

    if not name:
        return "Full name is required."

    if len(name) < 2:
        return "Full name must be at least 2 characters."

    if len(name) > 100:
        return "Full name is too long."

    # Only letters, spaces, hyphens, apostrophes
    if not re.match(r"^[a-zA-Z\s'-]+$", name):
        return "Full name can only contain letters, spaces, hyphens, and apostrophes."

    return None

def email_exists(email: str) -> bool:
    """Check if email is already registered."""
    return User.query.filter_by(email=email.lower()).first() is not None

# ══════════════════════════════════════════════
#  LOGIN REQUIRED DECORATOR
# ══════════════════════════════════════════════

def login_required(f):
    """Decorator to protect routes requiring authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        is_api_call = request.path.startswith('/api/') or 'application/json' in (request.headers.get('Accept') or '')

        log.debug("login_required check - endpoint: %s, user_id in session: %s",
                  request.endpoint, bool(user_id))

        if not user_id:
            log.warning("auth: unauthorized access attempt to %s - session: %s",
                       request.endpoint, dict(session))
            if is_api_call:
                return jsonify({"error": "Session expired. Please log in again.", "error_type": "auth_required"}), 401
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))

        # Verify user still exists in database
        user = User.query.filter_by(user_id=user_id).first()
        if not user:
            log.warning("auth: session user_id not found in database - clearing session")
            session.clear()
            if is_api_call:
                return jsonify({"error": "Session expired. Please log in again.", "error_type": "auth_required"}), 401
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))

        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        is_api_call = request.path.startswith('/api/') or 'application/json' in (request.headers.get('Accept') or '')

        if 'user_id' not in session:
            if is_api_call:
                return jsonify({"error": "Session expired. Please log in again.", "error_type": "auth_required"}), 401
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))
        user = User.query.filter_by(user_id=session['user_id']).first()
        if not user or user.role != 'admin':
            if is_api_call:
                return jsonify({"error": "Admin access required"}), 403
            flash('Admin access required', 'warning')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def require_role(role):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                return jsonify({"error": "Unauthorized"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

def get_current_user() -> User | None:
    """Get the currently logged-in user object from session."""
    user_id = session.get('user_id')

    log.debug("get_current_user - user_id from session: %s", user_id)

    if not user_id:
        return None

    user = User.query.filter_by(user_id=user_id).first()

    if not user:
        log.warning("get_current_user - user_id %s not found in database", user_id)
        session.clear()
        return None

    return user

def get_current_user_dict() -> dict | None:
    """Get current user as dictionary for template compatibility."""
    user = get_current_user()

    if not user:
        return None

    return {
        'user_id': user.user_id,
        'email': user.email,
        'full_name': user.full_name,
        'role': user.role,
        'is_active': user.is_active,
    }

# For demo purposes only — replace with real balance lookup in production

def get_user_balance(symbol: str) -> float:
    """Return the currently logged-in user's balance for *symbol* from DB."""
    user = get_current_user()
    if not user:
        return 0.0
    wallet = Wallet.query.filter_by(user_id=user.id, symbol=symbol.upper()).first()
    return float(wallet.balance) if wallet else 0.0

# ══════════════════════════════════════════════
#  HELPER — encrypt / decrypt user exchange keys
# ══════════════════════════════════════════════

def encrypt_key(plain: str) -> str:
    if not fernet:
        raise RuntimeError("ENCRYPTION_KEY is not set.")
    return fernet.encrypt(plain.encode()).decode()


def decrypt_key(token: str) -> str:
    if not fernet:
        raise RuntimeError("ENCRYPTION_KEY is not set.")
    return fernet.decrypt(token.encode()).decode()

# ══════════════════════════════════════════════
#  MARKET DATA
# ══════════════════════════════════════════════

def get_market_data(limit: int = 200) -> tuple[list[dict], str | None]:
    """
    Fetch live crypto listings from CoinMarketCap.
    Cached for CACHE_TTL seconds.

    Returns:
        (data, error_message)
        data          — list of coin dicts (empty list on failure)
        error_message — human-readable string if something went wrong,
                        None if successful
    """
    global _market_cache, _last_fetch

    # return cache if still fresh
    now = time.time()
    if _market_cache and (now - _last_fetch) < CACHE_TTL:
        log.info("market_data: returning cached data (%d coins)", len(_market_cache))
        return _market_cache, None

    # guard: missing API key
    if not CMC_PRO_API_KEY:
        # Try to return the last saved market snapshot from DB if available
        db_rows = MarketData.query.order_by(MarketData.last_updated.desc()).limit(limit).all()
        if db_rows:
            pairs = []
            for coin in db_rows:
                pairs.append({
                    "rank": coin.rank or 0,
                    "symbol": coin.symbol or "",
                    "name": coin.name or "",
                    "pair": f"{coin.symbol}/USDT",
                    "price": coin.price or 0.0,
                    "change_24h": coin.change_24h or 0.0,
                    "volume_24h": coin.volume_24h or 0.0,
                    "market_cap": coin.market_cap or 0.0,
                })
            _market_cache = pairs
            _last_fetch = time.time()
            return pairs, None

        msg = "CMC_PRO_API_KEY environment variable is not set."
        log.error("market_data: %s", msg)
        return [], msg

    headers = {
        "Accepts":           "application/json",
        "X-CMC_PRO_API_KEY": CMC_PRO_API_KEY,
    }
    params = {
        "start":   1,
        "limit":   limit,
        "convert": "USD",
        "sort":    "market_cap",
    }

    try:
        log.info("market_data: fetching %d coins from CoinMarketCap…", limit)
        response = requests.get(
            CRYPTO_MARKET_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        raw = response.json()

        # CMC wraps errors inside a 200 response sometimes
        status = raw.get("status", {})
        if status.get("error_code", 0) != 0:
            msg = f"CMC API error {status['error_code']}: {status.get('error_message','unknown')}"
            log.error("market_data: %s", msg)
            return [], msg

        coins = raw.get("data", [])
        if not coins:
            msg = "CMC API returned an empty data list."
            log.warning("market_data: %s", msg)
            return [], msg

        pairs = []
        for coin in coins:
            quote = coin.get("quote", {}).get("USD", {})
            pairs.append({
                "rank":       coin.get("cmc_rank", 0),
                "symbol":     coin.get("symbol", ""),
                "name":       coin.get("name", ""),
                "pair":       f"{coin.get('symbol', '')}/USDT",
                "price":      quote.get("price", 0.0),
                "change_24h": quote.get("percent_change_24h", 0.0),
                "volume_24h": quote.get("volume_24h", 0.0),
                "market_cap": quote.get("market_cap", 0.0),
            })

        _market_cache = pairs
        _last_fetch   = now
        log.info("market_data: successfully loaded %d coins.", len(pairs))
        return pairs, None

    except requests.exceptions.ConnectionError:
        msg = "Network error: could not reach CoinMarketCap. Check your internet connection."
        log.error("market_data: %s", msg)
        return [], msg

    except requests.exceptions.Timeout:
        msg = "Request timed out while contacting CoinMarketCap (10 s limit)."
        log.error("market_data: %s", msg)
        return [], msg

    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "?"
        if status_code == 401:
            msg = "CMC API key is invalid or unauthorised (HTTP 401)."
        elif status_code == 429:
            msg = "CMC API rate limit exceeded (HTTP 429). Try again in a minute."
        elif status_code == 500:
            msg = "CoinMarketCap server error (HTTP 500). Try again later."
        else:
            msg = f"CMC API returned HTTP {status_code}."
        log.error("market_data: %s", msg)
        return [], msg

    except (ValueError, KeyError) as exc:
        msg = f"Unexpected response format from CMC API: {exc}"
        log.error("market_data: %s", msg, exc_info=True)
        return [], msg

    except Exception as exc:
        msg = f"Unexpected error fetching market data: {exc}"
        log.error("market_data: %s", msg, exc_info=True)
        return [], msg


def persist_market_snapshot(limit: int = 200):
    """Fetch market data and persist into MarketData table."""
    try:
        pairs, err = get_market_data(limit=limit)
        if err:
            log.warning("persist_market_snapshot: fetch failed: %s", err)
            return

        with app.app_context():
            for p in pairs:
                sym = (p.get('symbol') or '').upper()
                if not sym:
                    continue
                md = MarketData.query.filter_by(symbol=sym).first()
                if not md:
                    md = MarketData(symbol=sym)
                    db.session.add(md)
                md.name = p.get('name')
                md.rank = p.get('rank')
                md.price = p.get('price')
                md.change_24h = p.get('change_24h')
                md.volume_24h = p.get('volume_24h')
                md.market_cap = p.get('market_cap')
                md.last_updated = datetime.utcnow()

            db.session.commit()
            log.info("persist_market_snapshot: saved %d market rows", len(pairs))

    except Exception as e:
        db.session.rollback()
        log.error("persist_market_snapshot failed: %s", e, exc_info=True)

# ══════════════════════════════════════════════
#  CHART DATA  — proxies Binance public klines
#  No API key required for this endpoint.
# ══════════════════════════════════════════════

def get_candles(symbol: str,
                interval: str = "1h",
                limit: int = 200) -> tuple[list, str | None]:
    """
    Fetch OHLCV candles from Binance for *symbol* (e.g. BTCUSDT).

    Returns:
        (candles, error)
        candles — list of {time, open, high, low, close, volume}
        error   — string on failure, None on success
    """
    allowed_intervals = {"1m","3m","5m","15m","30m",
                         "1h","2h","4h","6h","12h",
                         "1d","3d","1w","1M"}
    if interval not in allowed_intervals:
        return [], f"Invalid interval '{interval}'."

    # Binance symbol must be uppercase with no slash
    symbol = symbol.upper().replace("/", "").replace("-", "")

    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    try:
        log.info("candles: fetching %s %s (limit=%d)", symbol, interval, limit)
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        # Binance returns a list, not a dict — if it's a dict it's an error
        if isinstance(raw, dict):
            code = raw.get("code", "?")
            msg  = raw.get("msg", "unknown error")
            err  = f"Binance error {code}: {msg}"
            log.error("candles: %s", err)
            return [], err

        candles = []
        for k in raw:
            candles.append({
                "time":   int(k[0]) // 1000,   # ms → seconds for Lightweight Charts
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            })

        log.info("candles: loaded %d candles for %s.", len(candles), symbol)
        return candles, None

    except requests.exceptions.ConnectionError:
        msg = "Network error: could not reach Binance."
        log.error("candles: %s", msg)
        return [], msg

    except requests.exceptions.Timeout:
        msg = "Binance request timed out."
        log.error("candles: %s", msg)
        return [], msg

    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response else "?"
        if code == 400:
            msg = f"Binance rejected the request — check symbol name (HTTP 400)."
        elif code == 429:
            msg = "Binance rate limit hit (HTTP 429). Slow down requests."
        else:
            msg = f"Binance returned HTTP {code}."
        log.error("candles: %s", msg)
        return [], msg

    except Exception as exc:
        msg = f"Unexpected error fetching candles: {exc}"
        log.error("candles: %s", msg, exc_info=True)
        return [], msg

# ══════════════════════════════════════════════
#  NEWS DATA
# ══════════════════════════════════════════════

def get_crypto_news() -> tuple[list[dict], str | None]:
    """
    Fetch latest crypto news from NewsAPI.

    Returns:
        (articles, error_message)
        articles      — list of article dicts (empty list on failure)
        error_message — human-readable string if something went wrong,
                        None if successful
    """
    api_key = app.config.get("API_KEY")

    # guard: missing API key
    if not api_key:
        msg = "NEWS_API_KEY environment variable is not set."
        log.error("news: %s", msg)
        return [], msg

    params = {
        "q":        "crypto",
        "language": "en",
        "sortBy":   "publishedAt",
        "pageSize": 15,
        "apiKey":   api_key,
    }

    try:
        log.info("news: fetching latest crypto headlines…")
        response = requests.get(NEWS_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # NewsAPI wraps errors inside 200 responses
        if data.get("status") != "ok":
            code = data.get("code", "unknown")
            msg  = f"NewsAPI error [{code}]: {data.get('message', 'unknown error')}"
            log.error("news: %s", msg)
            return [], msg

        articles_raw = data.get("articles", [])
        if not articles_raw:
            msg = "NewsAPI returned zero articles."
            log.warning("news: %s", msg)
            return [], msg

        articles = []
        for article in articles_raw:
            title = article.get("title", "").strip()
            # NewsAPI sometimes returns "[Removed]" placeholders
            if not title or title == "[Removed]":
                continue
            articles.append({
                "title":     title,
                "source":    article.get("source", {}).get("name", "Unknown"),
                "url":       article.get("url", "#"),
                "image":     article.get("urlToImage") or "",
                "published": (article.get("publishedAt") or "")[:10],
            })

        if not articles:
            msg = "All retrieved articles were filtered out (placeholder content)."
            log.warning("news: %s", msg)
            return [], msg

        log.info("news: loaded %d articles.", len(articles))
        return articles, None

    except requests.exceptions.ConnectionError:
        msg = "Network error: could not reach NewsAPI. Check your internet connection."
        log.error("news: %s", msg)
        return [], msg

    except requests.exceptions.Timeout:
        msg = "Request timed out while contacting NewsAPI (10 s limit)."
        log.error("news: %s", msg)
        return [], msg

    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "?"
        if status_code == 401:
            msg = "NewsAPI key is invalid or unauthorised (HTTP 401)."
        elif status_code == 429:
            msg = "NewsAPI rate limit exceeded (HTTP 429). Try again later."
        elif status_code == 426:
            msg = "NewsAPI requires a paid plan for this endpoint (HTTP 426)."
        elif status_code == 500:
            msg = "NewsAPI server error (HTTP 500). Try again later."
        else:
            msg = f"NewsAPI returned HTTP {status_code}."
        log.error("news: %s", msg)
        return [], msg

    except (ValueError, KeyError) as exc:
        msg = f"Unexpected response format from NewsAPI: {exc}"
        log.error("news: %s", msg, exc_info=True)
        return [], msg

    except Exception as exc:
        msg = f"Unexpected error fetching news: {exc}"
        log.error("news: %s", msg, exc_info=True)
        return [], msg

# ══════════════════════════════════════════════
#  JSON API ENDPOINTS
# ══════════════════════════════════════════════

@app.route('/api/market-data')
def api_market_data():
    """
    Returns full market dataset as JSON.
    Called by market.html on page load via fetch().
    On error returns { "error": "<message>" } with HTTP 503.
    """
    data, err = get_market_data(limit=200)
    if err:
        log.error("GET /api/market-data failed: %s", err)
        return jsonify({"error": err}), 503
    return jsonify(data)


@app.route('/api/news')
def api_news():
    """
    Returns latest news as JSON.
    On error returns { "error": "<message>" } with HTTP 503.
    """
    articles, err = get_crypto_news()
    if err:
        log.error("GET /api/news failed: %s", err)
        return jsonify({"error": err}), 503
    return jsonify(articles)



# ══════════════════════════════════════════════
#  TRADE EXECUTION  — Binance signed order
# ══════════════════════════════════════════════

def place_market_order(api_key: str,
                       api_secret: str,
                       symbol: str,
                       side: str,
                       quote_amount: float) -> tuple[dict, str | None]:
    """
    Place a market order on Binance.

    Args:
        api_key      — user's Binance API key (decrypted)
        api_secret   — user's Binance API secret (decrypted)
        symbol       — e.g. "BTCUSDT"
        side         — "BUY" or "SELL"
        quote_amount — amount in USDT to spend / receive

    Returns:
        (order_result, error)
    """
    if PAPER_TRADE:
        # Simulate the order — no real request sent
        fake_order = {
            "orderId":          f"PAPER-{int(time.time()*1000)}",
            "symbol":           symbol,
            "side":             side,
            "type":             "MARKET",
            "quoteOrderQty":    quote_amount,
            "status":           "FILLED (simulated)",
            "paper":            True,
        }
        log.info("trade: PAPER order — %s %s %.2f USDT", side, symbol, quote_amount)
        return fake_order, None

    # ── Real order ────────────────────────────
    if side not in ("BUY", "SELL"):
        return {}, "Side must be BUY or SELL."

    if quote_amount <= 0:
        return {}, "Amount must be greater than zero."

    symbol = symbol.upper().replace("/", "")
    endpoint = f"{BINANCE_BASE}/api/v3/order"
    timestamp = int(time.time() * 1000)

    params = {
        "symbol":        symbol,
        "side":          side,
        "type":          "MARKET",
        "quoteOrderQty": round(quote_amount, 2),
        "timestamp":     timestamp,
    }

    # Binance requires HMAC-SHA256 signature
    query_string = urllib.parse.urlencode(params)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    params["signature"] = signature

    headers = {"X-MBX-APIKEY": api_key}

    try:
        log.info("trade: placing REAL %s %s %.2f USDT", side, symbol, quote_amount)
        resp = requests.post(endpoint, headers=headers,
                             params=params, timeout=10)
        resp.raise_for_status()
        order = resp.json()

        if "code" in order:
            msg = f"Binance order error {order['code']}: {order.get('msg','unknown')}"
            log.error("trade: %s", msg)
            return {}, msg

        log.info("trade: order placed OK — ID %s", order.get("orderId"))
        return order, None

    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response else "?"
        body = {}
        try:
            body = exc.response.json()
        except Exception:
            pass
        if code == 401:
            msg = "Binance API key invalid or not authorised (HTTP 401)."
        elif code == 400:
            msg = f"Bad order parameters: {body.get('msg', 'HTTP 400')}."
        elif code == 429:
            msg = "Binance rate limit exceeded (HTTP 429)."
        else:
            msg = f"Binance order HTTP {code}: {body.get('msg', '')}."
        log.error("trade: %s", msg)
        return {}, msg

    except Exception as exc:
        msg = f"Unexpected error placing order: {exc}"
        log.error("trade: %s", msg, exc_info=True)
        return {}, msg


# ══════════════════════════════════════════════
#  NEW API ROUTES — add to your existing routes
# ══════════════════════════════════════════════

@app.route('/api/candles/<symbol>')
def api_candles(symbol):
    """
    GET /api/candles/BTCUSDT?interval=1h&limit=200
    Returns OHLCV candle data for the chart modal.
    """
    interval = request.args.get("interval", "1h")
    limit    = min(int(request.args.get("limit", 200)), 1000)
    candles, err = get_candles(symbol, interval, limit)
    if err:
        return jsonify({"error": err}), 503
    return jsonify(candles)


@app.route('/api/keys', methods=['POST'])
def api_save_keys():
    """
    POST /api/keys  { api_key, api_secret }
    Encrypts and stores the user's Binance keys in their session.
    """
    if not fernet:
        return jsonify({"error": "Server encryption key not configured."}), 503

    data       = request.get_json(force=True)
    api_key    = (data.get("api_key")    or "").strip()
    api_secret = (data.get("api_secret") or "").strip()

    if not api_key or not api_secret:
        return jsonify({"error": "Both api_key and api_secret are required."}), 400

    try:
        session["enc_api_key"]    = encrypt_key(api_key)
        session["enc_api_secret"] = encrypt_key(api_secret)
        log.info("keys: user stored Binance keys in session.")
        return jsonify({"status": "saved"})
    except Exception as exc:
        msg = f"Failed to encrypt keys: {exc}"
        log.error("keys: %s", msg)
        return jsonify({"error": msg}), 500


@app.route('/api/trade', methods=['POST'])
def api_trade():
    """
    POST /api/trade  { symbol, side, amount }
    Places a market order. Requires keys saved via /api/keys.
    In PAPER_TRADE mode, simulates the order without hitting the exchange.
    """
    user = get_current_user()
    if not user:
        return jsonify({"error": "Please login to place trades."}), 401

    data   = request.get_json(force=True)
    symbol = (data.get("symbol") or "").strip().upper().replace("/", "")
    side   = (data.get("side")   or "").strip().upper()
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number."}), 400

    if not symbol:
        return jsonify({"error": "symbol is required."}), 400
    if side not in ("BUY", "SELL"):
        return jsonify({"error": "side must be BUY or SELL."}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than zero."}), 400

    # In paper mode we don't need real keys
    if PAPER_TRADE:
        order, err = place_market_order("", "", symbol, side, amount)
        if err:
            return jsonify({"error": err}), 503
    else:
        # Real mode — retrieve and decrypt stored keys
        enc_key    = session.get("enc_api_key")
        enc_secret = session.get("enc_api_secret")

        if not enc_key or not enc_secret:
            return jsonify({
                "error": "No exchange keys found. Please add your Binance API keys in Settings."
            }), 401

        try:
            api_key    = decrypt_key(enc_key)
            api_secret = decrypt_key(enc_secret)
        except Exception as exc:
            msg = f"Failed to decrypt stored keys: {exc}"
            log.error("trade: %s", msg)
            return jsonify({"error": msg}), 500

        order, err = place_market_order(api_key, api_secret, symbol, side, amount)
        if err:
            return jsonify({"error": err}), 503

    # Record trade activity for the current user
    try:
        pair = symbol
        if symbol.endswith('USDT'):
            pair = f"{symbol[:-4]}/USDT"
        else:
            pair = symbol.replace('USDT', '/USDT')

        entry_price = 0.0
        if isinstance(order, dict):
            entry_price = float(
                order.get('avgPrice') or
                (order.get('fills', [{}])[0].get('price') if order.get('fills') else None) or
                order.get('price') or
                0.0
            )

        trade = Trade(
            user_id=user.id,
            pair=pair,
            side=side,
            amount=amount,
            entry_price=entry_price,
            current_price=entry_price,
            status='open',
            pnl=0.0,
        )
        db.session.add(trade)

        tx = Transaction(
            tx_id=secrets.token_hex(32),
            user_id=user.id,
            type='trade',
            symbol=symbol,
            amount=amount,
            status='completed',
            notes=f"{side} {pair} market order",
        )
        db.session.add(tx)
        db.session.commit()
        log.info("trade: recorded trade activity for user %s, pair=%s, amount=%s", user.email, pair, amount)
    except Exception as exc:
        db.session.rollback()
        log.error("trade: failed to save trade activity: %s", exc, exc_info=True)
        return jsonify({"error": "Trade completed but failed to record activity."}), 500

    return jsonify(order)

# ══════════════════════════════════════════════
#  VALIDATION HELPERS
# ══════════════════════════════════════════════

def validate_address(network_key: str, address: str) -> str | None:
    """
    Returns an error string if the address is invalid,
    or None if it passes.
    """
    address = address.strip()
    net     = NETWORKS.get(network_key)

    if not net:
        return "Unknown network selected."
    if not address:
        return "Wallet address is required."
    if len(address) > 128:
        return "Address is too long."
    # only allow printable ASCII — reject unicode lookalike attacks
    if not address.isascii() or not address.isprintable():
        return "Address contains invalid characters."
    if not net["address_re"].match(address):
        return (
            f"Invalid {net['label']} address format. "
            f"{net['address_hint']}."
        )
    return None


def validate_amount(network_key: str,
                    amount_str: str,
                    balance: float) -> tuple[float | None, str | None]:
    """
    Parse and validate the send amount.

    Returns (amount_float, None) on success,
            (None, error_string) on failure.
    """
    net = NETWORKS.get(network_key)
    if not net:
        return None, "Unknown network."

    # strip anything that isn't a digit or decimal point
    cleaned = re.sub(r"[^\d.]", "", amount_str.strip())

    if not cleaned:
        return None, "Amount is required."

    # reject multiple decimal points
    if cleaned.count(".") > 1:
        return None, "Amount must be a valid number."

    try:
        amount = float(cleaned)
    except ValueError:
        return None, "Amount must be a valid number."

    if amount <= 0:
        return None, "Amount must be greater than zero."

    if amount < net["min_amount"]:
        return None, (
            f"Minimum send amount for {net['label']} is "
            f"{net['min_amount']} {net['symbol']}."
        )

    total_needed = amount + net["fee"] / 100   # rough fee buffer
    if balance <= 0:
        return None, "insufficient_balance"

    if amount > balance:
        return None, "insufficient_balance"

    return amount, None

def get_user_address(symbol: str) -> str | None:
    """Return an address for *symbol*.

    The *symbol* parameter may be either a ticker (e.g. 'BTC') or a
    network key (e.g. 'bitcoin'). Priority:
      1. If the logged-in user has a personal `Wallet` for the ticker, return it.
      2. Look for a `WalletHash` entry keyed by network key (lowercase).
      3. Look for a `WalletHash` entry keyed by ticker (upper/lower).
    """
    user = get_current_user()

    # 1) prefer user's own wallet by ticker
    if user:
        wallet = Wallet.query.filter_by(user_id=user.id, symbol=(symbol or '').upper()).first()
        if wallet and wallet.address:
            return wallet.address

    # 2) try WalletHash using network key (lowercase)
    try:
        net_key = (symbol or '').lower()
        wh = WalletHash.query.filter_by(symbol=net_key).first()
        if wh and wh.address:
            return wh.address
    except Exception:
        pass

    # 3) try WalletHash using ticker (upper/lower)
    try:
        wh = WalletHash.query.filter_by(symbol=(symbol or '').upper()).first()
        if wh and wh.address:
            return wh.address
    except Exception:
        pass

    # 4) try to resolve ticker -> network key and check again
    for k, v in NETWORKS.items():
        if v.get('symbol','').upper() == (symbol or '').upper():
            wh = WalletHash.query.filter_by(symbol=k).first()
            if wh and wh.address:
                return wh.address

    return None


def generate_qr_code(address: str) -> str:
    """
    Generate a QR code for *address* and return it
    as a base64-encoded PNG data URI.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(address)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#00d4aa", back_color="#161921")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    return f"data:image/png;base64,{b64}"

# ══════════════════════════════════════════════
#  ACCOUNT NAME RESOLUTION (mock)
# ══════════════════════════════════════════════

def resolve_account_name(country_code: str, bank_code: str, account_number: str) -> str | None:
    """
    Resolve account number to account holder name.
    In production, call the bank's API (e.g., Paystack, Flutterwave for NG).
    Returns account name or None if not found.
    """
    # Mock implementation — always return a fake name for demo
    if not account_number or len(account_number) < 10:
        return None

    # Simulate API delay
    time.sleep(0.3)

    # Generate a deterministic fake name based on account number hash
    hash_val = int(hashlib.md5(account_number.encode()).hexdigest(), 16)
    first_names = ["John", "Jane", "Michael", "Sarah", "David", "Mary", "James", "Patricia"]
    last_names  = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]

    first = first_names[hash_val % len(first_names)]
    last  = last_names[(hash_val // 100) % len(last_names)]

    return f"{first} {last}"

# ══════════════════════════════════════════════
#  NAME MATCHING (local bank name vs registered name,
#  and international account name vs KYC-verified name)
# ══════════════════════════════════════════════
import difflib

# Tune these after watching real traffic land in the admin review queue
NAME_MATCH_AUTO_APPROVE = 85   # >= this: auto-verify
NAME_MATCH_FLAG_REVIEW  = 60   # between this and AUTO_APPROVE: admin review queue
# below NAME_MATCH_FLAG_REVIEW: reject outright, user re-checks details

_TITLE_PATTERN = re.compile(
    r"\b(mr|mrs|miss|ms|dr|engr|barr|chief|alhaji|alhaja|prof|otunba|hon)\.?\b",
    re.IGNORECASE,
)

def normalize_name(name: str) -> str:
    """Strips titles/punctuation, sorts tokens — so word order doesn't matter."""
    if not name:
        return ""
    name = _TITLE_PATTERN.sub("", name)
    name = re.sub(r"[^a-zA-Z\s]", "", name)
    return " ".join(sorted(name.lower().split()))

def name_match_score(name_a: str, name_b: str) -> float:
    """Token-order-independent similarity, 0-100."""
    a, b = normalize_name(name_a), normalize_name(name_b)
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio() * 100, 1)

def classify_match(score: float) -> str:
    if score >= NAME_MATCH_AUTO_APPROVE:
        return "verified"
    if score >= NAME_MATCH_FLAG_REVIEW:
        return "flagged"
    return "rejected"

# ══════════════════════════════════════════════
#  KYC IDENTITY VERIFICATION (Persona) — required
#  before a foreign/international user can link a bank
# ══════════════════════════════════════════════
PERSONA_API_KEY        = os.getenv('PERSONA_API_KEY')
PERSONA_TEMPLATE_ID    = os.getenv('PERSONA_TEMPLATE_ID')
PERSONA_WEBHOOK_SECRET = os.getenv('PERSONA_WEBHOOK_SECRET')
PERSONA_BASE_URL       = "https://api.withpersona.com/api/v1"

def persona_create_inquiry(user_id: str) -> dict:
    """Starts a hosted Persona KYC inquiry. Returns {inquiry_id, hosted_url}."""
    if not PERSONA_API_KEY or not PERSONA_TEMPLATE_ID:
        raise RuntimeError("PERSONA_API_KEY / PERSONA_TEMPLATE_ID not configured")

    resp = requests.post(
        f"{PERSONA_BASE_URL}/inquiries",
        headers={
            "Authorization": f"Bearer {PERSONA_API_KEY}",
            "Content-Type": "application/json",
            "Persona-Version": "2023-01-05",
        },
        json={"data": {"attributes": {
            "inquiry-template-id": PERSONA_TEMPLATE_ID,
            "reference-id": user_id,
        }}},
        timeout=15,
    )
    resp.raise_for_status()
    inquiry = resp.json()["data"]
    return {
        "inquiry_id": inquiry["id"],
        "hosted_url": f"https://withpersona.com/verify?inquiry-id={inquiry['id']}",
    }

def persona_verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """Persona signs webhooks as: t=<timestamp>,v1=<hmac>"""
    if not PERSONA_WEBHOOK_SECRET or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        timestamp, provided_sig = parts.get("t"), parts.get("v1")
        if not timestamp or not provided_sig:
            return False
        signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}"
        expected_sig = hmac.new(
            PERSONA_WEBHOOK_SECRET.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_sig, provided_sig)
    except Exception:
        return False

def persona_parse_webhook(payload: dict) -> dict:
    """Maps a Persona webhook payload to {inquiry_id, reference_id, kyc_status, full_name}."""
    inquiry = payload.get("data", {}).get("attributes", {}).get("payload", {}).get("data", {})
    attrs = inquiry.get("attributes", {})
    status = attrs.get("status")

    if status == "approved":
        kyc_status = "verified"
    elif status in ("declined", "expired"):
        kyc_status = "rejected"
    elif status == "needs_review":
        kyc_status = "flagged"
    else:
        kyc_status = "pending"

    first = attrs.get("name-first", "") or ""
    last = attrs.get("name-last", "") or ""
    full_name = f"{first} {last}".strip() or None

    return {
        "inquiry_id": inquiry.get("id"),
        "reference_id": attrs.get("reference-id"),
        "kyc_status": kyc_status,
        "full_name": full_name,
    }

# ══════════════════════════════════════════════
#  MOCK WALLET BALANCE
# ══════════════════════════════════════════════
def get_usdt_balance() -> float:
    """Return user's USDT balance from DB."""
    user = get_current_user()
    if not user:
        return 0.0
    wallet = Wallet.query.filter_by(user_id=user.id, symbol='USDT').first()
    return float(wallet.balance) if wallet else 0.0

# ══════════════════════════════════════════════
#  PAGE ROUTES
# ══════════════════════════════════════════════
@app.before_request
def maybe_show_coming_soon():
    # Allow static files, API, admin endpoints and health checks to bypass coming soon
    path = (request.path or "").lower()
    allowed_prefixes = ('/static', '/api', '/admin/coming-soon', '/admin', '/login', '/register', '/logout', '/forgot-password', '/reset-password')
    if any(path.startswith(p) for p in allowed_prefixes):
        return None

    # if enabled, render the coming soon page for normal visitors
    if is_coming_soon_enabled():
        return render_template('coming-soon.html')


@app.route('/admin/coming-soon/toggle', methods=['POST'])
@admin_required
def admin_toggle_coming_soon():
    """Toggle the instance flag file to enable/disable coming-soon mode.

    This toggles the presence of `instance/COMING_SOON`. Admin-only.
    """
    try:
        if COMING_SOON_FLAG.exists():
            COMING_SOON_FLAG.unlink()
            state = False
        else:
            # ensure instance directory exists
            COMING_SOON_FLAG.parent.mkdir(parents=True, exist_ok=True)
            COMING_SOON_FLAG.write_text('on')
            state = True
        return jsonify({"coming_soon": state})
    except Exception as e:
        log.exception("Failed to toggle coming soon flag: %s", e)
        return jsonify({"error": "failed", "detail": str(e)}), 500

@app.route('/admin/coming-soon/state', methods=['GET'])
@admin_required
def admin_coming_soon_state():
    try:
        return jsonify({"coming_soon": bool(is_coming_soon_enabled())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    """Home page."""
    user = get_current_user()

    market_data, market_err = get_market_data(limit=15)
    news, news_err          = get_crypto_news()
    pages = [market_data[i:i+5] for i in range(0, len(market_data), 5)] if market_data else []


    if not user:
        log.info("Home - no user, redirecting to login")
        return redirect(url_for('login'))

    log.info("Home - user %s authenticated", user.email)

    # Calculate total balance in USD — same logic as /api/wallet-summary
    # so home dashboard and portfolio always show the same number
    total_balance = 0.0
    if user.wallets:
        for w in user.wallets:
            if not w.balance:
                continue
            md = (MarketData.query
                  .filter_by(symbol=w.symbol.upper())
                  .order_by(MarketData.last_updated.desc())
                  .first())
            if md and md.price:
                total_balance += float(w.balance) * float(md.price)
            elif w.symbol.upper() in ('USDT', 'USDC', 'DAI', 'BUSD'):
                # stablecoins = 1:1 USD when no market data
                total_balance += float(w.balance)
            # else: unknown price, skip (don't add raw units)

    # Ensure news is a list (sometimes can be None or malformed)
    if not news or not isinstance(news, list):
        news = []
        if not news_err:
            news_err = "Unable to load news at this time."

    return render_template(
        'index.html',
        pages=pages,
        news=news if news else [],
        market_error=market_err,
        news_error=news_err,
        total_balance=total_balance,
        now=datetime.utcnow()
    )

@app.route('/market')
@login_required
def market():
    """Market page."""
    return render_template('market.html', paper_trade=PAPER_TRADE)


@app.route('/send', methods=['GET'])
@login_required
def send():
    """Render the send form with popular + all networks."""
    popular = {k: NETWORKS[k] for k in POPULAR_NETWORKS if k in NETWORKS}
    all_nets = {k: v for k, v in NETWORKS.items() if k not in POPULAR_NETWORKS}
    return render_template('send.html',
                          popular_networks=popular,
                          all_networks=all_nets)

@app.route('/api/balance/<symbol>')
def api_balance(symbol):
    """
    GET /api/balance/BTC
    Returns the user's balance for a given ticker symbol.
    Called by JS when the user changes the network selector.
    """
    symbol  = symbol.upper()
    balance = get_user_balance(symbol)
    log.info("balance: %s = %.8f", symbol, balance)
    return jsonify({"symbol": symbol, "balance": balance})


@app.route('/api/send', methods=['POST'])
@login_required
def api_send():
    """
    POST /api/send
    Body: { network, address, amount }

    Validates all fields server-side and returns:
      { success: true,  tx_id: "...", ... }   on success
      { success: false, error: "...",
        error_type: "insufficient_balance"|"validation"|"network" }
    """
    data    = request.get_json(force=True)
    network = (data.get("network") or "").strip().lower()
    address = (data.get("address") or "").strip()
    amount_raw = str(data.get("amount") or "").strip()

    # ── 1. network ────────────────────────────
    if network not in NETWORKS:
        return jsonify({
            "success":    False,
            "error":      "Please select a valid network.",
            "error_type": "validation",
            "field":      "network",
        }), 400

    net     = NETWORKS[network]
    symbol  = net["symbol"]
    balance = get_user_balance(symbol)

    # ── 2. address ────────────────────────────
    addr_err = validate_address(network, address)
    if addr_err:
        log.warning("send: invalid address for %s — %s", network, addr_err)
        return jsonify({
            "success":    False,
            "error":      addr_err,
            "error_type": "validation",
            "field":      "address",
        }), 400

    # ── 3. amount ─────────────────────────────
    amount, amt_err = validate_amount(network, amount_raw, balance)
    if amt_err == "insufficient_balance":
        log.warning("send: insufficient %s balance (%.8f) for amount %s",
                    symbol, balance, amount_raw)
        return jsonify({
            "success":      False,
            "error_type":   "insufficient_balance",
            "error":        (
                f"Insufficient {symbol} balance. "
                f"You have {balance:.8f} {symbol} available."
            ),
            "balance":      balance,
            "symbol":       symbol,
            "amount_needed": float(amount_raw) if amount_raw else 0,
        }), 422

    if amt_err:
        return jsonify({
            "success":    False,
            "error":      amt_err,
            "error_type": "validation",
            "field":      "amount",
        }), 400

    # Store the send as a pending transaction for admin approval
    user = get_current_user()
    tx_id = secrets.token_hex(32)

    try:
        tx = Transaction(
            tx_id=tx_id,
            user_id=user.id,
            type='send',
            symbol=symbol,
            amount=amount,
            to_address=address,
            status='pending',
            fee=net['fee'],
        )
        db.session.add(tx)
        db.session.commit()

        # Notify user that transaction is pending admin approval
        notif = Notification(
            user_id=user.id,
            title='Send pending',
            message=f'Your send of {amount} {symbol} to {address[:10]}... is pending admin approval.',
            type='info'
        )
        db.session.add(notif)
        db.session.commit()

        log.info("send: stored pending tx %s for user %s", tx_id, user.email)

        return jsonify({
            "success": True,
            "tx_id": tx_id,
            "status": "pending",
            "message": "Send submitted and awaiting admin approval.",
        })

    except Exception as e:
        db.session.rollback()
        log.error("send: failed to save tx: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# Admin: list pending transactions
@app.route('/api/admin/pending-transactions')
@admin_required
def api_admin_pending_transactions():
    txs = Transaction.query.filter(Transaction.status == 'pending').order_by(Transaction.created_at.desc()).all()
    out = []
    for t in txs:
        out.append({
            "id": t.id,
            "tx_id": t.tx_id,
            "user_id": t.user.user_id,
            "user_email": t.user.email,
            "type": t.type,
            "symbol": t.symbol,
            "amount": t.amount,
            "to_address": t.to_address,
            "created_at": t.created_at.isoformat(),
        })
    return jsonify(out)


# Admin: approve or reject a pending transaction
@app.route('/api/admin/tx/<int:tx_id>/action', methods=['POST'])
@admin_required
def api_admin_tx_action(tx_id):
    data   = request.get_json(force=True) or {}
    action = (data.get('action') or '').lower().strip()
    note   = (data.get('note')   or '').strip()

    if action not in ('approve', 'reject'):
        return jsonify({"error": "action must be 'approve' or 'reject'"}), 400

    tx = Transaction.query.filter_by(id=tx_id).first()
    if not tx:
        return jsonify({"error": "Transaction not found"}), 404

    if tx.status != 'pending':
        return jsonify({"error": f"Transaction is already '{tx.status}'"}), 400

    try:
        user   = tx.user
        amount = float(tx.amount or 0)
        symbol = tx.symbol or '—'

        if action == 'approve':
            if tx.type in ('send', 'withdraw'):
                # Deduct from wallet
                wallet = Wallet.query.filter_by(user_id=user.id, symbol=symbol.upper()).first()
                if not wallet or float(wallet.balance or 0) < amount:
                    tx.status       = 'failed'
                    tx.notes        = note or 'Insufficient balance at approval time'
                    tx.completed_at = datetime.utcnow()
                    db.session.commit()
                    n = Notification(
                        user_id = user.id,
                        title   = f'❌ {tx.type.capitalize()} failed',
                        message = (
                            f'Your {tx.type} of {amount} {symbol} could not be processed'
                            f' — insufficient balance.'
                            + (f' Note: {note}' if note else '')
                        ),
                        type = 'error',
                    )
                    db.session.add(n)
                    db.session.commit()
                    return jsonify({"success": False, "error": "Insufficient balance"}), 422

                wallet.balance  = float(wallet.balance) - amount
                tx.status       = 'completed'
                tx.completed_at = datetime.utcnow()
                tx.notes        = note or f'Approved by admin'

                label = 'Withdrawal' if tx.type == 'withdraw' else 'Transfer'
                n = Notification(
                    user_id = user.id,
                    title   = f'✅ {label} approved',
                    message = (
                        f'Your {tx.type} of {amount} {symbol} has been approved and processed.'
                        + (f' Note: {note}' if note else '')
                    ),
                    type = 'success',
                )

            elif tx.type in ('receive', 'deposit'):
                # Receive / deposit — credit wallet
                wallet = Wallet.query.filter_by(user_id=user.id, symbol=symbol.upper()).first()
                if not wallet:
                    wallet = Wallet(
                        user_id = user.id,
                        symbol  = symbol.upper(),
                        address = secrets.token_hex(20),
                        balance = 0.0,
                    )
                    db.session.add(wallet)
                wallet.balance  = float(wallet.balance or 0) + amount
                tx.status       = 'completed'
                tx.completed_at = datetime.utcnow()
                tx.notes        = note or 'Approved by admin'
                n = Notification(
                    user_id = user.id,
                    title   = '✅ Deposit confirmed',
                    message = (
                        f'Your deposit of {amount} {symbol} has been confirmed and credited to your wallet.'
                        + (f' Note: {note}' if note else '')
                    ),
                    type = 'success',
                )

            else:
                # Generic fallback
                tx.status       = 'completed'
                tx.completed_at = datetime.utcnow()
                tx.notes        = note or 'Approved'
                n = Notification(
                    user_id = user.id,
                    title   = '✅ Transaction approved',
                    message = f'Your transaction of {amount} {symbol} was approved.' + (f' Note: {note}' if note else ''),
                    type    = 'success',
                )

            db.session.add(n)
            db.session.commit()
            log.info("admin: APPROVED tx %s (%s %s %s) for %s", tx.tx_id, tx.type, amount, symbol, user.email)
            return jsonify({"success": True, "status": "completed"})

        else:  # reject
            # If this was a withdrawal, the user's balance was NOT pre-deducted
            # (VaultCore deducts on APPROVE, not on submit) — so no refund needed.
            # If for some reason your flow pre-deducts, add the refund here.
            tx.status       = 'failed'
            tx.notes        = note or 'Rejected by admin'
            tx.completed_at = datetime.utcnow()
            db.session.commit()

            label = 'Withdrawal' if tx.type == 'withdraw' else tx.type.capitalize()
            n = Notification(
                user_id = user.id,
                title   = f'❌ {label} declined',
                message = (
                    f'Your {tx.type} of {amount} {symbol} was declined.'
                    + (f' Reason: {note}' if note else ' Please contact support if you have questions.')
                ),
                type = 'error',
            )
            db.session.add(n)
            db.session.commit()
            log.info("admin: REJECTED tx %s (%s %s %s) for %s", tx.tx_id, tx.type, amount, symbol, user.email)
            return jsonify({"success": True, "status": "failed"})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_tx_action failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/manual-deposit', methods=['POST'])
@admin_required
def api_manual_deposit():
    """
    Admin manually credits a user wallet.
    POST body: { user_email, symbol, amount, note? }

    Flow:
      1. Validate inputs
      2. Find user by email
      3. Create Transaction (type=receive, status=completed)
      4. Credit or create Wallet row
      5. Create Notification for the user
      6. Return { success, tx_id, new_balance }
    """
    data = request.get_json(force=True) or {}

    user_email = (data.get('user_email') or '').strip().lower()
    symbol     = (data.get('symbol')     or '').strip().upper()
    note       = (data.get('note')       or '').strip()

    try:
        amount = float(data.get('amount', 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a valid number"}), 400

    # ── Validate ──────────────────────────────────────────────────
    if not user_email:
        return jsonify({"error": "user_email is required"}), 400
    if not symbol:
        return jsonify({"error": "symbol / network is required"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than zero"}), 400

    # ── Lookup user ───────────────────────────────────────────────
    user = User.query.filter_by(email=user_email).first()
    if not user:
        # try case-insensitive
        user = User.query.filter(User.email.ilike(user_email)).first()
    if not user:
        return jsonify({"error": f"No user found with email: {user_email}"}), 404

    try:
        tx_id = secrets.token_hex(32)

        # ── Transaction ───────────────────────────────────────────
        tx_kwargs = dict(
            tx_id    = tx_id,
            user_id  = user.id,
            type     = 'receive',
            symbol   = symbol,
            amount   = amount,
            status   = 'completed',
            notes    = note or 'Admin manual deposit',
        )
        # completed_at is optional — only set if the column exists
        try:
            tx = Transaction(**tx_kwargs, completed_at=datetime.utcnow())
        except TypeError:
            tx = Transaction(**tx_kwargs)

        db.session.add(tx)

        # ── Wallet — credit or create ─────────────────────────────
        wallet = Wallet.query.filter_by(user_id=user.id, symbol=symbol).first()
        if not wallet:
            wallet = Wallet(
                user_id = user.id,
                symbol  = symbol,
                address = secrets.token_hex(20),
                balance = 0.0,
            )
            db.session.add(wallet)

        prev_balance   = float(wallet.balance or 0)
        wallet.balance = prev_balance + amount

        # ── Notification ──────────────────────────────────────────
        notif_msg = (
            f'{amount} {symbol} has been credited to your wallet.'
            + (f' ({note})' if note else '')
        )
        try:
            n = Notification(
                user_id = user.id,
                title   = '💰 Deposit successful',
                message = notif_msg,
                type    = 'success',
            )
            db.session.add(n)
        except Exception as ne:
            log.warning("Could not create notification: %s", ne)

        db.session.commit()

        log.info(
            "admin: manual deposit — %.6f %s for %s  tx=%s  prev_bal=%.6f  new_bal=%.6f",
            amount, symbol, user_email, tx_id, prev_balance, float(wallet.balance)
        )

        return jsonify({
            "success":     True,
            "tx_id":       tx_id,
            "new_balance": float(wallet.balance),
            "user_email":  user_email,
            "symbol":      symbol,
            "amount":      amount,
        })

    except Exception as e:
        db.session.rollback()
        log.error("api_manual_deposit failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────
#  Also fix api_admin_recent_deposits — filter by notes containing
#  'admin' OR by a dedicated is_manual flag (using notes field)
# ─────────────────────────────────────────────────────────────────

@app.route('/api/admin/recent-deposits')
@admin_required
def api_admin_recent_deposits():
    """Last 50 receive-type transactions, newest first."""
    try:
        txs = (Transaction.query
               .filter(Transaction.type == 'receive')
               .order_by(Transaction.created_at.desc())
               .limit(50)
               .all())

        return jsonify([{
            "id":         t.id,
            "tx_id":      t.tx_id or '',
            "user_email": t.user.email if t.user else '—',
            "symbol":     t.symbol or '',
            "amount":     float(t.amount or 0),
            "notes":      t.notes or '',
            "status":     t.status or '',
            "created_at": t.created_at.isoformat() if t.created_at else '',
        } for t in txs])

    except Exception as e:
        log.error("api_admin_recent_deposits: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────
#  User-facing transaction history  GET /api/transactions
# ─────────────────────────────────────────────────────────────────

@app.route('/api/transactions')
@login_required
def api_user_transactions():
    """
    GET /api/transactions?type=all&limit=50
    Returns the current user's transaction history, newest first.
    """
    user    = get_current_user()
    tx_type = request.args.get('type', 'all')
    limit   = min(int(request.args.get('limit', 50)), 200)

    query = Transaction.query.filter_by(user_id=user.id)
    if tx_type and tx_type != 'all':
        query = query.filter(Transaction.type == tx_type)
    txs = query.order_by(Transaction.created_at.desc()).limit(limit).all()

    return jsonify([{
        "id":           t.id,
        "tx_id":        t.tx_id or '',
        "type":         t.type or '',
        "symbol":       t.symbol or '',
        "amount":       float(t.amount or 0),
        "to_address":   t.to_address if hasattr(t, 'to_address') else None,
        "status":       t.status or '',
        "notes":        t.notes or '',
        "fee":          float(t.fee) if (hasattr(t, 'fee') and t.fee) else 0,
        "created_at":   t.created_at.isoformat() if t.created_at else '',
        "completed_at": (t.completed_at.isoformat()
                         if (hasattr(t, 'completed_at') and t.completed_at)
                         else None),
    } for t in txs])

@app.route('/receive')
@login_required
def receive():
    """Render the network selector page."""
    return render_template('receive.html', networks=NETWORKS)

@app.route('/api/receive/<symbol>')
def api_receive(symbol):
    """
    GET /api/receive/BTC
    Returns the user's deposit address + QR code for the symbol.
    Called by JS when the user selects a network.
    """
    # Accept either a network key (e.g. 'bitcoin') or a ticker (e.g. 'BTC')
    param = symbol
    network_key = None
    ticker = None

    if param.lower() in NETWORKS:
        network_key = param.lower()
        ticker = NETWORKS[network_key].get('symbol', param.upper())
    else:
        # try to find a network by ticker
        for k, v in NETWORKS.items():
            if v.get('symbol','').upper() == param.upper():
                network_key = k
                ticker = v.get('symbol')
                break

    if not ticker:
        ticker = param.upper()

    address = get_user_address(ticker)

    if not address:
        log.warning("receive: no address found for %s (network_key=%s, ticker=%s)", param, network_key, ticker)
        return jsonify({"error": f"No address configured for {ticker}."}), 404

    info = NETWORK_INFO.get(ticker, {})
    qr_data_uri = generate_qr_code(address)

    log.info("receive: generated QR for %s — %s", ticker, address[:12] + "…")

    return jsonify({
        "symbol":   ticker,
        "name":     info.get("name", ticker),
        "network":  info.get("network", ""),
        "address":  address,
        "qr_code":  qr_data_uri,
        "explorer": info.get("explorer", "") + address,
    })

@app.route('/withdraw')
@login_required
def withdraw():
    """Render withdraw page."""
    linked_bank = session.get('linked_bank')  # {bank_code, bank_name, account_last4}
    withdraw_rate = {
        "USDT": 1.0
    }
    return render_template('withdraw.html',
                           exchange_rates=withdraw_rate,
                          countries=COUNTRIES,
                          linked_bank=linked_bank)

@app.route('/api/banks/<country_code>')
def api_banks(country_code):
    """
    GET /api/banks/NG
    Returns list of banks for the given country.
    """
    banks = LOCAL_BANKS.get(country_code.upper(), [])
    return jsonify(banks)

@app.route('/api/resolve-account', methods=['POST'])
def api_resolve_account():
    """
    POST /api/resolve-account
    Body: {country, bank_code, account_number}
    Returns the account holder name if found.
    """
    data = request.get_json(force=True)
    country = data.get("country", "").upper()
    bank_code = data.get("bank_code", "")
    account_number = data.get("account_number", "").strip()

    if not all([country, bank_code, account_number]):
        return jsonify({"error": "Missing required fields."}), 400

    # Validate account number format
    if not re.match(r"^\d{10,12}$", account_number):
        return jsonify({"error": "Account number must be 10-12 digits."}), 400

    # Try external provider (Paystack) if configured
    PAYSTACK_SECRET = os.getenv('PAYSTACK_SECRET')
    if PAYSTACK_SECRET:
        try:
            headers = {"Authorization": f"Bearer {PAYSTACK_SECRET}"}
            resp = requests.get(
                "https://api.paystack.co/bank/resolve",
                params={"account_number": account_number, "bank_code": bank_code},
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get('status') and data.get('data'):
                acct_name = data['data'].get('account_name')
                if acct_name:
                    log.info("resolve (paystack): %s %s → %s", bank_code, account_number, acct_name)
                    return jsonify({"account_name": acct_name})
        except Exception as e:
            log.warning("Paystack resolve failed: %s", e)

    account_name = resolve_account_name(country, bank_code, account_number)

    if not account_name:
        return jsonify({"error": "Could not resolve account. Check details and try again."}), 404

    log.info("resolve: %s %s → %s", bank_code, account_number, account_name)

    return jsonify({"account_name": account_name})

@app.route('/api/paystack/init', methods=['POST'])
@login_required
def api_paystack_init():
    """
    Initialise a Paystack transaction for withdrawal verification.
    Returns { authorization_url, reference } that the frontend
    uses to open the Paystack checkout popup/redirect.

    POST body: { amount_ngn }   (amount in kobo internally)
    """
    user = get_current_user()
    data = request.get_json(force=True) or {}

    amount_ngn = float(data.get('amount_ngn') or 0)
    if amount_ngn <= 0:
        return jsonify({"error": "amount_ngn must be > 0"}), 400

    PAYSTACK_SECRET = os.getenv('PAYSTACK_SECRET_KEY') or os.getenv('PAYSTACK_SECRET')
    if not PAYSTACK_SECRET:
        return jsonify({"error": "Payment gateway not configured"}), 503

    amount_kobo = int(amount_ngn * 100)
    reference   = f"VC-{secrets.token_hex(10).upper()}"

    try:
        resp = requests.post(
            'https://api.paystack.co/transaction/initialize',
            headers={
                'Authorization': f'Bearer {PAYSTACK_SECRET}',
                'Content-Type':  'application/json',
            },
            json={
                'email':     user.email,
                'amount':    amount_kobo,
                'reference': reference,
                'metadata':  {
                    'user_id':    user.id,
                    'user_email': user.email,
                    'purpose':    'withdrawal_verification',
                },
                'callback_url': url_for('paystack_callback', _external=True),
            },
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get('status'):
            log.info("paystack init: ref=%s user=%s amount=%s kobo",
                     reference, user.email, amount_kobo)
            return jsonify({
                "success":           True,
                "authorization_url": body['data']['authorization_url'],
                "reference":         reference,
            })
        return jsonify({"error": body.get('message', 'Paystack error')}), 502
    except Exception as e:
        log.error("paystack init failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/paystack/verify/<reference>')
@login_required
def api_paystack_verify(reference):
    """
    Verify a Paystack payment by reference.
    Called after the popup closes or redirect returns.
    Returns { verified, amount_ngn, channel, bank, account_name }
    """
    PAYSTACK_SECRET = os.getenv('PAYSTACK_SECRET_KEY') or os.getenv('PAYSTACK_SECRET')
    if not PAYSTACK_SECRET:
        return jsonify({"error": "Payment gateway not configured"}), 503
    try:
        resp = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET}'},
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get('status'):
            return jsonify({"verified": False, "error": body.get('message')}), 400

        tx_data    = body['data']
        tx_status  = tx_data.get('status')          # 'success' | 'failed' | 'abandoned'
        amount_ngn = tx_data.get('amount', 0) / 100
        channel    = tx_data.get('channel', '')
        auth       = tx_data.get('authorization', {})

        return jsonify({
            "verified":     tx_status == 'success',
            "status":       tx_status,
            "amount_ngn":   amount_ngn,
            "channel":      channel,
            "bank":         auth.get('bank'),
            "last4":        auth.get('last4'),
            "account_name": tx_data.get('customer', {}).get('first_name', '') + ' '
                            + tx_data.get('customer', {}).get('last_name', ''),
            "reference":    reference,
        })
    except Exception as e:
        log.error("paystack verify failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/paystack/callback')
def paystack_callback():
    """Handles Paystack redirect after payment. Redirects back to withdraw page."""
    reference = request.args.get('reference', '')
    return redirect(url_for('withdraw', reference=reference))


@app.route('/api/paystack/banks')
def api_paystack_banks():
    """
    GET /api/paystack/banks?country=NG
    Returns Paystack's live bank list for a country — replaces LOCAL_BANKS on supported countries.
    """
    country = request.args.get('country', 'NG').upper()
    PAYSTACK_SECRET = os.getenv('PAYSTACK_SECRET_KEY') or os.getenv('PAYSTACK_SECRET')
    if not PAYSTACK_SECRET:
        return jsonify(LOCAL_BANKS.get(country, []))
    try:
        resp = requests.get(
            'https://api.paystack.co/bank',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET}'},
            params={'country': country.lower(), 'per_page': 200},
            timeout=8,
        )
        resp.raise_for_status()
        banks = resp.json().get('data', [])
        return jsonify([{"code": b['code'], "name": b['name']} for b in banks])
    except Exception as e:
        log.warning("paystack banks failed, fallback to local: %s", e)
        return jsonify(LOCAL_BANKS.get(country, []))

@app.route('/api/kyc/start', methods=['POST'])
@login_required
def api_kyc_start():
    """
    POST /api/kyc/start
    Starts a Persona KYC inquiry for the current user (required before they
    can link an international bank account). Returns a hosted URL the
    frontend redirects/opens for the user to complete ID + selfie check.
    """
    user = get_current_user()
    try:
        inquiry = persona_create_inquiry(user_id=str(user.id))
        user.kyc_status = 'pending'
        user.kyc_inquiry_id = inquiry['inquiry_id']
        db.session.commit()
        return jsonify({"hosted_url": inquiry["hosted_url"], "kyc_status": "pending"})
    except Exception as e:
        log.error("api_kyc_start: %s", e, exc_info=True)
        return jsonify({"error": "Could not start identity verification. Please try again shortly."}), 502


@app.route('/api/kyc/status')
@login_required
def api_kyc_status():
    """GET /api/kyc/status — lets the frontend poll after the user completes the hosted flow."""
    user = get_current_user()
    return jsonify({
        "kyc_status": user.kyc_status,
        "kyc_verified_name": user.kyc_verified_name,
    })


@app.route('/webhooks/persona', methods=['POST'])
def persona_webhook():
    """
    Persona calls this when a KYC inquiry completes (approved/declined/needs_review).
    Signature MUST verify — this endpoint is unauthenticated by necessity, so a
    forged webhook is the only way someone could fake a KYC pass without it.
    """
    signature = request.headers.get('Persona-Signature', '')
    raw_body = request.get_data()

    if not persona_verify_webhook_signature(raw_body, signature):
        log.warning("persona_webhook: invalid signature")
        return jsonify({"error": "invalid signature"}), 401

    try:
        result = persona_parse_webhook(request.get_json(force=True))
        reference_id = result.get('reference_id')
        if not reference_id:
            return jsonify({"received": True}), 200

        user = User.query.get(int(reference_id))
        if not user:
            log.warning("persona_webhook: no user for reference_id=%s", reference_id)
            return jsonify({"received": True}), 200

        user.kyc_status = result['kyc_status']
        if result['kyc_status'] == 'verified':
            user.kyc_verified_at = datetime.utcnow()
            if result.get('full_name'):
                user.kyc_verified_name = result['full_name']

        db.session.add(Notification(
            user_id=user.id,
            title='🪪 Identity verification update',
            message=f"Your identity verification is now: {result['kyc_status']}.",
            type='success' if result['kyc_status'] == 'verified' else 'info',
        ))
        db.session.commit()

        log.info("persona_webhook: user=%s kyc_status=%s", user.email, result['kyc_status'])
        return jsonify({"received": True}), 200

    except Exception as e:
        db.session.rollback()
        log.error("persona_webhook: %s", e, exc_info=True)
        return jsonify({"error": "processing failed"}), 500


@app.route('/api/link-bank', methods=['POST'])
@login_required
def api_link_bank():
    """
    POST /api/link-bank
    Saves a bank account to DB for this user — but only after real verification:

    - LOCAL accounts (bank_type='local'): the account_name must have come from
      the Paystack resolve step. We re-score it against the user's registered
      full_name. Strong match -> verified. Partial match -> flagged for admin
      review. Weak/no match -> rejected outright.

    - INTERNATIONAL accounts (bank_type='international'): there's no bank API
      to resolve these against, so we require the user to have passed Persona
      KYC first (kyc_status == 'verified'), then match the typed account_name
      against the KYC-confirmed legal name from the ID check.

    Body: { bank_code, bank_name, account_number, account_name,
            country_code?, bank_type?, iban?, swift_bic? }
    """
    user = get_current_user()
    data = request.get_json(force=True) or {}

    bank_code      = (data.get('bank_code')      or '').strip()
    bank_name      = (data.get('bank_name')      or '').strip()
    account_number = (data.get('account_number') or '').strip()
    account_name   = (data.get('account_name')   or '').strip()
    country_code   = (data.get('country_code')   or 'NG').strip().upper()
    bank_type      = (data.get('bank_type')      or 'local').strip().lower()
    iban           = (data.get('iban')           or '').strip() or None
    swift_bic      = (data.get('swift_bic')      or '').strip() or None

    if not all([bank_code, bank_name, account_number, account_name]):
        return jsonify({"error": "bank_code, bank_name, account_number and account_name are required"}), 400

    is_international = (bank_type == 'international') or (bank_code == 'INTL')

    # ── Gate: international linking requires KYC to be verified first ──
    if is_international:
        if user.kyc_status != 'verified':
            return jsonify({
                "error": "Identity verification required before linking an international account.",
                "error_type": "kyc_required",
                "kyc_status": user.kyc_status,
            }), 403
        reference_name = user.kyc_verified_name or user.full_name
    else:
        reference_name = user.full_name

    # ── Score the account name against the trusted reference name ──
    score = name_match_score(account_name, reference_name)
    verdict = classify_match(score)

    if verdict == "rejected":
        log.info("bank_link REJECTED: user=%s account_name=%r vs reference=%r score=%s",
                  user.email, account_name, reference_name, score)
        return jsonify({
            "error": "The name on this account doesn't match your verified identity closely enough to link automatically. "
                     "Double-check the account details, or contact support if this is your account.",
            "error_type": "name_mismatch",
            "match_score": score,
        }), 422

    is_verified        = (verdict == "verified")
    flagged_for_review = (verdict == "flagged")

    # Check for existing link for same account
    existing = LinkedBank.query.filter_by(
        user_id=user.id,
        bank_code=bank_code,
        account_number=account_number,
    ).first()
    if existing:
        existing.is_enabled         = True
        existing.is_verified        = is_verified
        existing.match_score        = score
        existing.flagged_for_review = flagged_for_review
        existing.updated_at         = datetime.utcnow()
        db.session.commit()
        session['linked_bank'] = {
            'bank_code': existing.bank_code,
            'bank_name': existing.bank_name,
            'account_last4': existing.account_last4,
            'linked_bank_id': existing.id,
        }
        return jsonify({
            "success":            True,
            "status":             verdict,
            "match_score":        score,
            "bank_name":          existing.bank_name,
            "account_last4":      existing.account_last4,
            "linked_bank_id":     existing.id,
            "flagged_for_review": flagged_for_review,
        })

    try:
        lb = LinkedBank(
            user_id            = user.id,
            bank_code          = bank_code,
            bank_name          = bank_name,
            country_code       = country_code,
            account_number     = account_number,
            account_name       = account_name,
            account_last4      = account_number[-4:] if len(account_number) >= 4 else '****',
            iban               = iban,
            swift_bic          = swift_bic,
            bank_type          = bank_type,
            is_enabled         = True,
            is_verified        = is_verified,
            match_score        = score,
            flagged_for_review = flagged_for_review,
        )
        db.session.add(lb)

        # Notify admin — flagged links get a distinct, actionable notice
        admins = User.query.filter_by(role='admin').all()
        for admin in admins:
            if flagged_for_review:
                title = '⚠️ Bank link flagged for review'
                msg   = f'{user.email} linked {bank_name} (****{lb.account_last4}) — name match score {score}. Needs manual review.'
                ntype = 'warning'
            else:
                title = '🏦 New bank linked'
                msg   = f'{user.email} linked {bank_name} (****{lb.account_last4}) — verified, match score {score}.'
                ntype = 'info'
            db.session.add(Notification(user_id=admin.id, title=title, message=msg, type=ntype))

        db.session.commit()

        session['linked_bank'] = {
            'bank_code':      lb.bank_code,
            'bank_name':      lb.bank_name,
            'account_last4':  lb.account_last4,
            'linked_bank_id': lb.id,
        }

        log.info("bank_link: %s linked %s ****%s status=%s score=%s",
                  user.email, bank_name, lb.account_last4, verdict, score)
        return jsonify({
            "success":            True,
            "status":             verdict,
            "match_score":        score,
            "bank_name":          lb.bank_name,
            "account_last4":      lb.account_last4,
            "linked_bank_id":     lb.id,
            "flagged_for_review": flagged_for_review,
        })

    except Exception as e:
        db.session.rollback()
        log.error("api_link_bank: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/unlink-bank', methods=['POST'])
@login_required
def api_unlink_bank():
    """
    POST /api/unlink-bank
    Soft-disables the user's linked bank (keeps DB record for admin).
    """
    user = get_current_user()
    data = request.get_json(force=True) or {}
    lb_id = data.get('linked_bank_id') or session.get('linked_bank', {}).get('linked_bank_id')

    if lb_id:
        lb = LinkedBank.query.filter_by(id=lb_id, user_id=user.id).first()
        if lb:
            lb.is_enabled = False
            db.session.commit()

    session.pop('linked_bank', None)
    return jsonify({"success": True})


@app.route('/api/linked-banks')
@login_required
def api_user_linked_banks():
    """GET — returns all linked banks for the current user."""
    user = get_current_user()
    banks = LinkedBank.query.filter_by(user_id=user.id, is_enabled=True)\
                            .order_by(LinkedBank.created_at.desc()).all()
    return jsonify([b.to_dict() for b in banks])

@app.route('/api/withdraw-to-bank', methods=['POST'])
def api_withdraw_to_bank():
    """
    POST /api/withdraw-to-bank
    Body: {method, currency, amount, bank_code, account_number, account_name}
    Processes withdrawal to bank account.
    """
    data = request.get_json(force=True)
    method = data.get("method")  # "manual" or "linked"
    currency = data.get("currency", "USD")
    amount_local = float(data.get("amount", 0))

    if amount_local <= 0:
        return jsonify({"error": "Amount must be greater than zero."}), 400

    # Convert to USDT
    rate = EXCHANGE_RATES.get(currency, 1.0)
    amount_usdt = amount_local / rate

    balance = get_usdt_balance()
    if amount_usdt > balance:
        return jsonify({
            "error": f"Insufficient balance. You have ${balance:.2f} USDT available.",
            "error_type": "insufficient_balance",
        }), 422

    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    # For manual or linked, record requested bank info in notes
    bank_name = None
    account_ref = None
    if method == "linked":
        linked = session.get('linked_bank')
        if not linked:
            return jsonify({"error": "No bank linked. Please link a bank first."}), 400

        bank_name = linked['bank_name']
        account_ref = f"****{linked['account_last4']}"
    else:
        bank_code = data.get("bank_code", "")
        account_number = data.get("account_number", "")
        account_name = data.get("account_name", "")
        if not all([bank_code, account_number, account_name]):
            return jsonify({"error": "Bank details are incomplete."}), 400
        country = data.get("country", "NG")
        banks = LOCAL_BANKS.get(country, [])
        bank_obj = next((b for b in banks if b['code'] == bank_code), None)
        bank_name = bank_obj['name'] if bank_obj else "Unknown Bank"
        account_ref = f"{account_name} ({account_number[-4:]})"

    try:
        import secrets
        tx = Transaction(
            tx_id=secrets.token_hex(24),
            user_id=user.id,
            type='withdraw',
            symbol='USDT',
            amount=amount_usdt,
            notes=f"bank:{bank_name} ref:{account_ref}",
            status='pending'
        )
        db.session.add(tx)
        db.session.commit()

        # notify user
        n = Notification(user_id=user.id, title='Withdrawal requested', message=f'Your withdrawal of {amount_local} {currency} is pending admin approval.', type='info')
        db.session.add(n)
        db.session.commit()

        return jsonify({
            "success": True,
            "tx_id": tx.tx_id,
            "amount_local": amount_local,
            "amount_usdt": amount_usdt,
            "currency": currency,
            "bank_name": bank_name,
            "account_ref": account_ref,
            "message": "Withdrawal request submitted and awaiting admin approval.",
        })

    except Exception as e:
        db.session.rollback()
        log.error("withdraw: failed to record tx %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/usdt-balance')
def api_usdt_balance():
    """GET /api/usdt-balance — returns available USDT balance."""
    balance = get_usdt_balance()
    return jsonify({"balance": balance})

@app.route('/wallet')
@login_required
def wallet():
    """Render wallet overview page."""
    return render_template('wallet.html')

@app.route('/api/wallet-summary')
def api_wallet_summary():
    """
    GET /api/wallet-summary
    Returns user's wallet balances + open trades.
    """
    user = get_current_user()
    if not user:
        return jsonify({"total_value": 0.0, "holdings": {}, "open_trades": []})

    wallets = Wallet.query.filter_by(user_id=user.id).all()
    holdings = {}
    total_value = 0.0
    for w in wallets:
        if not w.balance or float(w.balance) <= 0:
            continue  # skip zero-balance wallets
        price      = None
        change_24h = 0.0
        md = MarketData.query.filter_by(symbol=w.symbol.upper()).order_by(MarketData.last_updated.desc()).first()
        if md:
            if md.price:
                price = float(md.price)
            if md.change_24h is not None:
                change_24h = float(md.change_24h)
        usd_value = (float(w.balance) * price) if price else 0.0
        total_value += usd_value
        holdings[w.symbol.upper()] = {
            "balance":    float(w.balance),
            "usd_value":  round(usd_value, 2),
            "price":      round(price, 8) if price else 0.0,
            "change_24h": round(change_24h, 4),
        }

    open_trades = []
    trades = Trade.query.filter_by(user_id=user.id).order_by(Trade.opened_at.desc()).limit(50).all()
    for t in trades:
        entry   = float(t.entry_price   or 0)
        current = float(t.current_price or entry)
        pnl     = float(t.pnl           or 0)
        pnl_pct = ((current - entry) / entry * 100) if entry else 0.0
        open_trades.append({
            "id":            t.trade_id,
            "pair":          t.pair,
            "side":          t.side,
            "amount":        float(t.amount or 0),
            "entry_price":   round(entry, 8),
            "current_price": round(current, 8),
            "pnl":           round(pnl, 4),
            "pnl_percent":   round(pnl_pct, 4),
            "status":        t.status,
            "opened_at":     t.opened_at.isoformat() if t.opened_at else "",
        })

    log.info("wallet: loaded %d holdings, total $%.2f", len(holdings), total_value)

    return jsonify({
        "total_value": total_value,
        "holdings": holdings,
        "open_trades": open_trades,
    })

@app.route('/wallet-connect')
@login_required
def wallet_connect():
    """Wallet Connect page — user connects external wallets."""
    user   = get_current_user()
    wallets = ConnectedWallet.query.filter_by(user_id=user.id).order_by(ConnectedWallet.created_at.desc()).all()
    return render_template('wallet_connect.html', connected_wallets=wallets)


@app.route('/api/wallet-connect/add', methods=['POST'])
@login_required
def api_wallet_connect_add():
    """
    User adds a new external wallet connection.
    POST: { wallet_type, address, network, label? }
    """
    user = get_current_user()
    data = request.get_json(force=True) or {}

    wallet_type = (data.get('wallet_type') or '').strip().lower()
    address     = (data.get('address')     or '').strip()
    network     = (data.get('network')     or '').strip().upper()
    label       = (data.get('label')       or '').strip()

    # Wallet type → display name map
    WALLET_NAMES = {
        'metamask':     'MetaMask',
        'trustwallet':  'Trust Wallet',
        'coinbase':     'Coinbase Wallet',
        'walletconnect':'WalletConnect',
        'phantom':      'Phantom',
        'ledger':       'Ledger',
        'trezor':       'Trezor',
        'manual':       'Manual Address',
    }

    if not wallet_type:
        return jsonify({"error": "wallet_type is required"}), 400
    if not address:
        return jsonify({"error": "Wallet address is required"}), 400
    if not network:
        return jsonify({"error": "Network is required"}), 400

    # Basic address length validation
    if len(address) < 10 or len(address) > 200:
        return jsonify({"error": "Invalid wallet address"}), 400

    # Prevent duplicate address+network combos for this user
    existing = ConnectedWallet.query.filter_by(
        user_id=user.id, address=address, network=network
    ).first()
    if existing:
        return jsonify({"error": "This address is already connected on this network"}), 409

    try:
        cw = ConnectedWallet(
            user_id     = user.id,
            wallet_name = WALLET_NAMES.get(wallet_type, wallet_type.title()),
            wallet_type = wallet_type,
            address     = address,
            network     = network,
            label       = label or None,
            is_enabled  = True,
            verified_at = datetime.utcnow(),
        )
        db.session.add(cw)

        # Notify admin
        admins = User.query.filter_by(role='admin').all()
        for admin in admins:
            notif = Notification(
                user_id = admin.id,
                title   = f'🔗 New wallet connected',
                message = f'{user.email} connected a {WALLET_NAMES.get(wallet_type, wallet_type)} wallet on {network}: {address[:16]}…',
                type    = 'info',
            )
            db.session.add(notif)

        db.session.commit()
        log.info("wallet_connect: user %s added %s on %s: %s", user.email, wallet_type, network, address[:16])
        return jsonify({"success": True, "wallet": cw.to_dict()})

    except Exception as e:
        db.session.rollback()
        log.error("api_wallet_connect_add failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/wallet-connect/<int:wallet_id>/remove', methods=['POST'])
@login_required
def api_wallet_connect_remove(wallet_id):
    """User removes one of their connected wallets."""
    user = get_current_user()
    cw   = ConnectedWallet.query.filter_by(id=wallet_id, user_id=user.id).first()
    if not cw:
        return jsonify({"error": "Connected wallet not found"}), 404
    try:
        db.session.delete(cw)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/wallet-connect/list')
@login_required
def api_wallet_connect_list():
    """Return the current user's connected wallets."""
    user    = get_current_user()
    wallets = ConnectedWallet.query.filter_by(user_id=user.id).order_by(ConnectedWallet.created_at.desc()).all()
    return jsonify([w.to_dict() for w in wallets])

@app.route('/register', methods=['GET'])
def register():
    """Render registration page."""
    if get_current_user():
        return redirect(url_for('home'))
    return render_template('register.html')

@app.route('/login', methods=['GET'])
def login():
    """Render login page."""
    if get_current_user():
        return redirect(url_for('home'))
    return render_template('login.html')

# ══════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════

@app.route('/api/register', methods=['POST'])
def api_register():
    """POST /api/register - Create new user account."""

    try:
        data = request.get_json(force=True)
    except Exception as e:
        log.error("Failed to parse registration JSON: %s", e)
        return jsonify({"error": "Invalid request format"}), 400

    email     = data.get("email", "").strip().lower()
    password  = data.get("password", "").strip()
    full_name = data.get("full_name", "").strip()

    log.info("Registration attempt for email: %s", email)

    # Validate email
    email_err = validate_email(email)
    if email_err:
        log.warning("Email validation failed: %s", email_err)
        return jsonify({"error": email_err, "field": "email"}), 400

    # Check if email already exists
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        log.warning("Registration attempt with existing email: %s", email)
        return jsonify({
            "error": "This email is already registered. Please login instead.",
            "field": "email",
            "redirect": url_for('login'),
        }), 409

    # Validate password
    password_err = validate_password(password)
    if password_err:
        log.warning("Password validation failed: %s", password_err)
        return jsonify({"error": password_err, "field": "password"}), 400

    # Validate full name
    name_err = validate_full_name(full_name)
    if name_err:
        log.warning("Name validation failed: %s", name_err)
        return jsonify({"error": name_err, "field": "full_name"}), 400

    # Create user
    try:
        pw_hash, salt = hash_password(password)

        # Check if this is the first user or admin email
        user_count = User.query.count()
        is_admin = (user_count == 0) or (email == 'admin@vaultcore.com')

        log.info("Creating user - is_admin: %s, user_count: %d", is_admin, user_count)

        user = User(
            email=email,
            password_hash=pw_hash,
            salt=salt,
            full_name=full_name,
            role='admin' if is_admin else 'user'
        )

        db.session.add(user)
        db.session.flush()  # Get user.id

        log.info("User created with id: %d, user_id: %s", user.id, user.user_id)

        # Create default settings
        settings = UserSettings(user_id=user.id)
        db.session.add(settings)

        db.session.commit()

        log.info("Settings created for user: %d", user.id)

        # Log user in
        session.clear()
        session['user_id']    = user.user_id
        session['email']      = user.email
        session['full_name']  = user.full_name
        session['role']       = user.role
        session.permanent     = True

        log.info("Session created - user_id: %s, session: %s", user.user_id, dict(session))

        response_data = {
            "success": True,
            "message": "Account created successfully!",
            "redirect": url_for('home'),
            "user": {
                "email":     user.email,
                "full_name": user.full_name,
                "role":      user.role,
            }
        }

        log.info("Registration successful for: %s (role: %s)", email, user.role)

        return jsonify(response_data), 200

    except Exception as e:
        db.session.rollback()
        log.error("Registration failed with exception: %s", e, exc_info=True)
        return jsonify({"error": f"Failed to create account: {str(e)}"}), 500

@app.route('/api/login', methods=['POST'])
def api_login():
    """POST /api/login - Authenticate user."""
    data = request.get_json(force=True, silent=True)

    if not data:
        return jsonify({"error": "Invalid request — expected JSON body.", "field": "email"}), 400

    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()

    # Validate email format
    email_err = validate_email(email)
    if email_err:
        return jsonify({"error": email_err, "field": "email"}), 400

    # Find user
    user = User.query.filter_by(email=email).first()

    if not user or not verify_password(password, user.password_hash, user.salt):
        return jsonify({
            "error": "Invalid email or password.",
            "field": "password"
        }), 401

    if not user.is_active:
        return jsonify({
            "error": "Your account has been deactivated. Contact support.",
            "field": "email"
        }), 403

    # Create session
    session.clear()
    session['user_id']   = user.user_id
    session['email']     = user.email
    session['full_name'] = user.full_name
    session['role']      = user.role
    session.permanent    = True

    log.info("auth: login success — %s (role: %s) - session: %s", email, user.role, dict(session))

    # Admins go to admin dashboard, regular users go to home
    redirect_url = url_for('admin_dashboard') if user.role == 'admin' else url_for('home')

    return jsonify({
        "success": True,
        "message": "Login successful!",
        "redirect": redirect_url,
        "user": {
            "email":     user.email,
            "full_name": user.full_name,
            "role":      user.role,
        }
    })

@app.route('/logout')
def logout():
    """Logout user and clear session."""
    email = session.get('email', 'unknown')
    session.clear()
    log.info("auth: logout — %s", email)
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))


@app.route('/api/current-user')
def api_current_user():
    """GET /api/current-user - Returns currently logged-in user."""
    user_dict = get_current_user_dict()

    if not user_dict:
        return jsonify({"user": None})

    return jsonify({
        "user": {
            "email":     user_dict['email'],
            "full_name": user_dict['full_name'],
            "role":      user_dict['role'],
        }
    })


@app.route('/settings')
@login_required
def settings():
    """Render settings page."""
    user = get_current_user()
    return render_template('settings.html', role=user.role, networks=NETWORKS)


@app.route('/admin')
@admin_required
def admin_dashboard():
    return render_template('admin_dashboard.html')


@app.route('/api/settings/profile', methods=['GET', 'POST'])
@login_required
def api_profile():
    """Get or update profile."""
    user = get_current_user()

    if request.method == 'GET':
        return jsonify({
            "user_id":   user.user_id,
            "email":     user.email,
            "full_name": user.full_name,
        })

    # POST -> update profile fields
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    full_name = (data.get('full_name') or '').strip()

    # validation
    err = validate_email(email)
    if err:
        return jsonify({"error": err}), 400
    err = validate_full_name(full_name)
    if err:
        return jsonify({"error": err}), 400

    # check email uniqueness if changed
    if email != user.email and User.query.filter_by(email=email).first():
        return jsonify({"error": "Email is already in use."}), 400

    try:
        user.email = email
        user.full_name = full_name
        user.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True, "email": user.email, "full_name": user.full_name})
    except Exception as e:
        db.session.rollback()
        log.error("api_profile update failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to update profile."}), 500


@app.route('/api/settings/profile/photo', methods=['POST'])
@login_required
def api_profile_photo():
    """Upload profile photo for current user. Saves to static/images/avatars/<user_id>.<ext>"""
    user = get_current_user()
    if 'photo' not in request.files:
        return jsonify({"error": "No file provided."}), 400

    f = request.files['photo']
    if f.filename == '':
        return jsonify({"error": "Empty filename."}), 400

    filename = secure_filename(f.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {'.png', '.jpg', '.jpeg', '.gif'}:
        return jsonify({"error": "Unsupported image format."}), 400

    out_dir = _base_dir / 'static' / 'images' / 'avatars'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{user.user_id}{ext}"
    try:
        f.save(out_path)
        url = url_for('static', filename=f'images/avatars/{out_path.name}')
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        log.error("api_profile_photo save failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to save photo."}), 500


@app.route('/api/settings/password', methods=['POST'])
@login_required
def api_change_password():
    """Change user password. Expects JSON: {current_password, new_password}"""
    user = get_current_user()
    data = request.get_json() or {}
    current = data.get('current_password', '')
    new_pw = data.get('new_password', '')

    if not verify_password(current, user.password_hash, user.salt):
        return jsonify({"error": "Current password incorrect."}), 400

    err = validate_password(new_pw)
    if err:
        return jsonify({"error": err}), 400

    try:
        pw_hash, salt = hash_password(new_pw)
        user.password_hash = pw_hash
        user.salt = salt
        user.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        log.error("api_change_password failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to change password."}), 500


@app.route('/api/settings/2fa', methods=['POST'])
@login_required
def api_2fa():
    """2FA management. JSON {action: 'create'|'confirm'|'disable', code?, secret?}
    'create' -> generates secret and returns `secret` and `otpauth_url`.
    'confirm' -> verifies code and enables 2FA.
    'disable' -> disables 2FA when correct code provided.
    """
    user = get_current_user()
    settings = user.settings or UserSettings(user_id=user.id)
    data = request.get_json() or {}
    action = data.get('action')

    try:
        if action == 'create':
            secret = pyotp.random_base32()
            totp = pyotp.TOTP(secret)
            otpauth = totp.provisioning_uri(name=user.email, issuer_name='VaultCore Wallet')
            # store secret temporarily (not enabled until confirm)
            settings.two_fa_secret = secret
            db.session.add(settings)
            db.session.commit()
            return jsonify({"secret": secret, "otpauth_url": otpauth})

        if action == 'confirm':
            code = str(data.get('code',''))
            if not settings.two_fa_secret:
                return jsonify({"error": "No 2FA setup in progress."}), 400
            totp = pyotp.TOTP(settings.two_fa_secret)
            if not totp.verify(code):
                return jsonify({"error": "Invalid 2FA code."}), 400
            settings.two_fa_enabled = True
            db.session.add(settings)
            db.session.commit()
            return jsonify({"ok": True})

        if action == 'disable':
            code = str(data.get('code',''))
            if not settings.two_fa_enabled:
                return jsonify({"error": "2FA is not enabled."}), 400
            totp = pyotp.TOTP(settings.two_fa_secret)
            if not totp.verify(code):
                return jsonify({"error": "Invalid 2FA code."}), 400
            settings.two_fa_enabled = False
            settings.two_fa_secret = None
            db.session.add(settings)
            db.session.commit()
            return jsonify({"ok": True})

        return jsonify({"error": "Invalid action."}), 400
    except Exception as e:
        db.session.rollback()
        log.error("api_2fa error: %s", e, exc_info=True)
        return jsonify({"error": "2FA operation failed."}), 500


@app.route('/api/admin/wallet-hashes', methods=['GET'])
@admin_required
def api_admin_wallet_hashes():
    """Return all configured wallet hashes (admin only)."""
    try:
        rows = WalletHash.query.order_by(WalletHash.symbol).all()
        out = []
        for r in rows:
            out.append({
                "symbol": (r.symbol or '').lower(),
                "address": r.address,
                "note": r.note,
            })
        return jsonify(out)
    except Exception as e:
        log.error("api_admin_wallet_hashes failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to load wallet hashes."}), 500

@app.route('/api/settings/notifications')
@login_required
def api_notifications():
    """Get user notifications."""
    user = get_current_user()

    notifications = Notification.query.filter_by(user_id=user.id)\
        .order_by(Notification.created_at.desc())\
        .limit(50)\
        .all()

    return jsonify([{
        "id": n.id,
        "title": n.title,
        "message": n.message,
        "type": n.type,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
    } for n in notifications])


@app.route('/api/settings/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def api_mark_read(notif_id):
    """Mark notification as read."""
    user = get_current_user()

    notif = Notification.query.filter_by(id=notif_id, user_id=user.id).first()

    if not notif:
        return jsonify({"error": "Not found"}), 404

    try:
        notif.is_read = True
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/notifications/clear', methods=['POST'])
@login_required
def api_notifications_clear():
    """Delete all notifications for the current user."""
    user = get_current_user()
    try:
        Notification.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        log.error("api_notifications_clear: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/users')
@admin_required
def api_admin_users():
    """Get all users (admin only)."""
    users = User.query.order_by(User.created_at.desc()).all()

    return jsonify([{
        "id":         u.id,
        "user_id":    u.user_id,
        "email":      u.email,
        "full_name":  u.full_name,
        "role":       u.role,
        "is_active":  u.is_active,
        "created_at": u.created_at.isoformat(),
    } for u in users])


@app.route('/api/admin/user/<int:user_id>/activity')
@admin_required
def api_admin_user_activity(user_id):
    """Return activity dashboard for a single user."""
    user = User.query.get_or_404(user_id)

    transactions = Transaction.query.filter_by(user_id=user.id).order_by(Transaction.created_at.desc()).limit(100).all()
    trades = Trade.query.filter_by(user_id=user.id).order_by(Trade.opened_at.desc()).limit(100).all()

    return jsonify({
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
        },
        "transactions": [{
            "tx_id": tx.tx_id,
            "type": tx.type,
            "symbol": tx.symbol,
            "amount": tx.amount,
            "status": tx.status,
            "notes": tx.notes,
            "created_at": tx.created_at.isoformat(),
            "completed_at": tx.completed_at.isoformat() if tx.completed_at else None,
        } for tx in transactions],
        "trades": [{
            "trade_id": t.trade_id,
            "pair": t.pair,
            "side": t.side,
            "amount": t.amount,
            "entry_price": t.entry_price,
            "current_price": t.current_price,
            "status": t.status,
            "pnl": t.pnl,
            "opened_at": t.opened_at.isoformat(),
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        } for t in trades],
    })


@app.route('/api/admin/user/<int:user_id>/toggle-active', methods=['POST'])
@admin_required
def api_admin_toggle_user_active(user_id):
    """Toggle a user's active state. Admin only."""
    current = User.query.filter_by(user_id=session.get('user_id')).first()
    user = User.query.get_or_404(user_id)

    if current and current.id == user.id:
        return jsonify({"error": "You cannot deactivate your own account here."}), 403
    if user.role == 'admin':
        return jsonify({"error": "This endpoint cannot modify another admin account."}), 403

    user.is_active = not user.is_active
    try:
        db.session.commit()
        return jsonify({"success": True, "is_active": user.is_active})
    except Exception as exc:
        db.session.rollback()
        log.error("admin: toggle user active failed: %s", exc, exc_info=True)
        return jsonify({"error": "Could not update user status."}), 500

@app.route('/api/admin/linked-banks')
@admin_required
def api_admin_linked_banks():
    """Admin: list all users' linked bank accounts."""
    try:
        banks = LinkedBank.query.order_by(LinkedBank.created_at.desc()).all()
        return jsonify([{
            **b.to_dict(),
            'user_email': b.user.email     if b.user else '—',
            'user_name':  b.user.full_name if b.user else '—',
        } for b in banks])
    except Exception as e:
        log.error("api_admin_linked_banks: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/linked-banks/<int:bank_id>/review', methods=['POST'])
@admin_required
def api_admin_review_linked_bank(bank_id):
    """
    Admin approves or rejects a flagged (partial name-match) linked bank.
    Body: { decision: 'approve' | 'reject', notes?: str }
    """
    admin = get_current_user()
    lb = LinkedBank.query.get(bank_id)
    if not lb:
        return jsonify({"error": "Linked bank not found"}), 404

    data = request.get_json(force=True) or {}
    decision = (data.get('decision') or '').strip().lower()
    notes = (data.get('notes') or '').strip()

    if decision not in ('approve', 'reject'):
        return jsonify({"error": "decision must be 'approve' or 'reject'"}), 400

    try:
        lb.flagged_for_review = False
        lb.reviewed_by = admin.id
        lb.reviewed_at = datetime.utcnow()
        lb.review_notes = notes or None

        if decision == 'approve':
            lb.is_verified = True
            lb.is_enabled = True
        else:
            lb.is_verified = False
            lb.is_enabled = False

        db.session.add(Notification(
            user_id=lb.user_id,
            title='🏦 Bank link review complete',
            message=f'Your linked bank {lb.bank_name} (****{lb.account_last4}) was {decision}d by admin.'
                    + (f' Note: {notes}' if notes else ''),
            type='success' if decision == 'approve' else 'warning',
        ))
        db.session.commit()

        log.info("admin_review_lb: bank=%s decision=%s admin=%s", bank_id, decision, admin.email)
        return jsonify({"success": True, "decision": decision, "is_verified": lb.is_verified})
    except Exception as e:
        db.session.rollback()
        log.error("api_admin_review_linked_bank: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/linked-banks/<int:bank_id>/toggle', methods=['POST'])
@admin_required
def api_admin_toggle_linked_bank(bank_id):
    """Admin enables or disables a user's linked bank."""
    lb = LinkedBank.query.get(bank_id)
    if not lb:
        return jsonify({"error": "Linked bank not found"}), 404
    try:
        lb.is_enabled = not lb.is_enabled
        db.session.commit()
        word = 'enabled' if lb.is_enabled else 'disabled'
        db.session.add(Notification(
            user_id = lb.user_id,
            title   = f'🏦 Linked bank {word}',
            message = f'Your linked bank {lb.bank_name} (****{lb.account_last4}) has been {word} by admin.',
            type    = 'info' if lb.is_enabled else 'warning',
        ))
        db.session.commit()
        return jsonify({"success": True, "is_enabled": lb.is_enabled})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/linked-banks/<int:bank_id>/send', methods=['POST'])
@admin_required
def api_admin_lb_send(bank_id):
    """
    Admin initiates a withdrawal FROM a user's linked bank account.
    POST: { amount, currency?, note? }
    Creates a pending Transaction (type=withdraw) and notifies user.
    """
    lb = LinkedBank.query.get(bank_id)
    if not lb:
        return jsonify({"error": "Linked bank not found"}), 404
    if not lb.is_enabled:
        return jsonify({"error": "Linked bank is disabled"}), 400

    data     = request.get_json(force=True) or {}
    currency = (data.get('currency') or 'USDT').strip().upper()
    note     = (data.get('note')     or '').strip()
    try:
        amount = float(data.get('amount', 0) or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount"}), 400

    if amount <= 0:
        return jsonify({"error": "amount must be greater than zero"}), 400

    try:
        tx = Transaction(
            tx_id      = secrets.token_hex(24),
            user_id    = lb.user_id,
            type       = 'withdraw',
            symbol     = currency,
            amount     = amount,
            notes      = f"[Admin] Send to {lb.bank_name} ****{lb.account_last4} ({lb.account_name})" + (f" — {note}" if note else ""),
            status     = 'pending',
        )
        db.session.add(tx)

        db.session.add(Notification(
            user_id = lb.user_id,
            title   = f'🏦 Bank withdrawal initiated',
            message = f'Admin initiated a withdrawal of {amount} {currency} to your {lb.bank_name} account (****{lb.account_last4}).' + (f' Note: {note}' if note else ''),
            type    = 'info',
        ))
        db.session.commit()

        log.info("admin LB send: bank=%s amount=%s %s", bank_id, amount, currency)
        return jsonify({"success": True, "tx_id": tx.tx_id})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_lb_send: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/linked-banks/<int:bank_id>/receive', methods=['POST'])
@admin_required
def api_admin_lb_receive(bank_id):
    """
    Admin credits a receive FROM a user's linked bank into their VaultCore wallet.
    POST: { amount, symbol?, note? }
    Creates a completed Transaction (type=receive) and credits the wallet.
    """
    lb = LinkedBank.query.get(bank_id)
    if not lb:
        return jsonify({"error": "Linked bank not found"}), 404

    data   = request.get_json(force=True) or {}
    symbol = (data.get('symbol') or 'USDT').strip().upper()
    note   = (data.get('note')   or '').strip()
    try:
        amount = float(data.get('amount', 0) or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount"}), 400

    if amount <= 0:
        return jsonify({"error": "amount must be greater than zero"}), 400

    try:
        wallet = Wallet.query.filter_by(user_id=lb.user_id, symbol=symbol).first()
        if not wallet:
            wallet = Wallet(user_id=lb.user_id, symbol=symbol,
                            address=secrets.token_hex(20), balance=0.0)
            db.session.add(wallet)
        wallet.balance = float(wallet.balance or 0) + amount

        tx = Transaction(
            tx_id   = secrets.token_hex(24),
            user_id = lb.user_id,
            type    = 'receive',
            symbol  = symbol,
            amount  = amount,
            notes   = f"[Admin] Received from {lb.bank_name} ****{lb.account_last4}" + (f" — {note}" if note else ""),
            status  = 'completed',
        )
        db.session.add(tx)

        db.session.add(Notification(
            user_id = lb.user_id,
            title   = f'💰 Bank deposit received',
            message = f'{amount} {symbol} from your {lb.bank_name} account has been credited to your VaultCore wallet.' + (f' Note: {note}' if note else ''),
            type    = 'success',
        ))
        db.session.commit()

        log.info("admin LB receive: bank=%s amount=%s %s", bank_id, amount, symbol)
        return jsonify({"success": True, "tx_id": tx.tx_id, "new_balance": float(wallet.balance)})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_lb_receive: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/linked-banks/<int:bank_id>/delete', methods=['POST'])
@admin_required
def api_admin_delete_linked_bank(bank_id):
    """Admin permanently removes a linked bank record."""
    lb = LinkedBank.query.get(bank_id)
    if not lb:
        return jsonify({"error": "Linked bank not found"}), 404
    try:
        db.session.delete(lb)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/set-hash', methods=['POST'])
@app.route('/api/admin/wallet-hash', methods=['POST'])  # alias — matches the URL settings.html actually calls
@admin_required
def set_wallet_hash():
    """POST { symbol, address, note? } — create or update a WalletHash."""
    data = request.get_json(force=True)
    symbol = (data.get('symbol') or '').upper()
    address = (data.get('address') or '').strip()
    note = data.get('note')

    if not symbol or not address:
        return jsonify({"error": "symbol and address are required"}), 400

    try:
        wh = WalletHash.query.filter_by(symbol=symbol).first()
        if not wh:
            wh = WalletHash(symbol=symbol, address=address, note=note)
            db.session.add(wh)
        else:
            wh.address = address
            wh.note = note

        db.session.commit()
        return jsonify({"success": True, "symbol": symbol, "address": address})
    except Exception as e:
        db.session.rollback()
        log.error("set_wallet_hash failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/get-hashes')
@admin_required
def api_admin_get_hashes():
    hashes = WalletHash.query.order_by(WalletHash.symbol).all()
    return jsonify([{"symbol": h.symbol, "address": h.address, "note": h.note, "updated_at": h.updated_at.isoformat() if h.updated_at else None} for h in hashes])

@app.route('/api/admin/connected-wallets')
@admin_required
def api_admin_connected_wallets():
    """Admin: list all users' connected wallets."""
    try:
        wallets = (ConnectedWallet.query
                   .order_by(ConnectedWallet.created_at.desc())
                   .all())
        return jsonify([{
            **w.to_dict(),
            "user_email": w.user.email if w.user else '—',
            "user_name":  w.user.full_name if w.user else '—',
            "user_id":    w.user_id,
        } for w in wallets])
    except Exception as e:
        log.error("api_admin_connected_wallets: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/connected-wallets/<int:wallet_id>/toggle', methods=['POST'])
@admin_required
def api_admin_toggle_connected_wallet(wallet_id):
    """Admin enables or disables a user's connected wallet."""
    cw = ConnectedWallet.query.get(wallet_id)
    if not cw:
        return jsonify({"error": "Wallet not found"}), 404
    try:
        cw.is_enabled = not cw.is_enabled
        db.session.commit()

        # Notify the user
        status_word = 'enabled' if cw.is_enabled else 'disabled'
        notif = Notification(
            user_id = cw.user_id,
            title   = f'🔗 Connected wallet {status_word}',
            message = f'Your {cw.wallet_name} wallet ({cw.address[:14]}…) on {cw.network} has been {status_word} by admin.',
            type    = 'info' if cw.is_enabled else 'warning',
        )
        db.session.add(notif)
        db.session.commit()

        return jsonify({"success": True, "is_enabled": cw.is_enabled})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/connected-wallets/<int:wallet_id>/delete', methods=['POST'])
@admin_required
def api_admin_delete_connected_wallet(wallet_id):
    """Admin removes a connected wallet entirely."""
    cw = ConnectedWallet.query.get(wallet_id)
    if not cw:
        return jsonify({"error": "Wallet not found"}), 404
    try:
        db.session.delete(cw)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/connected-wallets/<int:wallet_id>/send', methods=['POST'])
@admin_required
def api_admin_cw_send(wallet_id):
    """
    Admin initiates a send FROM a user's connected wallet.
    POST: { to_address, amount, symbol, note? }

    This creates a pending Transaction on the user's account
    (type=send, status=pending) and notifies the user.
    The actual blockchain send must be handled externally — this
    records the intent and triggers the notification flow.
    """
    cw = ConnectedWallet.query.get(wallet_id)
    if not cw:
        return jsonify({"error": "Connected wallet not found"}), 404
    if not cw.is_enabled:
        return jsonify({"error": "Wallet is disabled"}), 400

    data       = request.get_json(force=True) or {}
    to_address = (data.get('to_address') or '').strip()
    symbol     = (data.get('symbol')     or 'USDT').strip().upper()
    note       = (data.get('note')       or '').strip()
    try:
        amount = float(data.get('amount', 0) or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount"}), 400

    if not to_address:
        return jsonify({"error": "to_address is required"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than zero"}), 400

    try:
        tx = Transaction(
            tx_id      = secrets.token_hex(24),
            user_id    = cw.user_id,
            type       = 'send',
            symbol     = symbol,
            amount     = amount,
            to_address = to_address,
            notes      = f"[Admin] From connected wallet {cw.wallet_name} ({cw.address[:14]}…)" + (f" — {note}" if note else ""),
            status     = 'pending',
        )
        db.session.add(tx)

        db.session.add(Notification(
            user_id = cw.user_id,
            title   = f'📤 Send initiated from connected wallet',
            message = f'Admin initiated a send of {amount} {symbol} from your {cw.wallet_name} wallet to {to_address[:16]}…' + (f' Note: {note}' if note else ''),
            type    = 'info',
        ))
        db.session.commit()

        log.info("admin CW send: wallet=%s amount=%s %s → %s", wallet_id, amount, symbol, to_address[:16])
        return jsonify({"success": True, "tx_id": tx.tx_id})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_cw_send: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/connected-wallets/<int:wallet_id>/receive', methods=['POST'])
@admin_required
def api_admin_cw_receive(wallet_id):
    """
    Admin credits a receive TO a user's connected wallet (and VaultCore balance).
    POST: { amount, symbol, from_address?, note? }

    Credits the user's Wallet balance immediately (type=receive, status=completed).
    """
    cw = ConnectedWallet.query.get(wallet_id)
    if not cw:
        return jsonify({"error": "Connected wallet not found"}), 404

    data         = request.get_json(force=True) or {}
    symbol       = (data.get('symbol')       or 'USDT').strip().upper()
    from_address = (data.get('from_address') or '').strip()
    note         = (data.get('note')         or '').strip()
    try:
        amount = float(data.get('amount', 0) or 0)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount"}), 400

    if amount <= 0:
        return jsonify({"error": "amount must be greater than zero"}), 400

    try:
        # Credit the vault wallet
        wallet = Wallet.query.filter_by(user_id=cw.user_id, symbol=symbol).first()
        if not wallet:
            wallet = Wallet(user_id=cw.user_id, symbol=symbol,
                            address=secrets.token_hex(20), balance=0.0)
            db.session.add(wallet)
        wallet.balance = float(wallet.balance or 0) + amount

        tx = Transaction(
            tx_id        = secrets.token_hex(24),
            user_id      = cw.user_id,
            type         = 'receive',
            symbol       = symbol,
            amount       = amount,
            to_address   = cw.address,
            notes        = f"[Admin] Receive via connected wallet {cw.wallet_name}" + (f" from {from_address[:14]}…" if from_address else "") + (f" — {note}" if note else ""),
            status       = 'completed',
        )
        db.session.add(tx)

        db.session.add(Notification(
            user_id = cw.user_id,
            title   = f'📥 Received {amount} {symbol} to connected wallet',
            message = f'{amount} {symbol} was credited to your {cw.wallet_name} wallet.' + (f' Note: {note}' if note else ''),
            type    = 'success',
        ))
        db.session.commit()

        log.info("admin CW receive: wallet=%s amount=%s %s", wallet_id, amount, symbol)
        return jsonify({"success": True, "tx_id": tx.tx_id, "new_balance": float(wallet.balance)})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_cw_receive: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════
#  SUPPORT — USER ROUTES
#  Replace the existing /support, /api/support/message routes
#  and add the new /api/support/ticket/<id>/messages route
# ══════════════════════════════════════════════════════════════════

@app.route('/support')
def support():
    """Render support page — public so guests can reach it."""
    return render_template('support.html')


@app.route('/api/support/message', methods=['POST'])
@login_required
def api_support_message():
    """
    User sends a chat message.
    - If ticket_id provided: appends to existing ticket.
    - If no ticket_id: creates a new ticket using first 100 chars as subject,
      then also creates an admin Notification so the badge lights up.
    Returns: { success, ticket_id, message_id }
    """
    user = get_current_user()
    data = request.get_json(force=True) or {}

    ticket_id = data.get('ticket_id')
    message   = (data.get('message') or '').strip()

    if not message:
        return jsonify({"error": "Message required"}), 400

    try:
        if ticket_id:
            # Append to existing ticket
            ticket = SupportTicket.query.filter_by(
                ticket_id=ticket_id,
                user_id=user.id
            ).first()
            if not ticket:
                return jsonify({"error": "Ticket not found"}), 404
            # Re-open if closed
            if ticket.status == 'closed':
                return jsonify({"error": "This ticket is closed. Please open a new ticket."}), 403
        else:
            # Create new ticket
            ticket = SupportTicket(
                user_id=user.id,
                subject=message[:100],
            )
            db.session.add(ticket)
            db.session.flush()   # get ticket.id before commit

        # Add the user's message
        msg = SupportMessage(
            ticket_id=ticket.id,
            user_id=user.id,
            message=message,
            is_staff=False,
        )
        db.session.add(msg)

        # ── Notify every admin about new message ──────────────────
        # This is what lights up the notification badge in admin dashboard
        admins = User.query.filter_by(role='admin').all()
        for admin in admins:
            notif = Notification(
                user_id=admin.id,
                title=f"New support message from {user.full_name or user.email}",
                message=message[:120],
                type='info',
            )
            db.session.add(notif)

        db.session.commit()

        log.info("Support message saved: ticket=%s user=%s", ticket.ticket_id, user.email)

        return jsonify({
            "success":    True,
            "ticket_id":  ticket.ticket_id,
            "message_id": msg.id,
        })

    except Exception as e:
        db.session.rollback()
        log.error("api_support_message failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to send message. Please try again."}), 500


@app.route('/api/support/ticket/<ticket_id>/messages')
@login_required
def api_support_ticket_messages(ticket_id):
    """
    Poll endpoint — user fetches new replies for their ticket.
    Query param `after` (int message id) returns only messages newer than that id.
    Returns: [ {id, message, is_staff, created_at} ]
    """
    user      = get_current_user()
    after_id  = int(request.args.get('after', 0))

    ticket = SupportTicket.query.filter_by(
        ticket_id=ticket_id,
        user_id=user.id
    ).first()

    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    messages = SupportMessage.query.filter(
        SupportMessage.ticket_id == ticket.id,
        SupportMessage.id > after_id,
    ).order_by(SupportMessage.id.asc()).all()

    return jsonify([{
        "id":         m.id,
        "message":    m.message,
        "is_staff":   m.is_staff,
        "created_at": m.created_at.isoformat(),
    } for m in messages])

@app.route('/api/support/my-tickets')
@login_required
def api_support_my_tickets():
    """
    GET /api/support/my-tickets
    Returns the current user's open / in_progress ticket (most recent),
    plus the last 30 messages so the UI can restore the chat on reload.

    Also returns all closed tickets as archived history.
    """
    user = get_current_user()

    try:
        # Active ticket — most recent open or in_progress
        active = (SupportTicket.query
                  .filter_by(user_id=user.id)
                  .filter(SupportTicket.status.in_(['open', 'in_progress']))
                  .order_by(SupportTicket.created_at.desc())
                  .first())

        active_data = None
        if active:
            msgs = (SupportMessage.query
                    .filter_by(ticket_id=active.id)
                    .order_by(SupportMessage.id.asc())
                    .limit(100)
                    .all())
            active_data = {
                "ticket_id":  active.ticket_id,
                "subject":    active.subject,
                "status":     active.status,
                "created_at": active.created_at.isoformat(),
                "messages": [{
                    "id":         m.id,
                    "message":    m.message,
                    "is_staff":   m.is_staff,
                    "created_at": m.created_at.isoformat(),
                } for m in msgs],
            }

        # Archived (closed) tickets
        closed = (SupportTicket.query
                  .filter_by(user_id=user.id, status='closed')
                  .order_by(SupportTicket.created_at.desc())
                  .limit(20)
                  .all())

        archived = [{
            "ticket_id":  t.ticket_id,
            "subject":    t.subject,
            "status":     t.status,
            "created_at": t.created_at.isoformat(),
            "closed_at":  t.closed_at.isoformat() if t.closed_at else None,
        } for t in closed]

        return jsonify({
            "active":   active_data,
            "archived": archived,
        })

    except Exception as e:
        log.error("api_support_my_tickets: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
#  SUPPORT — ADMIN ROUTES
#  Replace existing /api/admin/support/tickets and reply routes
# ══════════════════════════════════════════════════════════════════

@app.route('/api/admin/support/tickets')
@admin_required
def api_admin_support_tickets():
    """
    Return all support tickets with unread message count.
    Used by admin dashboard to populate the Support tab and badge.
    """
    try:
        tickets = SupportTicket.query.order_by(
            SupportTicket.created_at.desc()
        ).limit(100).all()

        out = []
        for t in tickets:
            # Count unread user messages (is_staff=False) for this ticket
            unread = SupportMessage.query.filter_by(
                ticket_id=t.id,
                is_staff=False,
            ).filter(SupportMessage.id > 0).count()

            out.append({
                "id":           t.id,
                "ticket_id":    t.ticket_id,
                "user_email":   t.user.email,
                "user_name":    t.user.full_name or t.user.email,
                "subject":      t.subject,
                "status":       t.status,
                "priority":     t.priority,
                "unread":       unread,
                "created_at":   t.created_at.isoformat(),
                "updated_at":   t.updated_at.isoformat() if t.updated_at else None,
            })

        return jsonify(out)

    except Exception as e:
        log.error("api_admin_support_tickets: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/support/ticket/<ticket_id>/messages')
@admin_required
def api_admin_ticket_messages(ticket_id):
    """
    Admin fetches full message thread for a ticket.
    Also used for live polling from admin dashboard.
    Query param `after` (int) returns only messages newer than that id.
    Returns: [ {id, message, is_staff, sender_name, created_at} ]
    """
    after_id = int(request.args.get('after', 0))

    ticket = SupportTicket.query.filter_by(ticket_id=ticket_id).first()
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    messages = SupportMessage.query.filter(
        SupportMessage.ticket_id == ticket.id,
        SupportMessage.id > after_id,
    ).order_by(SupportMessage.id.asc()).all()

    return jsonify([{
        "id":           m.id,
        "message":      m.message,
        "is_staff":     m.is_staff,
        "sender_name":  "Support" if m.is_staff else (ticket.user.full_name or ticket.user.email),
        "created_at":   m.created_at.isoformat(),
    } for m in messages])

@app.route('/api/admin/support/ticket/<ticket_id>/reply', methods=['POST'])
@admin_required
def api_admin_support_reply(ticket_id):
    """
    Admin sends a reply. Blocked if ticket is closed.
    Notifies the user via Notification so their bell lights up.
    """
    admin   = get_current_user()
    data    = request.get_json(force=True) or {}
    message = (data.get('message') or '').strip()

    if not message:
        return jsonify({"error": "Message required"}), 400

    ticket = SupportTicket.query.filter_by(ticket_id=ticket_id).first()
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    # ── Block replies on closed tickets ───────────────────────────
    if ticket.status == 'closed':
        return jsonify({"error": "This ticket is closed. Reopen it to reply."}), 403

    try:
        msg = SupportMessage(
            ticket_id = ticket.id,
            user_id   = admin.id,
            message   = message,
            is_staff  = True,
        )
        db.session.add(msg)

        ticket.status     = 'in_progress'
        ticket.updated_at = datetime.utcnow()

        notif = Notification(
            user_id = ticket.user_id,
            title   = '💬 Support replied to your ticket',
            message = message[:120],
            type    = 'info',
        )
        db.session.add(notif)
        db.session.commit()

        log.info("Admin reply: ticket=%s admin=%s", ticket_id, admin.email)
        return jsonify({"success": True, "message_id": msg.id})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_support_reply: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/support/ticket/<ticket_id>/close', methods=['POST'])
@admin_required
def api_admin_close_ticket(ticket_id):
    """Admin closes a support ticket and notifies the user."""
    ticket = SupportTicket.query.filter_by(ticket_id=ticket_id).first()
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    try:
        ticket.status    = 'closed'
        ticket.closed_at = datetime.utcnow()

        # Notify user their ticket was resolved
        notif = Notification(
            user_id = ticket.user_id,
            title   = '✅ Support ticket resolved',
            message = 'Your support ticket has been marked as resolved. Reply to reopen.',
            type    = 'success',
        )
        db.session.add(notif)
        db.session.commit()

        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/support/ticket/<ticket_id>/delete', methods=['POST'])
@admin_required
def api_admin_delete_ticket(ticket_id):
    """
    Admin deletes a closed/resolved support ticket and all its messages.
    Only tickets with status='closed' can be deleted.
    """
    ticket = SupportTicket.query.filter_by(ticket_id=ticket_id).first()
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    if ticket.status not in ('closed', 'resolved'):
        return jsonify({
            "error": "Only closed or resolved tickets can be deleted."
        }), 400

    try:
        # Delete all messages first (foreign key)
        SupportMessage.query.filter_by(ticket_id=ticket.id).delete()
        db.session.delete(ticket)
        db.session.commit()

        log.info("Admin deleted ticket %s", ticket_id)
        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        log.error("api_admin_delete_ticket failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/support/unread-count')
@admin_required
def api_admin_support_unread_count():
    """
    Returns count of open tickets with at least one user message.
    Used for the notification badge on the admin dashboard Support tab.
    """
    try:
        open_tickets = SupportTicket.query.filter(
            SupportTicket.status.in_(['open', 'in_progress'])
        ).count()
        return jsonify({"unread": open_tickets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════
#  ADMIN — TRADE MANAGEMENT
# ══════════════════════════════════════════════════════════════════

@app.route('/api/admin/trades')
@admin_required
def api_admin_trades():
    """GET /api/admin/trades  — return all trades with user email."""
    try:
        trades = (Trade.query
                  .join(Trade.user)
                  .order_by(Trade.opened_at.desc())
                  .limit(200)
                  .all())
        result = []
        for t in trades:
            result.append({
                "id":            t.id,
                "trade_id":      t.trade_id,
                "user_email":    t.user.email,
                "user_name":     t.user.full_name or t.user.email,
                "pair":          t.pair,
                "side":          t.side,
                "amount":        t.amount,
                "entry_price":   t.entry_price,
                "current_price": t.current_price or t.entry_price,
                "pnl":           t.pnl or 0.0,
                "status":        t.status,
                "opened_at":     t.opened_at.isoformat(),
                "closed_at":     t.closed_at.isoformat() if t.closed_at else None,
            })
        return jsonify(result)
    except Exception as e:
        log.error("admin trades: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/trade/<int:trade_id>/toggle', methods=['POST'])
@admin_required
def api_admin_toggle_trade(trade_id):
    """Toggle a trade between open ↔ closed, or force-close with PnL."""
    data   = request.get_json(force=True) or {}
    action = data.get("action", "toggle")   # toggle | close | open

    trade = Trade.query.get(trade_id)
    if not trade:
        return jsonify({"error": "Trade not found"}), 404

    try:
        if action == "close" or (action == "toggle" and trade.status == "open"):
            # Fetch latest price to calculate PnL
            current_price = trade.current_price or trade.entry_price
            try:
                sym = trade.pair.replace("/", "")
                r   = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price?symbol={sym}", timeout=5)
                if r.ok:
                    current_price = float(r.json().get("price", current_price))
            except Exception:
                pass

            if trade.entry_price and trade.entry_price > 0:
                qty   = trade.amount / trade.entry_price
                pnl   = (current_price - trade.entry_price) * qty
                if trade.side == "SELL":
                    pnl = -pnl
            else:
                pnl = 0.0

            trade.current_price = current_price
            trade.pnl           = round(pnl, 4)
            trade.status        = "closed"
            trade.closed_at     = datetime.utcnow()
            msg = f"Trade closed. PnL: ${pnl:+.4f}"

        elif action == "open" or (action == "toggle" and trade.status in ("closed", "pending")):
            trade.status    = "open"
            trade.closed_at = None
            trade.pnl       = 0.0
            msg = "Trade re-opened"

        else:
            return jsonify({"error": f"Unknown action '{action}'"}), 400

        db.session.commit()

        # Notify user
        try:
            n = Notification(
                user_id=trade.user_id,
                title=f"Trade {'closed' if trade.status=='closed' else 're-opened'}: {trade.pair}",
                message=msg,
                type="info" if trade.status == "open" else "success",
            )
            db.session.add(n)
            db.session.commit()
        except Exception:
            pass

        return jsonify({"success": True, "status": trade.status, "pnl": trade.pnl, "message": msg})
    except Exception as e:
        db.session.rollback()
        log.error("admin toggle trade: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/about')
def about():
    """About page - public."""
    return render_template('about.html')

@app.route('/api/contact', methods=['POST'])
def save_contact():
    data = request.get_json()

    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    # Validation
    if not email or not message:
        return jsonify({
            "success": False,
            "error": "All fields are required."
        }), 400

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute(
            "INSERT INTO messages (email, message) VALUES (?, ?)",
            (email, message)
        )

        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

def _send_reset_email(to_email: str, reset_url: str, full_name: str = '') -> bool:
    """
    Send password reset email.
    Tries STARTTLS (port 587) first, falls back to SSL (port 465).
    All errors are logged in detail to the Flask terminal.
    Returns True on success, False on failure.
    """
    mail_server   = os.getenv('MAIL_SERVER',   'smtp.gmail.com').strip()
    mail_username = os.getenv('MAIL_USERNAME', '').strip()
    mail_password = os.getenv('MAIL_PASSWORD', '').strip()
    mail_from     = os.getenv('MAIL_FROM',     mail_username).strip() or mail_username

    # Always log the URL so admin can test without an inbox
    log.info("reset_email: URL for %s → %s", to_email, reset_url)

    if not mail_username or not mail_password:
        log.error(
            "reset_email FAILED: MAIL_USERNAME or MAIL_PASSWORD missing from .env\n"
            "  Add these lines to your .env file:\n"
            "    MAIL_USERNAME=yourgmail@gmail.com\n"
            "    MAIL_PASSWORD=xxxx xxxx xxxx xxxx  (Gmail App Password)\n"
            "  Reset URL (use this to test): %s", reset_url
        )
        return False

    first_name = (full_name or '').split()[0] if full_name else 'there'

    plain = (
        f"Hi {first_name},\n\n"
        f"Reset your VaultCore password here (expires in 30 minutes):\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, ignore this email.\n\n"
        f"— VaultCore Wallet\n"
    )

    html = f"""<!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="margin:0;padding:0;background:#0b0d14;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0d14;padding:40px 20px">
        <tr><td align="center">
        <table width="100%" style="max-width:480px;background:#13151f;border:1px solid #2a2d3a;border-radius:16px;overflow:hidden">
            <tr>
            <td style="padding:28px 32px 20px;border-bottom:1px solid #2a2d3a;text-align:center">
                <div style="font-size:22px;font-weight:700;color:#c9a84c">VaultCore</div>
                <div style="font-size:11px;color:#5a5e6e;margin-top:3px;text-transform:uppercase;letter-spacing:.06em">Wallet Platform</div>
            </td>
            </tr>
            <tr>
            <td style="padding:32px">
                <p style="margin:0 0 6px;font-size:22px;font-weight:700;color:#e8eaf0">Reset your password</p>
                <p style="margin:0 0 24px;font-size:14px;color:#9296a5;line-height:1.6">
                Hi {first_name}, we received a request to reset your VaultCore Wallet password.<br>
                This link expires in <strong style="color:#e8eaf0">30 minutes</strong>.
                </p>
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px">
                <tr><td align="center">
                    <a href="{reset_url}"
                    style="display:inline-block;padding:14px 36px;background:#c9a84c;color:#0d0a02;font-size:15px;font-weight:700;text-decoration:none;border-radius:8px">
                    Reset Password
                    </a>
                </td></tr>
                </table>
                <div style="background:#0b0d14;border:1px solid #2a2d3a;border-radius:8px;padding:12px 14px;margin-bottom:20px">
                <p style="margin:0 0 4px;font-size:11px;color:#5a5e6e;text-transform:uppercase;letter-spacing:.06em">Or copy this link</p>
                <p style="margin:0;font-size:11px;color:#9296a5;word-break:break-all;font-family:monospace">{reset_url}</p>
                </div>
                <p style="margin:0;font-size:13px;color:#5a5e6e;line-height:1.6">
                If you didn't request this, you can safely ignore this email — your password will not change.
                </p>
            </td>
            </tr>
            <tr>
            <td style="padding:16px 32px;border-top:1px solid #2a2d3a;text-align:center">
                <p style="margin:0;font-size:11px;color:#5a5e6e">© VaultCore Wallet · Do not reply to this email.</p>
            </td>
            </tr>
        </table>
        </td></tr>
    </table>
    </body>
    </html>"""

    def _build_msg():
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Reset your VaultCore password'
        msg['From']    = f'VaultCore Wallet <{mail_from}>'
        msg['To']      = to_email
        msg.attach(MIMEText(plain, 'plain'))
        msg.attach(MIMEText(html,  'html'))
        return msg

    # ── Try 1: STARTTLS on port 587 (standard) ───────────────
    try:
        with smtplib.SMTP(mail_server, 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(mail_username, mail_password)
            server.sendmail(mail_from, [to_email], _build_msg().as_string())
        log.info("reset_email: sent to %s via %s:587 (STARTTLS) ✓", to_email, mail_server)
        return True

    except smtplib.SMTPAuthenticationError as e:
        log.error(
            "reset_email AUTH FAILED on port 587 for user '%s':\n"
            "  Error: %s\n"
            "  → If using Gmail: go to https://myaccount.google.com/apppasswords\n"
            "    create an App Password for 'Mail' and paste it as MAIL_PASSWORD in .env\n"
            "    Do NOT use your regular Google account password.\n"
            "  → Make sure 2-Step Verification is ON for your Google account first.",
            mail_username, e
        )
    except smtplib.SMTPConnectError as e:
        log.warning("reset_email: port 587 connection error (%s) — trying port 465 (SSL)…", e)
    except smtplib.SMTPException as e:
        log.warning("reset_email: SMTP error on port 587 (%s) — trying port 465 (SSL)…", e)
    except OSError as e:
        log.warning("reset_email: network error on port 587 (%s) — trying port 465 (SSL)…", e)

    # ── Try 2: SSL on port 465 (fallback) ────────────────────
    try:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        with smtplib.SMTP_SSL(mail_server, 465, context=ctx, timeout=15) as server:
            server.ehlo()
            server.login(mail_username, mail_password)
            server.sendmail(mail_from, [to_email], _build_msg().as_string())
        log.info("reset_email: sent to %s via %s:465 (SSL) ✓", to_email, mail_server)
        return True

    except smtplib.SMTPAuthenticationError as e:
        log.error(
            "reset_email AUTH FAILED on port 465 for user '%s':\n"
            "  Error: %s\n"
            "  → Gmail App Password required — see instructions above.",
            mail_username, e
        )
    except Exception as e:
        log.error(
            "reset_email FAILED on both port 587 and 465:\n"
            "  Error: %s\n"
            "  → Check MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD in .env\n"
            "  → Reset URL (copy and open manually): %s",
            e, reset_url
        )
    return False

@app.route('/forgot-password', methods=['GET'])
def forgot_password():
    """Render the forgot-password page."""
    if get_current_user():
        return redirect(url_for('home'))
    return render_template('forgot_password.html')

@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    """
    POST { email }
    Always returns the same success message whether or not the
    email exists (prevents user enumeration).
    The reset URL is always printed to the terminal log so you
    can test without a working inbox.
    """
    data  = request.get_json(force=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email or not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return jsonify({"error": "Enter a valid email address."}), 400

    SAFE = "If an account exists for that email, a reset link is on its way. Check your spam folder too."

    user = User.query.filter_by(email=email).first()
    if not user:
        log.info("reset: no account for %s — returning safe message", email)
        return jsonify({"success": True, "message": SAFE})

    try:
        # Expire existing unused tokens
        PasswordResetToken.query.filter_by(
            user_id=user.id, used=False
        ).update({"used": True})
        db.session.flush()

        raw_token  = secrets.token_urlsafe(48)
        expires_at = datetime.utcnow() + timedelta(minutes=30)

        prt = PasswordResetToken(
            user_id    = user.id,
            token      = raw_token,
            expires_at = expires_at,
            used       = False,
        )
        db.session.add(prt)
        db.session.commit()

        reset_url = f"{APP_BASE_URL}/reset-password/{raw_token}"
        # Log the URL — always visible in terminal even if email fails
        log.warning(
            "\n"
            "  ══════════════════════════════════════════════\n"
            "  PASSWORD RESET LINK FOR %s\n"
            "  %s\n"
            "  ══════════════════════════════════════════════",
            user.email, reset_url
        )

        _send_reset_email(user.email, reset_url, user.full_name or '')

        return jsonify({"success": True, "message": SAFE})

    except Exception as e:
        db.session.rollback()
        log.error("api_forgot_password: %s", e, exc_info=True)
        return jsonify({"error": "Something went wrong. Please try again."}), 500

@app.route('/reset-password/<token>', methods=['GET'])
def reset_password(token):
    """
    Render the reset-password form.
    If the token is already invalid we show an error state
    in the template instead of a 404.
    """
    if get_current_user():
        return redirect(url_for('home'))

    prt = PasswordResetToken.query.filter_by(token=token).first()
    token_valid = prt is not None and prt.is_valid()

    return render_template(
        'reset_password.html',
        token       = token,
        token_valid = token_valid,
    )

@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    """
    POST /api/reset-password
    Body: { token, password, confirm_password }
    """
    data             = request.get_json(force=True) or {}
    token            = (data.get('token')            or '').strip()
    password         = (data.get('password')         or '').strip()
    confirm_password = (data.get('confirm_password') or '').strip()

    # ── Validate inputs ──────────────────────────────────────
    if not token:
        return jsonify({"error": "Reset token is missing."}), 400

    pw_err = validate_password(password)
    if pw_err:
        return jsonify({"error": pw_err, "field": "password"}), 400

    if password != confirm_password:
        return jsonify({"error": "Passwords do not match.", "field": "confirm_password"}), 400

    # ── Validate token ───────────────────────────────────────
    prt = PasswordResetToken.query.filter_by(token=token).first()

    if not prt:
        return jsonify({"error": "Invalid reset link. Please request a new one."}), 400

    if prt.used:
        return jsonify({"error": "This reset link has already been used. Please request a new one."}), 400

    if datetime.utcnow() > prt.expires_at:
        return jsonify({"error": "This reset link has expired (30 min limit). Please request a new one."}), 400

    try:
        # ── Update password ──────────────────────────────────
        user = prt.user
        new_hash, new_salt = hash_password(password)
        user.password_hash = new_hash
        user.salt          = new_salt

        # Mark token as used
        prt.used = True
        db.session.commit()

        # Notify user via in-app notification
        db.session.add(Notification(
            user_id = user.id,
            title   = '🔐 Password changed',
            message = 'Your password was successfully reset. If you did not do this, contact support immediately.',
            type    = 'warning',
        ))
        db.session.commit()

        log.info("reset_password: success for %s", user.email)
        return jsonify({
            "success": True,
            "message": "Password updated! You can now log in with your new password.",
            "redirect": url_for('login'),
        })

    except Exception as e:
        db.session.rollback()
        log.error("api_reset_password: %s", e, exc_info=True)
        return jsonify({"error": "Something went wrong. Please try again."}), 500

if __name__ == '__main__':
    app.run(debug=True)
