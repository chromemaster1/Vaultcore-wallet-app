# main.py
# ─────────────────────────────────────────────
# Flask backend for VaultCore Wallet
# ─────────────────────────────────────────────
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
    SupportTicket, SupportMessage, Trade
)
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.utils import secure_filename
import psycopg2
import gunicorn
from sqlalchemy import inspect


_base_dir = pathlib.Path(__file__).parent.resolve()
load_dotenv(dotenv_path=_base_dir / '.env')


# load_dotenv()

env_path = pathlib.Path('.env')
print("=== ENV DEBUG ===")
print(f"  Working directory : {pathlib.Path.cwd()}")
print(f"  .env exists here  : {env_path.exists()}")
print(f"  .env full path    : {env_path.resolve()}")
print(f"  CMC key loaded    : {'YES' if os.getenv('CMC_PRO_API_KEY') else 'NO — still missing'}")
print(f"  NEWS key loaded   : {'YES' if os.getenv('NEWS_API_KEY') else 'NO — still missing'}")
print("=================")

# ── logging setup ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "FLASK_SECRET_KEY")
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
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///user.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config["SQLALCHEMY_ECHO"] = False

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

    except Exception as e:
        log.error("Failed to create database tables: %s", e, exc_info=True)

# Start background scheduler for periodic tasks
try:
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: persist_market_snapshot(200), 'interval', seconds=60, id='market_persist')
    scheduler.start()
    log.info("Background scheduler started")
except Exception as e:
    log.error("Failed to start scheduler: %s", e, exc_info=True)


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


def create_user(email: str, password: str, full_name: str) -> dict:
    """
    Create new user account.
    Returns user dict or None if email exists.
    """
    email = email.lower().strip()

    if email_exists(email):
        return None

    user_id = secrets.token_hex(8)
    pw_hash, salt = hash_password(password)

    user = {
        "user_id":     user_id,
        "email":       email,
        "password_hash": pw_hash,
        "salt":        salt,
        "full_name":   full_name.strip(),
        "created_at":  datetime.utcnow().isoformat(),
    }

    USERS_DB[email] = user
    log.info("auth: user created — %s (%s)", email, user_id)

    return user

def authenticate_user(email: str, password: str) -> dict | None:
    """
    Authenticate user credentials.
    Returns user dict if valid, None otherwise.
    """
    email = email.lower().strip()
    user  = USERS_DB.get(email)

    if not user:
        return None

    if verify_password(password, user["password_hash"], user["salt"]):
        log.info("auth: login success — %s", email)
        return user

    log.warning("auth: login failed — %s (wrong password)", email)
    return None

# ══════════════════════════════════════════════
#  LOGIN REQUIRED DECORATOR
# ══════════════════════════════════════════════

def login_required(f):
    """Decorator to protect routes requiring authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')

        log.debug("login_required check - endpoint: %s, user_id in session: %s",
                  request.endpoint, bool(user_id))

        if not user_id:
            log.warning("auth: unauthorized access attempt to %s - session: %s",
                       request.endpoint, dict(session))
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))

        # Verify user still exists in database
        user = User.query.filter_by(user_id=user_id).first()
        if not user:
            log.warning("auth: session user_id not found in database - clearing session")
            session.clear()
            flash('Please login to access this page.', 'warning')
            return redirect(url_for('login'))

        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to protect admin-only routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'warning')

        return redirect(url_for('login'))
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
    data   = request.get_json(force=True)
    symbol = (data.get("symbol") or "").strip().upper().replace("/","")
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
        return jsonify(order)

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
#  BANK LINKING (OAuth-style simulation)
# ══════════════════════════════════════════════
def verify_banking_credentials(bank_code: str, username: str, password: str) -> tuple[bool, str]:
    """
    Verify online banking credentials.
    In production, integrate with Plaid, Mono, or bank-specific OAuth.

    Returns (success, error_message)
    """
    if not username or not password:
        return False, "Username and password are required."

    if len(password) < 4:
        return False, "Invalid credentials — password too short."

    # Mock verification — accept if password is not "wrong"
    if password.lower() == "wrong":
        return False, "Invalid username or password."

    # Simulate API delay
    time.sleep(0.5)

    log.info("bank_link: verified credentials for bank %s, user %s", bank_code, username)
    return True, ""
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
    allowed_prefixes = ('/static', '/api', '/admin/coming-soon', '/admin')
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
    """Home page - requires login."""
    user = get_current_user()

    # If not logged in, render register/login page
    if not user:
        log.info("Home - no user, redirecting to login")
        return redirect(url_for('login'))

    market_data, market_err = get_market_data(limit=15)
    news, news_err          = get_crypto_news()
    pages = [market_data[i:i+5] for i in range(0, len(market_data), 5)] if market_data else []


    log.info("Home - user %s authenticated", user.email)
    # compute total portfolio value (sum of all wallets)
    total_balance = sum((w.balance or 0.0) for w in user.wallets) if user.wallets else 0.0

    return render_template(
        'index.html',
        pages=pages,
        news=news,
        market_error=market_err,
        news_error=news_err,
        total_balance=total_balance,
        now=datetime.utcnow()
    )

@app.route('/market')
@login_required
def market():
    """Market page."""
    return render_template('market.html')


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
    data = request.get_json(force=True)
    action = (data.get('action') or '').lower()
    note = data.get('note', '')

    tx = Transaction.query.filter_by(id=tx_id).first()
    if not tx:
        return jsonify({"error": "Transaction not found"}), 404

    if tx.status != 'pending':
        return jsonify({"error": "Transaction is not pending"}), 400

    try:
        user = tx.user
        if action == 'approve':
            # Deduct from user's wallet balance
            wallet = Wallet.query.filter_by(user_id=user.id, symbol=tx.symbol.upper()).first()
            if not wallet or wallet.balance < tx.amount:
                tx.status = 'failed'
                tx.notes = 'Insufficient balance on approval'
                db.session.commit()
                # notify user
                n = Notification(user_id=user.id, title='Transaction failed', message=f'Your transaction {tx.tx_id} failed on approval (insufficient balance).', type='error')
                db.session.add(n)
                db.session.commit()
                return jsonify({"success": False, "error": "Insufficient balance"}), 422

            wallet.balance = float(wallet.balance) - float(tx.amount)
            tx.status = 'completed'
            tx.completed_at = datetime.utcnow()
            tx.notes = note
            db.session.commit()

            n = Notification(user_id=user.id, title='Transaction approved', message=f'Your transaction {tx.tx_id} was approved and processed.', type='success')
            db.session.add(n)
            db.session.commit()

            return jsonify({"success": True})

        elif action == 'reject':
            tx.status = 'failed'
            tx.notes = note
            tx.completed_at = datetime.utcnow()
            db.session.commit()

            n = Notification(user_id=user.id, title='Transaction rejected', message=f'Your transaction {tx.tx_id} was rejected by admin.', type='error')
            db.session.add(n)
            db.session.commit()

            return jsonify({"success": True})

        else:
            return jsonify({"error": "Invalid action"}), 400

    except Exception as e:
        db.session.rollback()
        log.error("admin tx action failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

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
    EXCHANGE_RATES = {
        "USDT": 1.0
    }
    return render_template('withdraw.html',
                           exchange_rates=EXCHANGE_RATES,
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

@app.route('/api/link-bank', methods=['POST'])
def api_link_bank():
    """
    POST /api/link-bank
    Body: {bank_code, bank_name, username, password}
    Authenticates and links the bank account to the user's wallet.
    """
    data = request.get_json(force=True)
    bank_code = data.get("bank_code", "")
    bank_name = data.get("bank_name", "")
    username  = data.get("username", "").strip()
    password  = data.get("password", "").strip()

    if not all([bank_code, bank_name, username, password]):
        return jsonify({"error": "All fields are required."}), 400

    success, error = verify_banking_credentials(bank_code, username, password)

    if not success:
        log.warning("bank_link: failed for bank %s — %s", bank_code, error)
        return jsonify({"error": error}), 401

    # Store linked bank in session (in production use encrypted DB)
    session['linked_bank'] = {
        "bank_code": bank_code,
        "bank_name": bank_name,
        "account_last4": username[-4:] if len(username) >= 4 else "****",
        "linked_at": time.time(),
    }

    log.info("bank_link: success — %s linked", bank_name)

    return jsonify({
        "success": True,
        "bank_name": bank_name,
        "account_last4": session['linked_bank']['account_last4'],
    })

@app.route('/api/unlink-bank', methods=['POST'])
def api_unlink_bank():
    """POST /api/unlink-bank — removes linked bank from session."""
    if 'linked_bank' in session:
        del session['linked_bank']
        log.info("bank_link: unlinked")
    return jsonify({"success": True})

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

@app.route('/p2p')
def p2p_market():
    """Render P2P trading marketplace."""
    return render_template('p2p_market.html')

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
        price = None
        md = MarketData.query.filter_by(symbol=w.symbol.upper()).order_by(MarketData.last_updated.desc()).first()
        if md and md.price:
            price = md.price
        usd_value = (float(w.balance) * price) if price else None
        if usd_value:
            total_value += usd_value
        holdings[w.symbol.upper()] = {
            "balance": float(w.balance),
            "usd_value": usd_value,
        }

    open_trades = []
    trades = Trade.query.filter_by(user_id=user.id).order_by(Trade.opened_at.desc()).limit(50).all()
    for t in trades:
        open_trades.append({
            "id": t.trade_id,
            "pair": t.pair,
            "side": t.side,
            "amount": t.amount,
            "entry_price": t.entry_price,
            "current_price": t.current_price,
            "pnl": t.pnl,
            "status": t.status,
            "opened_at": t.opened_at.isoformat(),
        })

    log.info("wallet: loaded %d holdings, total $%.2f", len(holdings), total_value)

    return jsonify({
        "total_value": total_value,
        "holdings": holdings,
        "open_trades": open_trades,
    })

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
    data = request.get_json(force=True)

    email    = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()

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

    return jsonify({
        "success": True,
        "message": "Login successful!",
        "redirect": url_for('home'),
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

    if not user:
        return redirect(url_for('login'))

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
            "email": user.email,
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


@app.route('/api/admin/wallet-hash', methods=['POST'])
@admin_required
def api_admin_set_wallet_hash():
    """Create or update a wallet hash for a symbol. JSON: {symbol,address,note}"""
    try:
        data = request.get_json(force=True)
        # normalize symbol to lowercase to match NETWORKS keys used in templates
        symbol = (data.get('symbol') or '').lower().strip()
        address = (data.get('address') or '').strip()
        note = (data.get('note') or '').strip()

        if not symbol or symbol not in NETWORKS:
            return jsonify({"error": "Invalid or missing symbol."}), 400
        if not address:
            return jsonify({"error": "Address is required."}), 400

        wh = WalletHash.query.filter_by(symbol=symbol).first()
        if not wh:
            wh = WalletHash(symbol=symbol, address=address, note=note)
            db.session.add(wh)
        else:
            wh.address = address
            wh.note = note

        db.session.commit()
        return jsonify({"ok": True, "symbol": symbol, "address": address, "note": note})
    except Exception as e:
        db.session.rollback()
        log.error("api_admin_set_wallet_hash failed: %s", e, exc_info=True)
        return jsonify({"error": "Failed to save wallet hash."}), 500




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


@app.route('/api/settings/security/2fa', methods=['POST'])
@login_required
def api_enable_2fa():
    """Enable 2FA (placeholder)."""
    user = get_current_user()

    # TODO: Implement actual 2FA with pyotp
    return jsonify({
        "success": True,
        "message": "2FA setup — coming soon. Use Google Authenticator."
    })


@app.route('/api/admin/users')
@admin_required
def api_admin_users():
    """Get all users (admin only)."""
    users = User.query.order_by(User.created_at.desc()).all()

    return jsonify([{
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat(),
    } for u in users])


@app.route('/api/admin/manual-tx', methods=['POST'])
@admin_required
def api_manual_tx():
    """Create manual transaction (admin only)."""
    # uses Transaction and Wallet from model.py (imported at top)

    data = request.get_json(force=True)

    user_email = data.get("user_email")
    symbol = data.get("symbol")
    amount = float(data.get("amount", 0))
    tx_type = data.get("type", "receive")

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        # Create transaction
        tx = Transaction(
            tx_id=secrets.token_hex(32),
            user_id=user.id,
            type=tx_type,
            symbol=symbol,
            amount=amount,
            status='completed',
        )
        db.session.add(tx)

        # Update wallet
        wallet = Wallet.query.filter_by(user_id=user.id, symbol=symbol).first()
        if not wallet:
            wallet = Wallet(
                user_id=user.id,
                symbol=symbol,
                address=secrets.token_hex(20),
                balance=0,
            )
            db.session.add(wallet)

        if tx_type == 'receive':
            wallet.balance += amount
        else:
            wallet.balance -= amount

        db.session.commit()

        log.info("Admin manual TX: %s %s %s for %s", tx_type, amount, symbol, user_email)

        return jsonify({"success": True, "tx_id": tx.tx_id})

    except Exception as e:
        db.session.rollback()
        log.error("Manual TX failed: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/set-hash', methods=['POST'])
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

@app.route('/support')
def support():
    """Render support page."""
    return render_template('support.html')


@app.route('/api/admin/support/tickets')
@admin_required
def api_admin_support_tickets():
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).limit(100).all()
    out = []
    for t in tickets:
        out.append({
            "ticket_id": t.ticket_id,
            "user_email": t.user.email,
            "subject": t.subject,
            "status": t.status,
            "created_at": t.created_at.isoformat(),
        })
    return jsonify(out)


@app.route('/api/admin/support/ticket/<ticket_id>/reply', methods=['POST'])
@admin_required
def api_admin_support_reply(ticket_id):
    data = request.get_json(force=True)
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "Message required"}), 400

    ticket = SupportTicket.query.filter_by(ticket_id=ticket_id).first()
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    try:
        msg = SupportMessage(ticket_id=ticket.id, user_id=ticket.user_id, message=message, is_staff=True)
        db.session.add(msg)
        ticket.status = 'in_progress'
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/support/message', methods=['POST'])
@login_required
def api_support_message():
    """Send support message."""
    user = get_current_user()
    data = request.get_json(force=True)

    ticket_id = data.get('ticket_id')
    message = data.get('message', '').strip()

    if not message:
        return jsonify({"error": "Message required"}), 400

    try:
        # Find or create ticket
        if ticket_id:
            ticket = SupportTicket.query.filter_by(
                ticket_id=ticket_id,
                user_id=user.id
            ).first()
        else:
            ticket = SupportTicket(
                user_id=user.id,
                subject=message[:100],  # First 100 chars as subject
            )
            db.session.add(ticket)
            db.session.flush()

        # Add message
        msg = SupportMessage(
            ticket_id=ticket.id,
            user_id=user.id,
            message=message,
            is_staff=False,
        )
        db.session.add(msg)

        db.session.commit()

        return jsonify({
            "success": True,
            "ticket_id": ticket.ticket_id,
        })

    except Exception as e:
        db.session.rollback()
        log.error("Support message failed: %s", e)
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


if __name__ == '__main__':
    app.run(debug=True)
