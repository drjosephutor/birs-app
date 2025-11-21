from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=150)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    submit = SubmitField('Login')

class CreateUserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=150)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    role = StringField('Role', default='user')  # Optional: allow admin to assign role
    submit = SubmitField('Create User')

class TaxEntryForm(FlaskForm):
    pass  # No fields needed since you're generating them dynamically


class TaxEntryForm(FlaskForm):
    class Meta:
        csrf = False  # Disable CSRF for development/testing
