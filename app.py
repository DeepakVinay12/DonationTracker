from flask import Flask, render_template, request, redirect, url_for, session, flash
import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
import uuid
from datetime import datetime
from decimal import Decimal  # ✅ use Decimal for DynamoDB numeric types

app = Flask(__name__)
app.secret_key = 'your-secret-key'  # keep non-empty

# --- AWS config ---
REGION = 'ap-south-1'  # change if needed
dynamodb = boto3.resource('dynamodb', region_name=REGION)
sns = boto3.client('sns', region_name=REGION)

# --- DynamoDB tables ---
user_table = dynamodb.Table('Users')
donation_table = dynamodb.Table('Donations')
campaign_table = dynamodb.Table('Campaigns')
organization_table = dynamodb.Table('organization')  # if you query it later

# --- SNS topic (FIFO) ---
SNS_TOPIC_ARN = 'arn:aws:sns:ap-south-1:099066653843:Donation_topic.fifo'


# ---------- helpers ----------
def query_by_email_or_scan(table, email, index_name='email-index'):
    """
    Try to Query on a GSI 'email-index'. If missing or invalid, fallback to Scan with Attr('email') == email.
    Returns a list of items.
    """
    try:
        resp = table.query(
            IndexName=index_name,
            KeyConditionExpression=Key('email').eq(email)
        )
        items = resp.get('Items', [])
        print(f"[DDB] query using index '{index_name}' returned {len(items)} items")
        return items
    except ClientError as e:
        msg = str(e)
        if 'ValidationException' in msg or 'Requested resource not found' in msg:
            print(f"[DDB] index '{index_name}' missing; falling back to scan()")
            resp = table.scan(
                FilterExpression=Attr('email').eq(email)
            )
            items = resp.get('Items', [])
            print(f"[DDB] scan fallback returned {len(items)} items")
            return items
        raise


def safe_publish_sns(subject, message):
    """
    Publish to FIFO SNS topic. Requires MessageGroupId. Add a unique dedupe id as well.
    """
    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],  # SNS caps subject length
            Message=message,
            MessageGroupId="donations",  # any consistent group
            MessageDeduplicationId=str(uuid.uuid4())
        )
        print("[SNS] Published notification")
    except ClientError as e:
        print(f"[SNS] Publish failed: {e}")


def convert_floats(obj):
    """
    Recursively convert Python float to Decimal for DynamoDB.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_floats(v) for v in obj]
    return obj


# ---------- routes ----------
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/health')
def health():
    return "OK"


@app.route('/whoami')
def whoami():
    return {
        "email": session.get('email'),
        "name": session.get('name'),
        "user_type": session.get('user_type')
    }


# -------- auth --------
@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Users table PK = email (assumed). We use get_item for primary key lookup.
    Expected form fields: email, password, user_type
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user_type = request.form.get('user_type', '').strip()

        print(f"[LOGIN] Attempt for {email} as {user_type}")

        try:
            resp = user_table.get_item(Key={'email': email})
            user = resp.get('Item')
        except ClientError as e:
            print(f"[LOGIN] DDB get_item error: {e}")
            user = None

        if user and user.get('password') == password and user.get('user_type', '').lower() == user_type.lower():
            # set session
            session['email'] = user.get('email')
            session['name'] = user.get('name', '')
            session['user_type'] = user.get('user_type', '')
            print(f"[LOGIN] Success: {session['email']} -> {session['user_type']}")

            # redirect by role
            role = session['user_type'].lower()
            if role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif role == 'organization':
                return redirect(url_for('organization_dashboard'))
            elif role == 'donor':
                return redirect(url_for('donor_dashboard'))

            flash("Unknown user type.", "error")
            return redirect(url_for('login'))

        flash("Invalid email or password.", "error")
        return redirect(url_for('login'))

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Expected fields: name, email, password, user_type (Admin | Organization | Donor)
    """
    if request.method == 'POST':
        item = {
            'email': request.form.get('email', '').strip(),
            'name': request.form.get('name', '').strip(),
            'password': request.form.get('password', ''),
            'user_type': request.form.get('user_type', '').strip()
        }
        print(f"[REGISTER] Creating user {item['email']} ({item['user_type']})")

        try:
            user_table.put_item(Item=item)
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for('login'))
        except ClientError as e:
            print(f"[REGISTER] put_item failed: {e}")
            flash("Registration failed. Check logs.", "error")
            return redirect(url_for('register'))

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# -------- donor --------
@app.route('/donor/dashboard', methods=['GET', 'POST'])
def donor_dashboard():
    if session.get('user_type', '').lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        # create donation
        try:
            amount = float(request.form['amount'])
        except Exception:
            flash("Invalid amount.", "error")
            return redirect(url_for('donor_dashboard'))

        donation_id = str(uuid.uuid4())
        donation_data = {
            'id': donation_id,                    # assume PK in Donations is 'id'
            'email': session['email'],
            'amount': amount,
            'type': request.form.get('type', 'General'),
            'timestamp': datetime.utcnow().isoformat(),
            'campaign_id': request.form.get('campaign_id', '')
        }

        try:
            donation_table.put_item(Item=convert_floats(donation_data))
            print(f"[DONATION] Saved donation {donation_id}")

            # Update campaign raised amount if campaign provided
            if donation_data['campaign_id']:
                camp_key = {'id': donation_data['campaign_id']}
                camp_resp = campaign_table.get_item(Key=camp_key)
                campaign = camp_resp.get('Item')
                if campaign:
                    campaign['raised_amount'] = float(campaign.get('raised_amount', 0.0)) + amount
                    campaign_table.put_item(Item=convert_floats(campaign))
                    print(f"[DONATION] Updated campaign {campaign['id']} raised_amount")

            # SNS notify (FIFO safe)
            safe_publish_sns(
                subject="New Donation Received",
                message=f"New donation of ₹{donation_data['amount']} by {session['email']}"
            )
        except ClientError as e:
            print(f"[DONATION] Error: {e}")
            flash("Could not save donation. Check logs.", "error")

    # Load donor totals (query or scan)
    donations = query_by_email_or_scan(donation_table, session['email'])
    total = sum(float(d.get('amount', 0)) for d in donations)

    # All campaigns (simple scan)
    try:
        campaigns = campaign_table.scan().get('Items', [])
    except ClientError as e:
        print(f"[DDB] campaign scan error: {e}")
        campaigns = []

    return render_template('donor_dashboard.html', total=total, campaigns=campaigns)


@app.route('/donation_history', methods=['GET', 'POST'])
def donation_history():
    if session.get('user_type', '').lower() != 'donor':
        return redirect(url_for('login'))

    if request.method == 'POST':
        donation_id = request.form.get('donation_id')
        if donation_id:
            try:
                donation_table.delete_item(Key={'id': donation_id})
                flash("Donation deleted.", "success")
            except ClientError as e:
                print(f"[DONATION] delete failed: {e}")
                flash("Could not delete donation.", "error")

    donations = query_by_email_or_scan(donation_table, session['email'])
    donation_data = [{
        'id': d.get('id'),
        'campaign_name': d.get('campaign_id', 'N/A'),
        'donor_name': session.get('name', ''),
        'type': d.get('type', ''),
        'amount': float(d.get('amount', 0)),
        'timestamp': d.get('timestamp', '')
    } for d in donations]

    return render_template('donation_history.html', donations=donation_data)


# -------- organization --------
@app.route('/organization/dashboard')
def organization_dashboard():
    if session.get('user_type', '').lower() != 'organization':
        return redirect(url_for('login'))

    # campaigns owned by this org (query index if exists else scan)
    campaigns = query_by_email_or_scan(campaign_table, session['email'])
    return render_template('organization_dashboard.html', campaigns=campaigns)


@app.route('/organization_donations')
def organization_donations():
    # Allow only organization or admin users
    if session.get('user_type', '').lower() not in ['organization', 'admin']:
        return redirect(url_for('login'))

    try:
        # Fetch all donation records from DynamoDB
        response = donation_table.scan()
        donations = response.get('Items', [])
    except ClientError as e:
        print(f"[ERROR] Could not fetch donations: {e}")
        donations = []

    # Prepare data for the HTML table
    donation_data = []
    for d in donations:
        donation_data.append({
            'email': d.get('email', 'Unknown'),
            'amount': float(d.get('amount', 0)),
            'type': d.get('type', 'N/A'),
            'timestamp': d.get('timestamp', 'N/A')
        })

    return render_template('organization_donation.html', donations=donation_data)


@app.route('/organization/campaign/create', methods=['GET', 'POST'])
def create_campaign():
    if session.get('user_type', '').lower() != 'organization':
        return redirect(url_for('login'))

    if request.method == 'POST':
        item = {
            'id': str(uuid.uuid4()),      # assume PK for Campaigns is 'id'
            'email': session['email'],
            'title': request.form['title'],
            'description': request.form['description'],
            'goal_amount': float(request.form['goal_amount']),
            'raised_amount': 0.0
        }
        try:
            campaign_table.put_item(Item=convert_floats(item))
            flash("Campaign created.", "success")
            return redirect(url_for('organization_dashboard'))
        except ClientError as e:
            print(f"[CAMPAIGN] create failed: {e}")
            flash("Could not create campaign.", "error")

    return render_template('create_campaign.html')


@app.route('/organization/campaign/update/<campaign_id>', methods=['GET', 'POST'])
def update_campaign(campaign_id):
    if session.get('user_type', '').lower() != 'organization':
        return redirect(url_for('login'))

    # Load campaign
    camp_resp = campaign_table.get_item(Key={'id': campaign_id})
    campaign = camp_resp.get('Item')
    if not campaign:
        flash("Campaign not found.", "error")
        return redirect(url_for('organization_dashboard'))

    if request.method == 'POST':
        campaign['title'] = request.form['title']
        campaign['description'] = request.form['description']
        campaign['goal_amount'] = float(request.form['goal_amount'])
        try:
            campaign_table.put_item(Item=convert_floats(campaign))
            flash("Campaign updated.", "success")
            return redirect(url_for('organization_dashboard'))
        except ClientError as e:
            print(f"[CAMPAIGN] update failed: {e}")
            flash("Could not update campaign.", "error")

    return render_template('update_campaign.html', campaign=campaign)


@app.route('/delete_campaign/<campaign_id>', methods=['POST'])
def delete_campaign(campaign_id):
    try:
        campaign_table.delete_item(Key={'id': campaign_id})
        flash('Campaign deleted.', 'success')
    except ClientError as e:
        print(f"[CAMPAIGN] delete failed: {e}")
        flash('Could not delete campaign.', 'error')
    return redirect(url_for('organization_dashboard'))


# -------- admin --------
@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))
    return render_template('admin_dashboard.html')


@app.route('/admin/users')
def user_management():
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))
    try:
        users = user_table.scan().get('Items', [])
    except ClientError as e:
        print(f"[ADMIN] users scan failed: {e}")
        users = []
    return render_template('user_management.html', users=users)


@app.route('/admin/users/delete/<email>', methods=['POST'])
def delete_user(email):
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))

    if email == session.get('email'):
        flash("You cannot delete yourself.", "warning")
        return redirect(url_for('user_management'))

    try:
        user_table.delete_item(Key={'email': email})
        flash("User deleted.", "success")
    except ClientError as e:
        print(f"[ADMIN] delete user failed: {e}")
        flash("Could not delete user.", "error")

    return redirect(url_for('user_management'))


@app.route('/admin/reports')
def reports():
    if session.get('user_type', '').lower() != 'admin':
        return redirect(url_for('login'))

    try:
        donations = donation_table.scan().get('Items', [])
    except ClientError as e:
        print(f"[ADMIN] donations scan failed: {e}")
        donations = []

    report_data = []
    for d in donations:
        camp_id = d.get('campaign_id', '')
        try:
            camp = campaign_table.get_item(Key={'id': camp_id}).get('Item', {}) if camp_id else {}
        except ClientError:
            camp = {}
        report_data.append({
            'email': d.get('email'),
            'campaign_title': camp.get('title', 'N/A'),
            'amount': float(d.get('amount', 0)),
            'timestamp': d.get('timestamp', '')
        })

    return render_template('reports.html', reports=report_data)


# -------- run --------
if __name__ == '__main__':
    # On EC2, keep host=0.0.0.0
    app.run(host='0.0.0.0', port=5000, debug=True)


