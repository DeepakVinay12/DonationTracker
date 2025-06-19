from flask import Flask, render_template, request, redirect, url_for, session
import boto3
import uuid
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key'

# Initialize DynamoDB and SNS clients
dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')  # Change region as needed
sns = boto3.client('sns', region_name='ap-south-1')

users_table = dynamodb.Table('Donorprofiles')
donations_table = dynamodb.Table('campaigndetails')
campaigns_table = dynamodb.Table('Transactionrecords')

# Replace with your SNS topic ARN
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:099066653843:DonationAlerts'

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user = {
            'email': request.form['email'],
            'name': request.form['name'],
            'password': request.form['password'],
            'user_type': request.form['user_type']
        }
        users_table.put_item(Item=user)
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user_type = request.form['user_type']

        response = users_table.get_item(Key={'email': email})
        user = response.get('Item')

        if user and user['password'] == password and user['user_type'] == user_type:
            session['email'] = email
            session['name'] = user['name']
            session['user_type'] = user_type

            if user_type.lower() == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user_type.lower() == 'organization':
                return redirect(url_for('organization_dashboard'))
            elif user_type.lower() == 'donor':
                return redirect(url_for('donor_dashboard'))

    return render_template('login.html')

@app.route('/donor/dashboard', methods=['GET', 'POST'])
def donor_dashboard():
    if 'user_type' not in session or session['user_type'].lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        donation_id = str(uuid.uuid4())
        donation = {
            'id': donation_id,
            'email': session['email'],
            'amount': float(request.form['amount']),
            'type': request.form['type'],
            'timestamp': datetime.now().isoformat(),
            'campaign_id': request.form.get('campaign_id', '')
        }
        donations_table.put_item(Item=donation)

        # Send SNS notification
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject='New Donation Received',
            Message=f"{session['name']} donated ₹{donation['amount']} ({donation['type']})"
        )

    # Fetch all campaigns
    campaigns = campaigns_table.scan().get('Items', [])
    return render_template('donor_dashboard.html', campaigns=campaigns, total=0)

@app.route('/organization/dashboard')
def organization_dashboard():
    if 'user_type' not in session or session['user_type'].lower() != 'organization':
        return redirect(url_for('login'))

    response = campaigns_table.scan()
    campaigns = [c for c in response.get('Items', []) if c['email'] == session['email']]
    return render_template('organization_dashboard.html', campaigns=campaigns)

@app.route('/organization/campaign/create', methods=['GET', 'POST'])
def create_campaign():
    if 'user_type' not in session or session['user_type'].lower() != 'organization':
        return redirect(url_for('login'))

    if request.method == 'POST':
        campaign = {
            'id': str(uuid.uuid4()),
            'email': session['email'],
            'title': request.form['title'],
            'description': request.form['description'],
            'goal_amount': float(request.form['goal_amount']),
            'raised_amount': 0.0
        }
        campaigns_table.put_item(Item=campaign)
        return redirect(url_for('organization_dashboard'))

    return render_template('create_campaign.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
