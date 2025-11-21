# app.py (refactored & cleaned)
import os
import calendar 
from datetime import datetime, timezone 

from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file, make_response
)
from flask_login import (
    LoginManager, login_user, login_required, logout_user, current_user
)
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from sqlalchemy import extract, func, case
from sqlalchemy.exc import IntegrityError
from calendar import month_name
# Local imports (your modules)
from extensions import db, login_manager  # assumes these initialize DB and login
from models import User, TaxEntry, PerformanceTarget, PerformanceSummary
from forms import LoginForm, CreateUserForm, TaxEntryForm
from payment_api import verify_remita_rrr, verify_paydirect_reference

# Third-party libs used by export routes
import pandas as pd
from io import BytesIO

# App setup
app = Flask(__name__)
app.config.from_object('config.Config')
# DEV NOTE: use environment variable or secure vault for SECRET_KEY in production
app.config['SECRET_KEY'] = 'Sonnen@1989TheBusiness'
os.environ.get('SECRET_KEY', 'Sonnen@1989TheBusiness')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///birs.db')
app.config['WTF_CSRF_ENABLED'] = False  # OK for dev; enable in production

csrf = CSRFProtect(app)
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'
migrate = Migrate(app, db)

# ========== DASHBOARD DATA HELPERS ==========

def get_league_table_data():
    """Generate ranking of ATOs by percentage of target achieved."""
    league = []
    all_atos = User.query.filter_by(role='ato').all()

    for ato in all_atos:
        # Get all verified entries
        entries = TaxEntry.query.filter_by(uploaded_by=ato.id).all()
        total_returns = sum(
            (e.rrr_amount or 0) + (e.paydirect_amount or 0)
            for e in entries
            if e.rrr_verified or e.paydirect_verified
        )

        # Get target
        target = get_target_for_ato(ato)
        percent_met = round((total_returns / target * 100), 2) if target else 0

        league.append({
            'ato_name': ato.username,
            'target': target or 0,
            'total_returns': total_returns,
            'percent_met': percent_met
        })

    # Sort by performance descending
    league.sort(key=lambda x: x['percent_met'], reverse=True)
    return league

def get_analytics_data(from_date=None, to_date=None):
    """Compute monthly totals across verified entries, filtered by date range."""

    engine_name = db.engine.url.get_backend_name()
    if engine_name == 'sqlite':
        month_expr = func.strftime('%Y-%m', TaxEntry.date_uploaded)
    elif engine_name == 'postgresql':
        month_expr = func.to_char(TaxEntry.date_uploaded, 'YYYY-MM')
    else:  # MySQL or others
        month_expr = func.date_format(TaxEntry.date_uploaded, '%Y-%m')

    query = db.session.query(
        month_expr.label('month'),
        func.sum(
            (case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)) +
            (case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0))
        ).label('total')
    )

    if from_date and to_date:
        query = query.filter(
            TaxEntry.date_uploaded >= from_date,
            TaxEntry.date_uploaded <= to_date
        )

    results = query.group_by('month').order_by('month').all()

    data = [{'month': m or 'N/A', 'total': float(t or 0)} for m, t in results]
    print("DEBUG - Analytics data:", data)
    return data

# -------------------------
# Login loader
# -------------------------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# -------------------------
# Auth routes
# -------------------------
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash('Login successful.', 'success')

            # Redirect everyone to the unified dashboard
            return redirect(url_for('dashboard'))

        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)


# -------------------------
# Root & Dashboard routing
# -------------------------
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    total_atos = User.query.filter_by(role='ato').count()
    total_entries = TaxEntry.query.count()
    verified_count = TaxEntry.query.filter(
        (TaxEntry.rrr_verified == True) | (TaxEntry.paydirect_verified == True)
    ).count()

    return render_template('index.html',
                           total_atos=total_atos,
                           total_entries=total_entries,
                           verified_count=verified_count)



def get_analytics_data_filtered(from_date=None, to_date=None):
    """Return analytics data per month with totals for RRR, PayDirect, Total"""
    query = db.session.query(
        extract('month', TaxEntry.date_uploaded).label('month'),
        func.sum(case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)).label('rrr'),
        func.sum(case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0)).label('paydirect')
    )

    if from_date:
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d")
        query = query.filter(TaxEntry.date_uploaded >= from_date_obj)
    if to_date:
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d")
        query = query.filter(TaxEntry.date_uploaded <= to_date_obj)

    query = query.group_by(extract('month', TaxEntry.date_uploaded)).order_by('month')
    results = query.all()

    # Create full month list
    analytics = []
    months_seen = {int(r.month) for r in results}
    for m in range(1, 13):
        rrr = next((float(r.rrr) for r in results if int(r.month) == m), 0)
        paydirect = next((float(r.paydirect) for r in results if int(r.month) == m), 0)
        total = rrr + paydirect
        analytics.append({
            'month': m,
            'month_name': month_name[m],
            'rrr': rrr,
            'paydirect': paydirect,
            'total': total
        })

    return analytics

def get_user_summary(user_id):
    """Compute the performance summary for an ATO from TaxEntry data."""
    # Sum verified RRR and Paydirect amounts
    result = db.session.query(
        func.sum(TaxEntry.rrr_amount).label('rrr_total'),
        func.sum(TaxEntry.paydirect_amount).label('paydirect_total')
    ).filter(
        TaxEntry.uploaded_by == user_id,
        (TaxEntry.rrr_verified == True) | (TaxEntry.paydirect_verified == True)
    ).first()

    rrr_total = result.rrr_total or 0
    paydirect_total = result.paydirect_total or 0
    total = rrr_total + paydirect_total

    return {
        "rrr_total": rrr_total,
        "paydirect_total": paydirect_total,
        "total_amount": total
    }

def get_target_for_ato(user):
    target = PerformanceTarget.query.filter_by(user_id=user.id).first()
    return target.target_amount if target else 0


@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'ato':
        
        # existing ATO block (leave as is)
        entries = TaxEntry.query.filter_by(uploaded_by=current_user.id).order_by(TaxEntry.date_uploaded.desc()).all()

        total_returns = sum(
            (e.rrr_amount or 0) + (e.paydirect_amount or 0)
            for e in entries
            if e.rrr_verified or e.paydirect_verified
        )
        target = get_target_for_ato(current_user)
        target_amount = target if target else 0
        percent_met = round((total_returns / target_amount * 100), 2) if target_amount else 0

        monthly_data = (
            db.session.query(
                extract('month', TaxEntry.date_uploaded).label('month'),
                func.sum(case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)).label('rrr_total'),
                func.sum(case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0)).label('paydirect_total'),
            )
            .filter(TaxEntry.uploaded_by == current_user.id)
            .group_by(extract('month', TaxEntry.date_uploaded))
            .order_by('month')
            .all()
        )

        chart_labels = [calendar.month_name[int(row.month)] for row in monthly_data]
        ebills_values = [float(row.rrr_total or 0) for row in monthly_data]
        paydirect_values = [float(row.paydirect_total or 0) for row in monthly_data]
        total_values = [ebills_values[i] + paydirect_values[i] for i in range(len(ebills_values))]

        summaries = {
            'target': target_amount,
            'total': total_returns,
            'percent': percent_met,
            'records': entries,
            'chart_labels': chart_labels,
            'ebills_values': ebills_values,
            'paydirect_values': paydirect_values,
            'total_values': total_values
        }
        return render_template('dashboard.html', summaries=summaries, current_year=datetime.now().year)

    # --- ADMIN / CHAIRMAN VIEW ---

        # --- FILTER INPUTS ---
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    month = request.args.get('month')
    if month:
        month = int(month)
    else:
        month = None

    year = request.args.get('year', type=int)

    query = TaxEntry.query

    # Convert date strings → Python dates
    if from_date:
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d")
        query = query.filter(TaxEntry.date_uploaded >= from_date_obj)

    if to_date:
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d")
        query = query.filter(TaxEntry.date_uploaded <= to_date_obj)

    # Filter by month
    if month:
        query = query.filter(extract('month', TaxEntry.date_uploaded) == month)

    # Filter by year
    if year:
        query = query.filter(extract('year', TaxEntry.date_uploaded) == year)


     #Prepare chart data

    chart_data = (
        query.with_entities(
            TaxEntry.date_uploaded.label('date'),
            func.sum(case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)).label('rrr_total'),
            func.sum(case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0)).label('pay_total')
        )
        .group_by(func.date(TaxEntry.date_uploaded))
        .order_by(func.date(TaxEntry.date_uploaded))
        .all()
    )

    labels = [row.date.strftime("%d %b") for row in chart_data]
    rrr_values = [float(row.rrr_total or 0) for row in chart_data]
    pay_values = [float(row.pay_total or 0) for row in chart_data]
    total_values = [rrr_values[i] + pay_values[i] for i in range(len(rrr_values))]


    # League table summary grouped by ATO
    
    league_query = (
        db.session.query(
            User.id.label('user_id'),
            User.username.label('ATO'),
            func.sum(case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)).label('RRR'),
            func.sum(case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0)).label('Paydirect'),
            PerformanceTarget.target_amount.label('Target')
        )
        .join(TaxEntry, User.id == TaxEntry.uploaded_by)
        .outerjoin(PerformanceTarget, PerformanceTarget.user_id == User.id)
        .filter(User.role == 'ato')
    )
    # Apply date range filters
    if from_date:
        league_query = league_query.filter(TaxEntry.date_uploaded >= from_date)

    if to_date:
        league_query = league_query.filter(TaxEntry.date_uploaded <= to_date)

    # Apply month filter
    if month:
        league_query = league_query.filter(extract('month', TaxEntry.date_uploaded) == month)

    # Apply year filter
    if year:
        league_query = league_query.filter(extract('year', TaxEntry.date_uploaded) == year)

    league_table = (
    league_query
    .group_by(User.id, PerformanceTarget.target_amount)
    .order_by(func.sum(TaxEntry.rrr_amount + TaxEntry.paydirect_amount).desc())
    .all()
    )

    enriched_league = []
    for entry in league_table:
        total = (entry.RRR or 0) + (entry.Paydirect or 0)
        target = entry.Target or 0
        percent = round((total / target * 100), 1) if target else 0

        entry_dict = dict(entry._mapping)
        entry_dict['Percent'] = percent
        entry_dict['Actual'] = total

        enriched_league.append(entry_dict)

    # Sort by Percent descending
    enriched_league.sort(key=lambda x: x['Percent'], reverse=True)
         

    total_rrr = sum([float(r['RRR'] or 0) for r in enriched_league])
    total_paydirect = sum([float(r['Paydirect'] or 0) for r in enriched_league])
    total_all = total_rrr + total_paydirect
    avg_percent = round(sum([r['Percent'] for r in enriched_league]) / len(enriched_league), 1) if enriched_league else 0

    analytics = get_analytics_data_filtered(from_date, to_date)
    # --- Prepare summaries dictionary ---
    summaries = {
        'analytics': analytics,
        'league': enriched_league,
        'from_date': from_date,
        'to_date': to_date,
        'month': month,
        'year': year,
        # include your existing chart data as well:
        'labels': labels,
        'rrr_values': rrr_values,
        'pay_values': pay_values,
        'total_values': total_values,
        'total_rrr': total_rrr,
        'total_paydirect': total_paydirect,
        'grand_total': total_all,
        'avg_percent': avg_percent
    }
   # After you sort enriched_league by Percent descending:
    top_5 = enriched_league[:5]
    bottom_5 = enriched_league[-5:] if len(enriched_league) >= 5 else enriched_league[-len(enriched_league):]

    summaries['top5_labels'] = [r['ATO'] for r in top_5]
    summaries['top5_values'] = [r['Percent'] for r in top_5]

    summaries['bottom5_labels'] = [r['ATO'] for r in bottom_5]
    summaries['bottom5_values'] = [r['Percent'] for r in bottom_5]

    # Aggregate monthly totals for summary charts (Admin/Chairman)
    monthly_summary_data = (
        db.session.query(
            extract('month', TaxEntry.date_uploaded).label('month'),
            func.sum(case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)).label('rrr_total'),
            func.sum(case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0)).label('paydirect_total')
        )
        .filter(TaxEntry.date_uploaded != None)  # optional: adjust filters if needed
        .group_by(extract('month', TaxEntry.date_uploaded))
        .order_by('month')
        .all()
    )

    summaries['summary_chart_labels'] = [calendar.month_name[int(row.month)] for row in monthly_summary_data]
    summaries['summary_rrr_values'] = [float(row.rrr_total or 0) for row in monthly_summary_data]
    summaries['summary_paydirect_values'] = [float(row.paydirect_total or 0) for row in monthly_summary_data]
    summaries['summary_total_values'] = [
        summaries['summary_rrr_values'][i] + summaries['summary_paydirect_values'][i] 
        for i in range(len(summaries['summary_rrr_values']))
    ]

    return render_template('dashboard.html', summaries=summaries, current_year=datetime.now().year)

@app.route('/download-submissions', methods=['GET', 'POST'])
@login_required
def download_submissions():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    selected_ato_ids = request.args.getlist('ato_ids')  # only for admin

    query = TaxEntry.query

    if current_user.role == 'user':  # ATO
        query = query.filter_by(uploaded_by=current_user.id)
    elif current_user.role == 'admin':  # Admin
        if selected_ato_ids:
            query = query.filter(TaxEntry.uploaded_by.in_(selected_ato_ids))

    if start_date and end_date:
        query = query.filter(TaxEntry.date_uploaded.between(start_date, end_date))

    entries = query.all()

    # Convert to Excel
    data = [{
        'RRR': e.rrr,
        'Tax Item': e.tax_item,
        'Subhead': e.subhead,
        'Amount': e.rrr_amount or e.paydirect_amount,
        'Date Uploaded': e.date_uploaded,
        'Month': e.month,
        'Year': e.year,
        'Uploaded By': e.uploaded_by
    } for e in entries]

    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, download_name="submissions.xlsx", as_attachment=True)



@app.route('/dashboards')
@login_required
def dashboards():
    user = current_user
    summaries = None
    chart_data = []

    if user.role == 'ato':
        # ATO: calculate their totals and recent entries
        entries = TaxEntry.query.filter_by(uploaded_by=user.id).order_by(TaxEntry.date_uploaded.desc()).all()
        rrr_total = sum(e.rrr_amount or 0 for e in entries if e.rrr_verified)
        paydirect_total = sum(e.paydirect_amount or 0 for e in entries if e.paydirect_verified)
        combined_total = rrr_total + paydirect_total
        recent_records = [
            {
                'date': e.date_uploaded.strftime('%Y-%m-%d'),
                'rrr_amount': e.rrr_amount,
                'paydirect_amount': e.paydirect_amount,
                'total': (e.rrr_amount or 0) + (e.paydirect_amount or 0)
            } for e in entries[:10]  # last 10 entries
        ]

        summaries = {
            'rrr_total': rrr_total,
            'paydirect_total': paydirect_total,
            'total_amount': combined_total,
            'records': recent_records
        }

        # Chart: monthly totals
        monthly_totals = (
            db.session.query(extract('month', TaxEntry.date_uploaded).label('month'),
                             func.sum(TaxEntry.rrr_amount + TaxEntry.paydirect_amount))
            .filter_by(uploaded_by=user.id)
            .group_by('month')
            .order_by('month')
            .all()
        )
        chart_data = [0]*12
        for month, total in monthly_totals:
            chart_data[int(month)-1] = float(total)

    elif user.role in ['admin', 'chairman', 'director']:
        # Admin/Chairman: summary for all ATOs
        atos = User.query.filter_by(role='ato').all()
        summaries = []
        chart_data = [0]*12

        for ato in atos:
            entries = TaxEntry.query.filter_by(uploaded_by=ato.id).all()
            rrr_total = sum(e.rrr_amount or 0 for e in entries if e.rrr_verified)
            paydirect_total = sum(e.paydirect_amount or 0 for e in entries if e.paydirect_verified)
            combined_total = rrr_total + paydirect_total
            target = get_target_for_ato(ato) or 0
            percent = round((combined_total / target * 100), 2) if target else 0

            # recent entries (last 5)
            recent_records = [
                {
                    'date': e.date_uploaded.strftime('%Y-%m-%d'),
                    'rrr_amount': e.rrr_amount,
                    'paydirect_amount': e.paydirect_amount,
                    'total': (e.rrr_amount or 0) + (e.paydirect_amount or 0)
                } for e in sorted(entries, key=lambda x: x.date_uploaded, reverse=True)[:5]
            ]

            summaries.append({
                'username': ato.username,
                'rrr_total': rrr_total,
                'paydirect_total': paydirect_total,
                'total_amount': combined_total,
                'target': target,
                'percent': percent,
                'records': recent_records
            })

            # accumulate chart data
            monthly_totals = (
                db.session.query(extract('month', TaxEntry.date_uploaded).label('month'),
                                 func.sum(TaxEntry.rrr_amount + TaxEntry.paydirect_amount))
                .filter_by(uploaded_by=ato.id)
                .group_by('month')
                .all()
            )
            for month, total in monthly_totals:
                chart_data[int(month)-1] += float(total)

    return render_template('dashboard.html',
                           summaries=summaries,
                           chart_data=chart_data)


# -------------------------
# Manage users
# -------------------------
@app.route('/manage_users', methods=['GET', 'POST'])
@login_required
def manage_users():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    users = User.query.all()
    return render_template('manage_users.html', users=users)


# Create user (admin)
@app.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    form = CreateUserForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "warning")
            return redirect(url_for('create_user'))
        try:
            new_user = User(username=username, role=form.role.data)
            new_user.set_password(form.password.data)
            db.session.add(new_user)
            db.session.commit()
            flash(f"User '{new_user.username}' created successfully.", 'success')
            return redirect(url_for('manage_users'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
            return redirect(url_for('create_user'))

    return render_template('create_user.html', form=form)


# -------------------------
# View and enter Tax Entries
# -------------------------
@app.route('/view_entries')
@login_required
def view_entries():
    page = request.args.get('page', 1, type=int)
    per_page = 10

    tax_item = request.args.get('tax_item')
    date_filter = request.args.get('date')

    # Role-aware base query
    if current_user.role in ['admin', 'reviewer']:
        query = TaxEntry.query
    else:
        query = TaxEntry.query.filter_by(uploaded_by=current_user.id)

    # Optional filters
    if tax_item:
        query = query.filter(TaxEntry.tax_item.ilike(f"%{tax_item}%"))
    if date_filter:
        try:
            date_obj = datetime.strptime(date_filter, '%Y-%m-%d')
            query = query.filter(func.date(TaxEntry.date_uploaded) == date_obj.date())
        except ValueError:
            flash("Invalid date format. Use YYYY-MM-DD.", "warning")

    pagination = query.order_by(TaxEntry.date_uploaded.desc()).paginate(page=page, per_page=per_page)
    entries = pagination.items
    total_count = query.count()

    # Tax item breakdown (role-aware)
    if current_user.role in ['admin', 'reviewer']:
        breakdown_query = db.session.query(
            TaxEntry.tax_item,
            func.count(TaxEntry.id)
        ).group_by(TaxEntry.tax_item)
    else:
        breakdown_query = db.session.query(
            TaxEntry.tax_item,
            func.count(TaxEntry.id)
        ).filter_by(uploaded_by=current_user.id).group_by(TaxEntry.tax_item)

    tax_item_breakdown = breakdown_query.all()

    return render_template(
        'view_entries.html',
        entries=entries,
        pagination=pagination,
        tax_item=tax_item,
        date_filter=date_filter,
        total_count=total_count,
        tax_item_breakdown=tax_item_breakdown
    )

@app.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
def edit_user(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)
    new_role = request.form.get('role')
    if new_role in ['admin', 'reviewer', 'ato']:
        user.role = new_role
        db.session.commit()
        flash(f"Role updated for {user.username}.", "success")
    else:
        flash("Invalid role selected.", "warning")
    return redirect(url_for('manage_users'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for("manage_users"))

    try:
        db.session.delete(user)
        db.session.commit()
        flash(f"User {user.username} deleted successfully.", "info")
        print(f"Deleted user: {user.username}")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deleting user: {str(e)}", "danger")
        print(f"Deletion error: {e}")

    return redirect(url_for("manage_users"))


@app.route('/enter_tax_data', methods=['GET'])
@login_required
def enter_tax_data():
    form = TaxEntryForm()
    return render_template('enter_tax_data.html', form=form)


@app.route('/submit_tax_item', methods=['POST'])
@login_required
def submit_tax_item():
    tax_item = request.form.get('tax_item')
    road_subhead = request.form.get('road_subhead') if tax_item == 'Road' else None

    remita_rrr = request.form.get('remita_rrr')
    paydirect_ref = request.form.get('paydirect')

     # 🔒 Check for duplicate RRR
    if remita_rrr:
        existing_rrr = TaxEntry.query.filter_by(rrr=remita_rrr).first()
        if existing_rrr:
            flash("❌ This Remita RRR has already been used. Please enter a unique one.", "danger")
            return redirect(url_for('enter_tax_data'))

    # 🔒 Check for duplicate PayDirect
    if paydirect_ref:
        existing_paydirect = TaxEntry.query.filter_by(paydirect_ref=paydirect_ref).first()
        if existing_paydirect:
            flash("❌ This PayDirect reference has already been used. Please enter a unique one.", "danger")
            return redirect(url_for('enter_tax_data'))


    # call verification APIs (these functions should return dicts with 'verified' and 'amount')
    rrr_result = verify_remita_rrr(remita_rrr) if remita_rrr else {"verified": False, "amount": 0}
    paydirect_result = verify_paydirect_reference(paydirect_ref) if paydirect_ref else {"verified": False, "amount": 0}

    date_of_collection = datetime.utcnow().strftime('%Y-%m-%d')

    rrr_verified = rrr_result.get('verified', False)
    rrr_amount = rrr_result.get('amount', 0)

    paydirect_verified = paydirect_result.get('verified', False)
    paydirect_amount = paydirect_result.get('amount', 0)

    # store arbitrary form fields in data dict except the structured fields
    data = {key: value for key, value in request.form.items() if key not in ['tax_item', 'road_subhead', 'remita_rrr', 'paydirect']}
    data['date_of_collection'] = date_of_collection

    now = datetime.utcnow()
    month = now.month
    year = now.year

    new_entry = TaxEntry(
        tax_item=tax_item,
        subhead=road_subhead,
        uploaded_by=current_user.id,
        rrr=remita_rrr,
        rrr_verified=rrr_verified,
        rrr_amount=rrr_amount,
        paydirect_ref=paydirect_ref,
        paydirect_verified=paydirect_verified,
        paydirect_amount=paydirect_amount,
        data=data,
        date_uploaded=datetime.utcnow(),
        month=month,  
        year=year     
    )

    try:
        db.session.add(new_entry)
        db.session.commit()
        flash(f"{tax_item} entry submitted successfully.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("❌ Duplicate RRR or PayDirect reference detected. Entry not saved.", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving entry: {str(e)}", "danger")

    return redirect(url_for('enter_tax_data'))


@app.route('/delete_entry/<int:entry_id>', methods=['POST'])
@login_required
def delete_entry(entry_id):
    entry = TaxEntry.query.get_or_404(entry_id)
    if not entry.rrr_verified and not entry.paydirect_verified:
        try:
            db.session.delete(entry)
            db.session.commit()
            flash("Entry deleted successfully.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Error deleting entry: {str(e)}", "danger")
    else:
        flash("Cannot delete verified entry.", "danger")
    return redirect(url_for('enter_tax_data'))


# -------------------------
# Performance Tracker (simple)
# -------------------------
@app.route('/performance_tracker')
@login_required
def performance_tracker():
    target = PerformanceTarget.query.filter_by(user_id=current_user.id).first()
    # use SQL sum with coalesce to avoid None
    actual_sum = db.session.query(func.coalesce(func.sum(
        (func.coalesce(TaxEntry.rrr_amount, 0) + func.coalesce(TaxEntry.paydirect_amount, 0))
    ), 0)).filter_by(uploaded_by=current_user.id).scalar() or 0

    return render_template('performance_tracker.html', target=target, actual=actual_sum)


# -------------------------
# Analytics (user view) -> '/analytics'
# -------------------------
@app.route('/analytics')
@login_required
def analytics_dashboard():
    # user-level analytics: show monthly totals for current_user
    month = request.args.get('month', type=int)
    tax_item = request.args.get('tax_item')
    subhead = request.args.get('subhead')

    # build chart query with SQL coalesce (NULL-safe)
    chart_query = db.session.query(
        extract('month', TaxEntry.date_uploaded).label('month'),
        func.sum(func.coalesce(TaxEntry.rrr_amount, 0) + func.coalesce(TaxEntry.paydirect_amount, 0)).label('total')
    ).filter_by(uploaded_by=current_user.id)

    if month:
        chart_query = chart_query.filter(extract('month', TaxEntry.date_uploaded) == month)

    monthly_data = chart_query.group_by('month').all()

    # ensure JSON-friendly chart_data for Chart.js; fill months 1..12 for consistent display
    chart_data = []
    mon_map = {int(m): float(t) for m, t in monthly_data}
    for m in range(1, 13):
        chart_data.append({'month': m, 'total': mon_map.get(m, 0.0)})

    # submissions list
    entry_query = TaxEntry.query.filter_by(uploaded_by=current_user.id)
    if tax_item:
        entry_query = entry_query.filter(TaxEntry.tax_item.ilike(f"%{tax_item}%"))
    if subhead:
        entry_query = entry_query.filter(TaxEntry.subhead.ilike(f"%{subhead}%"))
    # avoid N+1 when rendering entry user info
    entries = entry_query.order_by(TaxEntry.date_uploaded.desc()).all()

    # performance numbers
    target = PerformanceTarget.query.filter_by(user_id=current_user.id).first()
    actual = sum(
        (e.rrr_amount or 0) + (e.paydirect_amount or 0)
        for e in entries if e.rrr_verified or e.paydirect_verified
    )

    return render_template(
        'analytics_dashboard.html',
        chart_data=chart_data,
        entries=entries,
        target=target,
        actual=actual
    )


# -------------------------
# Admin analytics (aggregate across ATOs)
# renamed from '/analytic' to '/analytics_admin' to avoid ambiguity
# -------------------------
@app.route('/analytics_admin')
@login_required
def analytics_admin():
    if current_user.role not in ['admin', 'reviewer']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    # aggregate verified totals by ATO username (use JOINs to be more efficient)
    ato_totals = {}
    # load all entries with uploader users to avoid N+1
    entries = TaxEntry.query.all()
    for entry in entries:
        user = User.query.get(entry.uploaded_by)
        if not user or user.role != 'ato':
            continue
        ato = user.username
        if ato not in ato_totals:
            ato_totals[ato] = 0
        if entry.rrr_verified:
            ato_totals[ato] += entry.rrr_amount or 0
        if entry.paydirect_verified:
            ato_totals[ato] += entry.paydirect_amount or 0

    sorted_analytics = sorted(ato_totals.items(), key=lambda x: x[1], reverse=True)
    return render_template('analytics.html', analytics=sorted_analytics)


@app.route('/export_analytics')
@login_required
def export_analytics():
    if current_user.role not in ['admin', 'reviewer']:
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    entries = TaxEntry.query.all()
    data = {}

    for entry in entries:
        user = User.query.get(entry.uploaded_by)
        if not user or user.role != 'ato':
            continue
        ato = user.username
        if ato not in data:
            data[ato] = 0
        if entry.rrr_verified:
            data[ato] += entry.rrr_amount or 0
        if entry.paydirect_verified:
            data[ato] += entry.paydirect_amount or 0

    df = pd.DataFrame(list(data.items()), columns=['ATO Name', 'Total Verified Amount'])
    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, download_name='analytics_export.csv', as_attachment=True)


# -------------------------
# League / Comparison routes
# -------------------------
# 🏆 Updated league_table route (SQLAlchemy 2.0 compatible)


def get_target_for_ato(user):
    """
    Fetches the performance target for an ATO (User object).
    Returns 0 if no target is found.
    """
    if not user:
        return 0

    target_record = db.session.query(PerformanceTarget).filter_by(user_id=user.id).first()
    return target_record.target_amount if target_record else 0

@app.route('/league-table')
@login_required
def league_table():
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')

    # Base query for performance ranking
    query = db.session.query(
        User.id.label('user_id'),
        User.username.label('ATO'),
        func.sum(case((TaxEntry.rrr_verified, TaxEntry.rrr_amount), else_=0)).label('RRR'),
        func.sum(case((TaxEntry.paydirect_verified, TaxEntry.paydirect_amount), else_=0)).label('Paydirect'),
        PerformanceTarget.target_amount.label('Target')
    ).join(TaxEntry, User.id == TaxEntry.uploaded_by
    ).outerjoin(PerformanceTarget, PerformanceTarget.user_id == User.id
    ).filter(User.role == 'ato')

    # Apply date filters if provided
    if from_date:
        query = query.filter(TaxEntry.date_uploaded >= from_date)
    if to_date:
        query = query.filter(TaxEntry.date_uploaded <= to_date)

    # Group results
    query = query.group_by(User.id, User.username, PerformanceTarget.target_amount)
    league = query.all()

    # Enrich and compute performance %
    enriched_league = []
    for entry in league:
        rrr = entry.RRR or 0
        paydirect = entry.Paydirect or 0
        target = entry.Target or 0
        total = rrr + paydirect
        percent = round((total / target * 100), 2) if target > 0 else 0

        enriched_league.append({
            'user_id': entry.user_id,
            'ATO': entry.ATO,
            'RRR': rrr,
            'Paydirect': paydirect,
            'Target': target,
            'Actual': total,
            'Percent': percent
        })

    # ✅ Always force numeric sort before render
    enriched_league.sort(key=lambda x: float(x['Percent']), reverse=True)

    # Prevent caching (so browser doesn’t reuse stale table)
    response = make_response(render_template(
        'league_table.html',
        league=enriched_league,
        from_date=from_date,
        to_date=to_date
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'

    return response


@app.route('/create_ato', methods=['GET', 'POST'])
@login_required
def create_ato():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password')
        target_amount = request.form.get('target_amount')

        if not username or not password or not target_amount:
            flash("All fields are required.", "warning")
            return redirect(url_for('create_ato'))

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "warning")
            return redirect(url_for('create_ato'))

        try:
            new_user = User(username=username, role='ato')
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()

            target = PerformanceTarget(
                user_id=new_user.id,
                target_amount=float(target_amount)
            )
            db.session.add(target)
            db.session.commit()

            flash(f"ATO '{username}' created with fixed target.", "success")
            return redirect(url_for('league_table'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")
            return redirect(url_for('create_ato'))

    return render_template('create_ato.html')


@app.route('/compare_atos')
@login_required
def compare_atos():
    month = request.args.get('month')
    lga = request.args.get('lga')

    users_query = User.query.filter_by(role='ato')
    if lga:
        users_query = users_query.filter_by(lga=lga)
    users = users_query.all()

    chart_data = {}
    labels = []

    for user in users:
        summaries_q = PerformanceSummary.query.filter_by(uploaded_by=user.id).order_by(PerformanceSummary.date_uploaded.asc())
        if month:
            try:
                year, m = map(int, month.split('-'))
                summaries_q = summaries_q.filter(
                    extract('year', PerformanceSummary.date_uploaded) == year,
                    extract('month', PerformanceSummary.date_uploaded) == m
                )
            except Exception:
                pass
        summaries = summaries_q.all()
        chart_data[user.username] = [s.total_amount for s in summaries]
        if not labels and summaries:
            labels = [s.date_uploaded.strftime('%d %b') for s in summaries]

    rankings = sorted(chart_data.items(), key=lambda x: sum(x[1]), reverse=True)
    all_lgas = db.session.query(User.lga).distinct().all()
    lgas = [l[0] for l in all_lgas if l[0]]
    return render_template('compare_atos.html',
                           chart_data=chart_data,
                           chart_labels=labels,
                           rankings=rankings,
                           lgas=lgas)


# -------------------------
# ATO detail view
# -------------------------

@app.route("/ato/<int:user_id>")
@login_required
def ato_detail(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role != "ato":
        flash("ATO not found or invalid access.", "warning")
        return redirect(url_for("league_table"))

    # 🗓 Get date range filters from query parameters
    from_date_str = request.args.get('from_date')
    to_date_str = request.args.get('to_date')

    query = TaxEntry.query.filter_by(uploaded_by=user.id)

    # Apply date range filter if provided
    if from_date_str and to_date_str:
        try:
            # Accept both "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM" formats
            from_date = datetime.strptime(from_date_str, "%Y-%m-%dT%H:%M") if "T" in from_date_str else datetime.strptime(from_date_str, "%Y-%m-%d")
            to_date = datetime.strptime(to_date_str, "%Y-%m-%dT%H:%M") if "T" in to_date_str else datetime.strptime(to_date_str, "%Y-%m-%d")

            # Ensure timezone consistency if your app uses UTC
            from_date = from_date.replace(tzinfo=timezone.utc)
            to_date = to_date.replace(tzinfo=timezone.utc)

            query = query.filter(TaxEntry.date_uploaded >= from_date, TaxEntry.date_uploaded <= to_date)
        except ValueError:
            flash("Invalid date range format. Showing all records.", "warning")

    # Retrieve filtered entries
    filtered_entries = query.all()

    # 🧮 Calculate performance stats
    total_returns = sum(
        (e.rrr_amount or 0) + (e.paydirect_amount or 0)
        for e in filtered_entries
        if e.rrr_verified or e.paydirect_verified
    )

    target = get_target_for_ato(user)
    percent_met = round((total_returns / target * 100), 2) if target else 0

    # Last recorded entry date
    last_entry = (
        max((e.date_uploaded for e in filtered_entries), default=None)
        .strftime("%Y-%m-%d")
        if filtered_entries else "—"
    )

    # 📊 Data for Chart.js
    chart_labels = [e.date_uploaded.strftime("%Y-%m-%d") for e in filtered_entries]
    total_values = [
        (e.rrr_amount or 0) + (e.paydirect_amount or 0) for e in filtered_entries
    ]
    ebills_values = [e.rrr_amount or 0 for e in filtered_entries]
    paydirect_values = [e.paydirect_amount or 0 for e in filtered_entries]

    # 🧾 Render template
    return render_template(
        "ato_detail.html",
        user=user,
        total_returns=total_returns,
        target={"target_amount": target},
        percent_met=percent_met,
        last_entry=last_entry,
        chart_labels=chart_labels,
        total_values=total_values,
        ebills_values=ebills_values,
        paydirect_values=paydirect_values,
        from_date=from_date_str,
        to_date=to_date_str,
    )


# -------------------------
# Generic submit_entry used in some templates
# -------------------------
@app.route('/submit_entry', methods=['POST'])
@login_required
def submit_entry():
    rrr = request.form.get('rrr')
    paydirect_ref = request.form.get('paydirect_ref')

    rrr_result = verify_remita_rrr(rrr) if rrr else {"verified": False, "amount": 0}
    paydirect_result = verify_paydirect_reference(paydirect_ref) if paydirect_ref else {"verified": False, "amount": 0}

    entry = TaxEntry(
        tax_item=request.form.get('tax_item'),
        subhead=request.form.get('subhead'),
        uploaded_by=current_user.id,
        rrr=rrr,
        paydirect_ref=paydirect_ref,
        rrr_verified=rrr_result.get('verified', False),
        paydirect_verified=paydirect_result.get('verified', False),
        rrr_amount=rrr_result.get('amount', 0),
        paydirect_amount=paydirect_result.get('amount', 0),
        data=request.form.to_dict(),
        date_uploaded=datetime.utcnow()
    )
    try:
        db.session.add(entry)
        db.session.commit()
        flash("Entry submitted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving entry: {str(e)}", "danger")

    return redirect(url_for('my_submissions'))


# -------------------------
# Reverify route
# -------------------------
@app.route('/reverify/<int:entry_id>', methods=['POST'])
@login_required
def reverify_entry(entry_id):
    entry = TaxEntry.query.get_or_404(entry_id)

    if entry.rrr:
        rrr_result = verify_remita_rrr(entry.rrr)
        entry.rrr_verified = rrr_result.get('verified', False)
        entry.rrr_amount = rrr_result.get('amount', 0)

    if entry.paydirect_ref:
        paydirect_result = verify_paydirect_reference(entry.paydirect_ref)
        entry.paydirect_verified = paydirect_result.get('verified', False)
        entry.paydirect_amount = paydirect_result.get('amount', 0)

    try:
        db.session.commit()
        flash("Verification updated.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating verification: {str(e)}", "danger")

    return redirect(url_for('my_submissions'))


# -------------------------
# Exports: Excel & PDF for user's submissions
# -------------------------
@app.route('/export_excel')
@login_required
def export_excel():
    # Get filters
    month = request.args.get('month', type=int)
    tax_item = request.args.get('tax_item')
    subhead = request.args.get('subhead')

    query = TaxEntry.query.filter_by(uploaded_by=current_user.id)
    if tax_item:
        query = query.filter(TaxEntry.tax_item.ilike(f"%{tax_item}%"))
    if subhead:
        query = query.filter(TaxEntry.subhead.ilike(f"%{subhead}%"))
    if month:
        query = query.filter(extract('month', TaxEntry.date_uploaded) == month)

    entries = query.all()

    data = []
    for entry in entries:
        row = {
            'Tax Item': entry.tax_item,
            'Subhead': entry.subhead or '',
            'Date Uploaded': entry.date_uploaded.strftime('%Y-%m-%d'),
            'RRR': entry.rrr or '',
            'RRR Verified': 'Yes' if entry.rrr_verified else 'No',
            'RRR Amount': entry.rrr_amount or 0,
            'PayDirect Ref': entry.paydirect_ref or '',
            'PayDirect Verified': 'Yes' if entry.paydirect_verified else 'No',
            'PayDirect Amount': entry.paydirect_amount or 0,
        }
        if entry.data:
            row.update(entry.data)
        data.append(row)

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Submissions')
    output.seek(0)
    return send_file(output, download_name='submissions.xlsx', as_attachment=True)


@app.route('/export_pdf')
@login_required
def export_pdf():
    # NOTE: xhtml2pdf must be installed; this route uses a simple render_template_string as before
    from xhtml2pdf import pisa
    from io import BytesIO
    from flask import render_template_string

    month = request.args.get('month', type=int)
    tax_item = request.args.get('tax_item')
    subhead = request.args.get('subhead')

    query = TaxEntry.query.filter_by(uploaded_by=current_user.id)
    if tax_item:
        query = query.filter(TaxEntry.tax_item.ilike(f"%{tax_item}%"))
    if subhead:
        query = query.filter(TaxEntry.subhead.ilike(f"%{subhead}%"))
    if month:
        query = query.filter(extract('month', TaxEntry.date_uploaded) == month)

    entries = query.all()

    html = render_template_string("""
    <html>
    <head>
      <style>
        body { font-family: Arial, sans-serif; }
        h2 { text-align: center; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #333; padding: 6px; text-align: left; font-size: 12px; }
        th { background-color: #f2f2f2; }
      </style>
    </head>
    <body>
      <h2>Tax Submissions Report</h2>
      <table>
        <tr>
          <th>Tax Item</th>
          <th>Subhead</th>
          <th>Date Uploaded</th>
          <th>Details</th>
        </tr>
        {% for entry in entries %}
        <tr>
          <td>{{ entry.tax_item }}</td>
          <td>{{ entry.subhead or '—' }}</td>
          <td>{{ entry.date_uploaded.strftime('%Y-%m-%d') }}</td>
          <td>
            {% if entry.data %}
              {% for key, value in entry.data.items() %}
                <strong>{{ key.replace('_', ' ').title() }}:</strong> {{ value }}<br>
              {% endfor %}
            {% else %}
              No details
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </body>
    </html>
    """, entries=entries)

    output = BytesIO()
    pisa.CreatePDF(html, dest=output)
    output.seek(0)
    return send_file(output, download_name='submissions.pdf', as_attachment=True)


# -------------------------
# Final guard / run
# -------------------------
if __name__ == '__main__':
    # debug mode ON for development; set to False in production
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
