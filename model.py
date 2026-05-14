# models.py
# ─────────────────────────────────────────────
# Database models using SQLAlchemy
# ─────────────────────────────────────────────

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import secrets

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.String(16), unique=True, nullable=False, default=lambda: secrets.token_hex(8))
    email           = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash   = db.Column(db.String(64), nullable=False)
    salt            = db.Column(db.String(32), nullable=False)
    full_name       = db.Column(db.String(100), nullable=False)
    role            = db.Column(db.String(20), default='user', nullable=False)  # user, admin
    is_active       = db.Column(db.Boolean, default=True, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    wallets         = db.relationship('Wallet', backref='user', lazy=True, cascade='all, delete-orphan')
    transactions    = db.relationship('Transaction', backref='user', lazy=True, cascade='all, delete-orphan')
    notifications   = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    settings        = db.relationship('UserSettings', backref='user', uselist=False, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.email}>'


class Wallet(db.Model):
    __tablename__ = 'wallets'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    symbol          = db.Column(db.String(10), nullable=False)  # BTC, ETH, etc.
    address         = db.Column(db.String(128), nullable=False)
    balance         = db.Column(db.Float, default=0.0, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'symbol', name='_user_symbol_uc'),)

    def __repr__(self):
        return f'<Wallet {self.symbol}: {self.balance}>'


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id              = db.Column(db.Integer, primary_key=True)
    tx_id           = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    type            = db.Column(db.String(20), nullable=False)  # send, receive, trade, withdraw
    symbol          = db.Column(db.String(10), nullable=False)
    amount          = db.Column(db.Float, nullable=False)
    to_address      = db.Column(db.String(128))
    from_address    = db.Column(db.String(128))
    status          = db.Column(db.String(20), default='pending', nullable=False)  # pending, completed, failed
    fee             = db.Column(db.Float, default=0.0)
    notes           = db.Column(db.Text)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at    = db.Column(db.DateTime)

    def __repr__(self):
        return f'<Transaction {self.tx_id} {self.type}>'


class Trade(db.Model):
    __tablename__ = 'trades'

    id              = db.Column(db.Integer, primary_key=True)
    trade_id        = db.Column(db.String(16), unique=True, nullable=False, default=lambda: secrets.token_hex(8))
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    pair            = db.Column(db.String(20), nullable=False)  # BTC/USDT
    side            = db.Column(db.String(10), nullable=False)  # BUY, SELL
    amount          = db.Column(db.Float, nullable=False)
    entry_price     = db.Column(db.Float, nullable=False)
    current_price   = db.Column(db.Float)
    status          = db.Column(db.String(20), default='open', nullable=False)  # open, closed
    pnl             = db.Column(db.Float, default=0.0)
    opened_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    closed_at       = db.Column(db.DateTime)

    user            = db.relationship('User', backref='trades')

    def __repr__(self):
        return f'<Trade {self.trade_id} {self.pair}>'


class Notification(db.Model):
    __tablename__ = 'notifications'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title           = db.Column(db.String(200), nullable=False)
    message         = db.Column(db.Text, nullable=False)
    type            = db.Column(db.String(20), default='info')  # info, warning, success, error
    is_read         = db.Column(db.Boolean, default=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<Notification {self.title}>'


class UserSettings(db.Model):
    __tablename__ = 'user_settings'

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True, nullable=False)
    two_fa_enabled  = db.Column(db.Boolean, default=False)
    two_fa_secret   = db.Column(db.String(32))
    email_notifications = db.Column(db.Boolean, default=True)
    trade_notifications = db.Column(db.Boolean, default=True)
    withdraw_notifications = db.Column(db.Boolean, default=True)
    theme           = db.Column(db.String(20), default='dark')
    language        = db.Column(db.String(10), default='en')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Settings user_id={self.user_id}>'


class MarketData(db.Model):
    __tablename__ = 'market_data'

    id              = db.Column(db.Integer, primary_key=True)
    symbol          = db.Column(db.String(10), nullable=False, index=True)
    name            = db.Column(db.String(100))
    rank            = db.Column(db.Integer)
    price           = db.Column(db.Float)
    change_24h      = db.Column(db.Float)
    volume_24h      = db.Column(db.Float)
    market_cap      = db.Column(db.Float)
    last_updated    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (db.Index('idx_symbol_updated', 'symbol', 'last_updated'),)

    def __repr__(self):
        return f'<MarketData {self.symbol}>'


class WalletHash(db.Model):
    __tablename__ = 'wallet_hashes'

    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False, index=True)
    address = db.Column(db.String(256), nullable=False)
    note = db.Column(db.String(256))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<WalletHash {self.symbol}>'


class SupportTicket(db.Model):
    __tablename__ = 'support_tickets'

    id              = db.Column(db.Integer, primary_key=True)
    ticket_id       = db.Column(db.String(16), unique=True, nullable=False, default=lambda: f"TKT-{secrets.token_hex(6).upper()}")
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject         = db.Column(db.String(200), nullable=False)
    status          = db.Column(db.String(20), default='open')  # open, in_progress, closed
    priority        = db.Column(db.String(20), default='medium')  # low, medium, high
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at       = db.Column(db.DateTime)

    user            = db.relationship('User', backref='support_tickets')
    messages        = db.relationship('SupportMessage', backref='ticket', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Ticket {self.ticket_id}>'


class SupportMessage(db.Model):
    __tablename__ = 'support_messages'

    id              = db.Column(db.Integer, primary_key=True)
    ticket_id       = db.Column(db.Integer, db.ForeignKey('support_tickets.id'), nullable=False)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message         = db.Column(db.Text, nullable=False)
    is_staff        = db.Column(db.Boolean, default=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user            = db.relationship('User')

    def __repr__(self):
        return f'<Message ticket={self.ticket_id}>'
