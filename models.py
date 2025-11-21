from datetime import datetime
from extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(50), default='user')  # Optional: add role support

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    performance_summaries = db.relationship('PerformanceSummary', backref='user', lazy=True)

class PerformanceSummary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    ato_name = db.Column(db.String(100))
    total_amount = db.Column(db.Float)
    ebills = db.Column(db.String(50))
    paydirect = db.Column(db.String(50))
    date_uploaded = db.Column(db.DateTime, default=datetime.utcnow)

class PerformanceTarget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    target_amount = db.Column(db.Float)
    user = db.relationship('User', backref=db.backref('target', uselist=False))



class TaxEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tax_item = db.Column(db.String(100))
    subhead = db.Column(db.String(100))
    rrr = db.Column(db.String(100), unique=True, nullable=True)
    paydirect_ref = db.Column(db.String(100), unique=True, nullable=True)
    rrr_verified = db.Column(db.Boolean, default=False)
    paydirect_verified = db.Column(db.Boolean, default=False)
    rrr_amount = db.Column(db.Float)
    paydirect_amount = db.Column(db.Float)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User', backref='entries')
    data = db.Column(db.JSON)
    date_uploaded = db.Column(db.DateTime, default=datetime.utcnow)
    month = db.Column(db.Integer)  # ✅ NEW
    year = db.Column(db.Integer)   # ✅ NEW

class MonthlyLeagueSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.Integer)
    year = db.Column(db.Integer)
    data = db.Column(db.JSON)  # Store league table as JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
