from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import User, PerformanceSummary, UploadLog
from forms import LoginForm, CreateUserForm  # Ensure CreateUserForm is imported
from app import app
from extensions import db

@app.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    return render_template('login.html', form=form)


@app.route('/dashboard')
@login_required
def dashboard():
    summaries = PerformanceSummary.query.filter_by(uploaded_by=current_user.id).order_by(PerformanceSummary.timestamp.desc()).limit(10).all()
    uploads = UploadLog.query.filter_by(user_id=current_user.id).order_by(UploadLog.timestamp.desc()).limit(5).all()
    return render_template('dashboard.html', summaries=summaries, uploads=uploads)

@app.route('/view_all_summaries')
@login_required
def view_all_summaries():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    summaries = PerformanceSummary.query.order_by(PerformanceSummary.timestamp.desc()).all()
    return render_template('all_summaries.html', summaries=summaries)

@app.route('/manage_users')
@login_required
def manage_users():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('manage_users.html', users=users)

@app.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    form = CreateUserForm()
    if form.validate_on_submit():
        new_user = User(
            username=form.username.data,
            role=form.role.data
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.commit()
        flash(f"User '{new_user.username}' created successfully.", 'success')
        return redirect(url_for('manage_users'))

    return render_template('create_user.html', form=form)
