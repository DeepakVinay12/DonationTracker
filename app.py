from flask import Flask, render_template, request, redirect, url_for, session, flash
import boto3
from boto3.dynamodb.conditions import Key, Attr
import uuid
from datetime import datetime
from botocore.exceptions import ClientError

app = Flask(__name__)
app.secret_key = 'your-secret-key'

# AWS Config
dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')  # Change region if needed
sns = boto3.client('sns', region_name='ap-south-1')

# Table references
user_table = dynamodb.Table('Users')
donation_table = dynamodb.Table('Donations')
campaign_table = dynamodb.Table('Campaigns')
organization_table = dynamodb.Table('organization')

# Replace with your SNS Topic ARN
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:099066653843:Donation_topic.fifo'

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user_type = request.form['user_type']

        response = user_table.get_item(Key={'email': email})
        user = response.get('Item')

        if user and user['password'] == password and user['user_type'] == user_type:
            session['email'] = user['email']
            session['name'] = user['name']
            session['user_type'] = user['user_type']

            if user_type.lower() == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user_type.lower() == 'organization':
                return redirect(url_for('organization_dashboard'))
            elif user_type.lower() == 'donor':
                return redirect(url_for('donor_dashboard'))
            else:
                flash("Unknown user type.", "error")
                return redirect(url_for('login'))
        else:
            flash("Invalid email or password.", "error")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        user_type = request.form.get('user_type')

        user_table.put_item(Item={
            'email': email,
            'name': name,
            'password': password,
            'user_type': user_type
        })
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/donor/dashboard', methods=['GET', 'POST'])
def donor_dashboard():
    if session.get('user_type', '').lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        donation_id = str(uuid.uuid4())
        donation_data = {
            'id': donation_id,
            'email': session['email'],
            'amount': float(request.form['amount']),
            'type': request.form['type'],
            'timestamp': datetime.now().isoformat(),
            'campaign_id': request.form.get('campaign_id', '')
        }

        donation_table.put_item(Item=donation_data)

        if donation_data['campaign_id']:
            campaign = campaign_table.get_item(Key={'id': donation_data['campaign_id']}).get('Item')
            if campaign:
                campaign['raised_amount'] += donation_data['amount']
                campaign_table.put_item(Item=campaign)

        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=f"New donation of ₹{donation_data['amount']} by {session['email']}",
            Subject="New Donation Received"
        )

    # Fetch donations safely
    try:
        response = donation_table.query(
            IndexName='email-index',
            KeyConditionExpression=Key('email').eq(session['email'])
        )
        donations = response.get('Items', [])
    except ClientError as e:
        if "ValidationException" in str(e):
            donations = donation_table.scan(
                FilterExpression=Attr('email').eq(session['email'])
            ).get('Items', [])
        else:
            raise e

    total = sum(d['amount'] for d in donations)
    campaigns = campaign_table.scan().get('Items', [])

    return render_template('donor_dashboard.html', total=total, campaigns=campaigns)

@app.route('/donation_history', methods=['GET', 'POST'])
def donation_history():
    if session.get('user_type', '').lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        donation_id = request.form.get('donation_id')
        if donation_id:
            donation_table.delete_item(Key={'id': donation_id})

    try:
        response = donation_table.query(
            IndexName='email-index',
            KeyConditionExpression=Key('email').eq(session['email'])
        )
        donations = response.get('Items', [])
    except ClientError as e:
        if "ValidationException" in str(e):
            donations = donation_table.scan(
                FilterExpression=Attr('email').eq(session['email'])
            ).get('Items', [])
        else:
            raise e

    donation_data = [{
        'id': d['id'],
        'campaign_name': d.get('campaign_id', 'N/A'),
        'donor_name': session.get('name', ''),
        'type': d['type'],
        'amount': d['amount'],
        'timestamp': d['timestamp'],
    } for d in donations]

    return render_template('donation_history.html', donations=donation_data)

@app.route('/organization/dashboard')
def organization_dashboard():
    if session.get('user_type', '').lower() != 'organization':
        return redirect(url_for('login'))

    try:
        campaigns = campaign_table.scan(
            FilterExpression=Attr('email').eq(session['email'])
        ).get('Items', [])
    except ClientError:
        campaigns = []

    return render_template('organization_dashboard.html', campaigns=campaigns)

@app.route('/organization/campaign/create', methods=['GET', 'POST'])
def create_campaign():
    if session.get('user_type', '').lower() != 'organization':
        return redirect(url_for('login'))

    if request.method == 'POST':
        campaign_table.put_item(Item={
            'id': str(uuid.uuid4()),
            'email': session['email'],
            'title': request.form['title'],
            'description': request.form['description'],
            'goal_amount': float(request.form['goal_amount']),
            'raised_amount': 0.0
        })
        flash("Campaign created successfully.", "success")
        return redirect(url_for('organization_dashboard'))
    return render_template('create_campaign.html')

@app.route('/organization/campaign/update/<campaign_id>', methods=['GET', 'POST'])
def update_campaign(campaign_id):
    if session.get('user_type', '').lower() != 'organization':
        return redirect(url_for('login'))

    campaign = campaign_table.get_item(Key={'id': campaign_id}).get('Item')

    if request.method == 'POST':
        campaign['title'] = request.form['title']
        campaign['description'] = request.form['description']
        campaign['goal_amount'] = float(request.form['goal_amount'])
        campaign_table.put_item(Item=campaign)
        flash("Campaign updated successfully.", "success")
        return redirect(url_for('organization_dashboard'))

    return render_template('update_campaign.html', campaign=campaign)

@app.route('/delete_campaign/<campaign_id>', methods=['POST'])
def delete_campaign(campaign_id):
    campaign_table.delete_item(Key={'id': campaign_id})
    flash('Campaign deleted successfully.', 'success')
    return redirect(url_for('organization_dashboard'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))
    return render_template('admin_dashboard.html')

@app.route('/admin/users')
def user_management():
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))

    users = user_table.scan().get('Items', [])
    return render_template('user_management.html', users=users)

@app.route('/admin/users/delete/<email>', methods=['POST'])
def delete_user(email):
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))

    if email == session.get('email'):
        return "You cannot delete yourself."

    user_table.delete_item(Key={'email': email})
    flash("User deleted successfully.", "success")

    return redirect(url_for('user_management'))

@app.route('/admin/reports')
def reports():
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))

    donations = donation_table.scan().get('Items', [])
    report_data = []
    for d in donations:
        campaign = campaign_table.get_item(Key={'id': d.get('campaign_id', '')}).get('Item', {})
        report_data.append({
            'email': d['email'],
            'campaign_title': campaign.get('title', 'N/A'),
            'amount': d['amount'],
            'timestamp': d['timestamp']
        })
    return render_template('reports.html', reports=report_data)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
