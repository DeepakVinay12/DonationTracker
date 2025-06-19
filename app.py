from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
import uuid
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key'

# SQLite DB config
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///donation.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Models
class User(db.Model):
    email = db.Column(db.String(100), primary_key=True)
    name = db.Column(db.String(100))
    password = db.Column(db.String(100))
    user_type = db.Column(db.String(50))

class Donation(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    email = db.Column(db.String(100), db.ForeignKey('user.email'))
    amount = db.Column(db.Float)
    type = db.Column(db.String(50))
    timestamp = db.Column(db.String(100))

class Campaign(db.Model):
    id = db.Column(db.String(100), primary_key=True)
    email = db.Column(db.String(100), db.ForeignKey('user.email'))
    title = db.Column(db.String(100))
    description = db.Column(db.Text)
    goal_amount = db.Column(db.Float)
    raised_amount = db.Column(db.Float)

@app.before_request
def create_tables_once():
    if not hasattr(app, 'tables_created'):
        db.create_all()
        app.tables_created = True

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user_type = request.form['user_type']

        user = User.query.filter_by(email=email, password=password, user_type=user_type).first()

        if user:
            session['email'] = user.email
            session['name'] = user.name
            session['user_type'] = user.user_type

            if user.user_type.lower() == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user.user_type.lower() == 'organization':
                return redirect(url_for('organization_dashboard'))
            elif user.user_type.lower() == 'donor':
                return redirect(url_for('donor_dashboard'))
            else:
                return render_template('login.html', error="Unknown user type.")
        else:
            return render_template('login.html', error="Invalid email or password.")

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        user_type = request.form.get('user_type')

        user = User(email=email, name=name, password=password, user_type=user_type)
        db.session.add(user)
        db.session.commit()

        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/donor/dashboard', methods=['GET', 'POST'])
def donor_dashboard():
    if 'user_type' not in session or session['user_type'].lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        donation = Donation(
            id=str(uuid.uuid4()),
            email=session['email'],
            amount=float(request.form['amount']),
            type=request.form['type'],
            timestamp=datetime.now().isoformat()
        )
        db.session.add(donation)

        # Optional: update campaign's raised amount if campaign ID was submitted
        campaign_id = request.form.get('campaign_id')
        if campaign_id:
            campaign = Campaign.query.get(campaign_id)
            if campaign:
                campaign.raised_amount += donation.amount

        db.session.commit()

    donations = Donation.query.filter_by(email=session['email']).all()
    total = sum(d.amount for d in donations)
    campaigns = Campaign.query.all()

    return render_template('donor_dashboard.html', total=total, campaigns=campaigns)


@app.route('/donation_history', methods=['GET', 'POST'])
def donation_history():
    if 'user_type' not in session or session['user_type'].lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        donation_id = request.form.get('donation_id')
        if donation_id:
            donation = Donation.query.get(donation_id)
            if donation and donation.email == session['email']:
                if donation.campaign_id:
                    campaign = Campaign.query.get(donation.campaign_id)
                    if campaign:
                        campaign.raised_amount -= donation.amount
                db.session.delete(donation)
                db.session.commit()

    donations = Donation.query.filter_by(email=session['email']).all()
    return render_template('donation_history.html', donations=donations)


@app.route('/organization/dashboard')
def organization_dashboard():
    if 'email' not in session or session.get('user_type').lower() != 'organization':
        return redirect(url_for('login'))

    campaigns = Campaign.query.filter_by(email=session['email']).all()
    return render_template('organization_dashboard.html', campaigns=campaigns)

@app.route('/organization/donations')
def organization_donations():
    if 'user_type' not in session or session['user_type'].lower() != 'organization':
        return redirect(url_for('login'))

    donations = Donation.query.all()  # You can filter by campaign later
    return render_template('organization_donations.html', donations=donations)



@app.route('/organization/campaign/create', methods=['GET', 'POST'])
def create_campaign():
    if 'user_type' not in session or session['user_type'].lower() != 'organization':
        return redirect(url_for('login'))

    if request.method == 'POST':
        campaign = Campaign(
            id=str(uuid.uuid4()),
            email=session['email'],
            title=request.form['title'],
            description=request.form['description'],
            goal_amount=float(request.form['goal_amount']),
            raised_amount=0.0
        )
        db.session.add(campaign)
        db.session.commit()
        return redirect(url_for('organization_dashboard'))

    return render_template('create_campaign.html')

@app.route('/organization/campaign/update/<campaign_id>', methods=['GET', 'POST'])
def update_campaign(campaign_id):
    if 'user_type' not in session or session['user_type'].lower() != 'organization':
        return redirect(url_for('login'))

    campaign = Campaign.query.get(campaign_id)

    if request.method == 'POST':
        campaign.title = request.form['title']
        campaign.description = request.form['description']
        campaign.goal_amount = float(request.form['goal_amount'])
        db.session.commit()
        return redirect(url_for('organization_dashboard'))

    return render_template('update_campaign.html', campaign=campaign)

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'user_type' not in session or session['user_type'].lower() != 'admin':
        return redirect(url_for('login'))

    return render_template('admin_dashboard.html')

@app.route('/admin/users')
def user_management():
    if 'user_type' not in session or session['user_type'].lower() != 'admin':
        return redirect(url_for('login'))

    users = User.query.all()
    return render_template('user_management.html', users=users)

@app.route('/admin/users/delete/<email>', methods=['POST'])
def delete_user(email):
    if 'user_type' not in session or session['user_type'].lower() != 'admin':
        return redirect(url_for('login'))

    if email == session.get('email'):
        return "You cannot delete your own account while logged in."

    user = User.query.get(email)

    if user:
        Donation.query.filter_by(email=email).delete()
        Campaign.query.filter_by(email=email).delete()
        db.session.delete(user)
        db.session.commit()

    return redirect(url_for('user_management'))

@app.route('/admin/reports')
def reports():
    if 'user_type' not in session or session['user_type'].lower() != 'admin':
        return redirect(url_for('login'))

    donations = Donation.query.all()
    summary = [
        f"{d.email} donated ${d.amount} on {d.timestamp}"
        for d in donations
    ]
    return render_template('reports.html', reports=summary)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
