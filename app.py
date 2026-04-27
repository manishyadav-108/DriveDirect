from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import random
import os
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_drivedirect_key'

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 # Limits images to 5MB
# ------------------------------------------

# Database Configuration
#app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:free@localhost/DriveDirect'
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://postgres:yourpassword@localhost/yourdatabase')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)



# 1. This catches the duration when they click "Book Now" under a car picture
@app.route('/save_duration', methods=['POST'])
def save_duration():
    # Save the duration to the session securely
    session['duration_amount'] = int(request.form.get('duration_amount'))
    session['duration_unit'] = request.form.get('duration_unit')
    
    # Send them down to the main booking form to pick driver and vehicle type
    return redirect(url_for('index', _anchor='book-ride'))


# 2. This runs when they click "Check Fare" on the main form
@app.route('/calculate_fare', methods=['POST'])
def calculate_fare():
    service_type = request.form.get('service_type')
    vehicle_type = request.form.get('vehicle_type')
    
    # Grab the duration they entered earlier on the fleet card
    duration_amount = session.get('duration_amount', 1) # Defaults to 1 if missing
    duration_unit = session.get('duration_unit', 'hours')

    # --- TEMPORARY MOCK DATA (From the 'Be Partner' section) ---
    fares_from_partner_db = {
        'bike': {'hours': 50, 'days': 400},
        'car': {'hours': 150, 'days': 1200},
        '7_seater': {'hours': 250, 'days': 2000}
    }
    
    base_fare = fares_from_partner_db[vehicle_type][duration_unit]
    total_amount = base_fare * duration_amount

    if service_type == 'only_driver':
        driver_hourly_rate = 100
        driver_daily_rate = 800
        if duration_unit == 'hours':
            total_amount = driver_hourly_rate * duration_amount
        else:
            total_amount = driver_daily_rate * duration_amount

    # Save to session and go to confirm page
    session['booking_details'] = {
        'service_type': service_type,
        'vehicle_type': vehicle_type,
        'duration': f"{duration_amount} {duration_unit}",
        'total_amount': total_amount
    }

    return render_template('confirm_booking.html', details=session['booking_details'])
# ==========================================
# DATABASE MODELS
# ==========================================


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(15), unique=True, nullable=False)
    
    # NEW: Admin security flag (defaults to False for normal users)
    is_admin = db.Column(db.Boolean, default=False) 

    password_hash = db.Column(db.String(256), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Partner(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(15), unique=True, nullable=False)
    
    # Partner Type: "driver_only", "vehicle_only", "both"
    partner_type = db.Column(db.String(50), nullable=False)
    
    # Driver Details
    driving_skill = db.Column(db.String(50), nullable=True) # e.g., Beginner, Intermediate, Expert
    experience_years = db.Column(db.Integer, nullable=True)
    transmission_preference = db.Column(db.String(20), nullable=True) # Automatic, Manual, Both
    
    # Pricing setup
    hourly_rate = db.Column(db.Float, nullable=False)
    daily_rate = db.Column(db.Float, nullable=False)
    # NEW: Image storage
    vehicle_image = db.Column(db.String(255), nullable=True)
    is_approved = db.Column(db.Boolean, default=False) # For Admin panel approval
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # 1) self_drive, 2) with_driver, 3) only_driver
    service_type = db.Column(db.String(50), nullable=False) 
    
    # a_to_b OR duration
    journey_type = db.Column(db.String(50), nullable=False) 
    
    # Fields for A to B
    pickup_location = db.Column(db.String(200), nullable=True)
    dropoff_location = db.Column(db.String(200), nullable=True)
    
    # Fields for Duration
    duration_type = db.Column(db.String(20), nullable=True) # hours or days
    duration_value = db.Column(db.Integer, nullable=True)
    
    estimated_fare = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Confirmed, Cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    
# ==========================================
# ROUTES
# ==========================================

# ==========================================
# BOOKING ENGINE ROUTE
# ==========================================
class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default='Pending') # Pending, Reviewed, Resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@app.route('/book', methods=['GET', 'POST'])
def book():
    # Security check: Ensure user is logged in
    if 'user_id' not in session:
        flash('Please login to book a ride.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            new_booking = Booking(
                user_id=session['user_id'],
                service_type=request.form.get('service_type'),
                journey_type=request.form.get('journey_type'),
                pickup_location=request.form.get('pickup_location'),
                dropoff_location=request.form.get('dropoff_location'),
                duration_type=request.form.get('duration_type'),
                duration_value=request.form.get('duration_value') if request.form.get('duration_value') else None,
                estimated_fare=float(request.form.get('calculated_fare', 0))
            )
            db.session.add(new_booking)
            db.session.commit()
            
            flash('Booking requested successfully! It will confirm shortly.', 'success')
            return redirect(url_for('book')) # Later we will redirect to a User Dashboard
            
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {str(e)}', 'error')

    return render_template('book.html')

def send_email_otp(receiver_email, otp):
    sender_email = "manishyadavsci@gmail.com"  # Put your Gmail here
    sender_password = "ehhy jctz orhc aerz" # Put your App Password here

    # Format the email
    msg = MIMEText(f"Welcome to DriveDirect! Your verification OTP is: {otp}")
    msg['Subject'] = 'DriveDirect - Verify Your Account'
    msg['From'] = sender_email
    msg['To'] = receiver_email

    try:
        # Connect to Gmail's server securely
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

# ==========================================
# DASHBOARD & COMPLAINT ROUTES
# ==========================================

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Please login to view your dashboard.', 'error')
        return redirect(url_for('login'))
    
    # Fetch the logged-in user's bookings and complaints, newest first
    user_bookings = Booking.query.filter_by(user_id=session['user_id']).order_by(Booking.created_at.desc()).all()
    user_complaints = Complaint.query.filter_by(user_id=session['user_id']).order_by(Complaint.created_at.desc()).all()
    
    return render_template('dashboard.html', bookings=user_bookings, complaints=user_complaints)

@app.route('/cancel-booking/<int:booking_id>', methods=['POST'])
def cancel_booking(booking_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    booking = Booking.query.get_or_404(booking_id)
    
    # Security check: Ensure the user cancelling owns the booking
    if booking.user_id == session['user_id']:
        booking.status = 'Cancelled'
        db.session.commit()
        flash('Your ride has been successfully cancelled.', 'success')
    else:
        flash('Unauthorized action.', 'error')
        
    return redirect(url_for('dashboard'))

@app.route('/file-complaint', methods=['POST'])
def file_complaint():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    new_complaint = Complaint(
        user_id=session['user_id'],
        subject=request.form.get('subject'),
        message=request.form.get('message')
    )
    
    db.session.add(new_complaint)
    db.session.commit()
    
    flash('Complaint submitted successfully. Our admin team will review it shortly.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/')
def index():
    # Fetch up to 3 approved partners who uploaded a vehicle image to feature on the homepage
    featured_vehicles = Partner.query.filter(Partner.is_approved == True, Partner.vehicle_image != None).limit(3).all()
    return render_template('index.html', vehicles=featured_vehicles)

@app.route('/be-partner', methods=['GET', 'POST'])
def be_partner():
    if request.method == 'POST':
        try:
            # Handle Image Upload
            image_filename = None
            if 'vehicle_image' in request.files:
                file = request.files['vehicle_image']
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    # Add a timestamp to prevent overwriting files with the same name
                    unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                    image_filename = unique_filename

            new_partner = Partner(
                name=request.form.get('name'),
                phone=request.form.get('phone'),
                partner_type=request.form.get('partner_type'),
                driving_skill=request.form.get('driving_skill'),
                experience_years=request.form.get('experience_years') if request.form.get('experience_years') else None,
                transmission_preference=request.form.get('transmission_preference'),
                hourly_rate=float(request.form.get('hourly_rate')),
                daily_rate=float(request.form.get('daily_rate')),
                vehicle_image=image_filename # Save the image name
            )
            
            db.session.add(new_partner)
            db.session.commit()
            
            flash('Registration successful! Waiting for Admin approval.', 'success')
            return redirect(url_for('be_partner'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {str(e)}', 'error')
            
    return render_template('partner_register.html')# ==========================================
# AUTHENTICATION ROUTES (Signup, OTP, Login)
# ==========================================

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')


        # THE FIX: Check if the email or phone already exists before doing anything else
        existing_user = User.query.filter(
            (User.email == email) | (User.phone == phone)
        ).first()

        if existing_user:
            flash('An account with this email or phone number already exists. Please log in.', 'error')
            return redirect(url_for('login'))
        
        session['temp_user'] = {
            'name': name,
            'email': email,
            'phone': phone,
            'password': password # <-- SAVE IT HERE
        }

        # Generate and save OTP
        otp = str(random.randint(1000, 9999))
        session['otp'] = otp
        
        # Send the email
        email_sent = send_email_otp(email, otp)
        
        if email_sent:
            flash('An OTP has been sent to your email address.', 'success')
            return redirect(url_for('verify_otp'))
        else:
            flash('Failed to send OTP email. Please check your console.', 'error')
            return redirect(url_for('signup'))

    return render_template('signup.html')

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    # If they refresh or lose session, send them back to signup
    if 'temp_user' not in session:
        flash('Session expired. Please sign up again.', 'error')
        return redirect(url_for('signup'))

    if request.method == 'POST':
        user_otp = request.form.get('otp')

        if user_otp == session.get('otp'):
            # 1. CREATE the new_user
            user_data = session['temp_user']
            new_user = User(
                name=user_data['name'],
                email=user_data['email'],
                phone=user_data['phone'],
                password_hash=user_data['password'] 
            )
            db.session.add(new_user)
            db.session.commit()

            # 2. USE the new_user (Notice how these are indented directly under the creation step!)
            session.pop('temp_user', None)
            session.pop('otp', None)
            
            session['user_id'] = new_user.id 
            session['user_name'] = new_user.name
            session['is_admin'] = new_user.is_admin

            flash('Account created successfully! Welcome.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid OTP. Please try again.', 'error')

    return render_template('verify_otp.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        # Find the user by email
        user = User.query.filter_by(email=email).first()

        # NEW WAY: Check against the password_hash column!
        if user and user.password_hash == password:
            # Login successful! Save data to session
            session['user_id'] = user.id
            session['user_name'] = user.name
            session['is_admin'] = user.is_admin
            
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')

@app.route('/rescue_my_account')
def rescue_my_account():
    # Find your master account in the live database
    me = User.query.filter_by(email='manishyadavsci@gmail.com').first()
    
    if me:
        # Overwrite whatever the old password was with a new, simple one.
        # (Remember, we are still saving as plain text in the password_hash column for now)
        me.password_hash = "free" 
        db.session.commit()
        
        return "<h1>ACCOUNT RESCUED!</h1><p>Your password is now exactly: <b>admin123</b>. Go log in!</p>"
    else:
        return "<h1>ERROR!</h1><p>I couldn't find your email in the database.</p>"

@app.route('/verify-login-otp', methods=['GET', 'POST'])
def verify_login_otp():
    if 'login_email' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        user_otp = request.form.get('otp')

        if user_otp == session.get('otp'):
            user = User.query.filter_by(email=session['login_email']).first()
            
            session.pop('login_email', None)
            session.pop('otp', None)
            
            # Save user details in session
            session['user_id'] = user.id
            session['user_name'] = user.name
            
            # NEW: Remember if the user is an admin!
            session['is_admin'] = user.is_admin
            
            flash('Logged in successfully!', 'success')
            
            # NEW: Smart Redirect!
            if user.is_admin:
                return redirect(url_for('admin_panel')) # Admins go to the panel
            else:
                return redirect(url_for('index')) # Normal users go to home
                
        else:
            flash('Invalid OTP.', 'error')

    return render_template('verify_otp.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('user_name', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

# ==========================================
# ADMIN PANEL ROUTES
# ==========================================

# ==========================================
# ADMIN PANEL ROUTES (SECURED)
# ==========================================

@app.route('/admin')
def admin_panel():
    # SECURITY CHECK
    if 'user_id' not in session:
        flash('Please log in to access the Admin Panel.', 'error')
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    if not user or not user.is_admin:
        flash('Access Denied: You do not have admin privileges.', 'error')
        return redirect(url_for('index'))
    
    # Fetch Data for Admin
    pending_partners = Partner.query.filter_by(is_approved=False).order_by(Partner.created_at.desc()).all()
    active_complaints = Complaint.query.filter_by(status='Pending').order_by(Complaint.created_at.desc()).all()
    
    # NEW: Fetch pending bookings!
    pending_bookings = Booking.query.filter_by(status='Pending').order_by(Booking.created_at.desc()).all()
    
    return render_template('admin.html', partners=pending_partners, complaints=active_complaints, bookings=pending_bookings)

# NEW ROUTE: Confirm Bookings
@app.route('/admin/confirm-booking/<int:booking_id>', methods=['POST'])
def confirm_booking(booking_id):
    if 'user_id' not in session or not User.query.get(session['user_id']).is_admin:
        return redirect(url_for('index'))
        
    booking = Booking.query.get_or_404(booking_id)
    booking.status = 'Confirmed'
    db.session.commit()
    
    flash(f'Ride for {booking.service_type.replace("_", " ")} has been Confirmed!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/admin/approve-partner/<int:partner_id>', methods=['POST'])
def approve_partner(partner_id):
    # Quick security check to prevent unauthorized form submissions
    if 'user_id' not in session or not User.query.get(session['user_id']).is_admin:
        return redirect(url_for('index'))
        
    partner = Partner.query.get_or_404(partner_id)
    partner.is_approved = True
    db.session.commit()
    
    flash(f'Partner "{partner.name}" has been officially approved!', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/resolve-complaint/<int:complaint_id>', methods=['POST'])
def resolve_complaint(complaint_id):
    # Quick security check
    if 'user_id' not in session or not User.query.get(session['user_id']).is_admin:
        return redirect(url_for('index'))
        
    complaint = Complaint.query.get_or_404(complaint_id)
    complaint.status = 'Resolved'
    db.session.commit()
    
    flash(f'Complaint regarding "{complaint.subject}" has been marked as resolved.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/make_me_admin')
def make_me_admin():
    # 1. Find your exact account in the new live database
    me = User.query.filter_by(email='manishyadavsci@gmail.com').first()
    
    if me:
        # 2. Give yourself the master key
        me.is_admin = True
        db.session.commit()
        
        # 3. Force update your current browser session
        session['is_admin'] = True 
        
        return "<h1>SUCCESS!</h1><p>You are now an Admin. Go back to the homepage!</p>"
    else:
        return "<h1>ERROR!</h1><p>I couldn't find your email. Did you actually click 'Sign Up' on the live website yet?</p>"
# ... all your routes and classes are above this ...

# 1. This goes at the bottom so Python knows about all your tables before building them!
with app.app_context():
    db.create_all()

# 2. This is the normal bottom of a Flask file
if __name__ == '__main__':
    app.run(debug=True)