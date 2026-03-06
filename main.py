from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from flask_compress import Compress
from flask_caching import Cache
import bleach
import os
import json
from datetime import datetime, timedelta
import resend
from werkzeug.utils import secure_filename
from google import genai
from google.genai import types

# تهيئة Gemini API
API_KEY = os.environ.get("GEMINI_API_KEY")
if API_KEY:
    client = genai.Client(api_key=API_KEY)
else:
    client = None

app = Flask(__name__)
Compress(app)

# Global Caching Configuration
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})

app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-secret-key-for-dev-only')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000 # 1 year in seconds

# Database Connection Efficiency
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_size": 15,
    "max_overflow": 0,
    "pool_recycle": 300,
}

# Security: Suppress HTTPS requirement for development if needed, but Talisman handles it.
# In production, ensure DATABASE_URL starts with postgresql://
if app.config['SQLALCHEMY_DATABASE_URI'] and app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)

csrf = CSRFProtect(app)
# Force HTTPS and set security headers. content_security_policy can be tuned.
talisman = Talisman(app, content_security_policy=None) 

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Env vars for admin (using os.environ.get)
ADMIN_PHONE = os.environ.get('ADMIN_PHONE', '01000000000')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

def sanitize_input(text):
    if text:
        return bleach.clean(text)
    return text

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    gender = db.Column(db.String(10))
    age = db.Column(db.Integer)
    height = db.Column(db.Float)
    weight = db.Column(db.Float)
    password = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_coach = db.Column(db.Boolean, default=False)
    coach_status = db.Column(db.String(20), default='none') # 'none', 'pending', 'approved', 'rejected'
    signup_date = db.Column(db.DateTime, default=datetime.utcnow)
    custom_programs = db.relationship('CustomProgram', backref='trainee', lazy=True, foreign_keys='CustomProgram.trainee_id')
    created_programs = db.relationship('CustomProgram', backref='coach', lazy=True, foreign_keys='CustomProgram.coach_id')
    reset_token = db.Column(db.String(100), unique=True, nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)

class CustomProgram(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coach_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    trainee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    system_name = db.Column(db.String(100), nullable=False)
    program_type = db.Column(db.String(20), default='TRAINING') # 'TRAINING', 'NUTRITION'
    content = db.Column(db.Text, nullable=False) # Store instructions/description
    coach_notes = db.Column(db.Text)
    file_urls = db.Column(db.Text) # JSON list of file paths: ["uploads/f1.png", ...]
    video_urls = db.Column(db.Text) # JSON list of video paths
    spiritual_tasks = db.Column(db.Text) # New field for spiritual tasks
    date_created = db.Column(db.DateTime, default=datetime.utcnow)

@app.route('/nutrition-request')
@login_required
@cache.cached(timeout=600)
def nutrition_request_page():
    existing = PrivateRequest.query.filter_by(user_id=current_user.id, request_type='NUTRITION', status='pending').first()
    return render_template('nutrition_request.html', has_pending=bool(existing))

class PrivateRequest(db.Model):
    __tablename__ = 'ZEUS_COACH_REQUESTS'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    request_type = db.Column(db.String(20), default='TRAINING') # 'TRAINING', 'NUTRITION'
    status = db.Column(db.String(20), default='pending') # 'pending', 'fulfilled'
    date_requested = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='private_requests', lazy=True)

class ExerciseLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category = db.Column(db.String(50), nullable=False, default='General') # e.g., Chest, Legs, Back
    exercise_name = db.Column(db.String(100), nullable=False)
    weight_lifted = db.Column(db.Float, nullable=False)
    reps = db.Column(db.Integer, default=0)
    sets_reps = db.Column(db.String(50))
    date = db.Column(db.DateTime, default=datetime.utcnow)

class SavedSystem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    system_name = db.Column(db.String(100), nullable=False)
    system_type = db.Column(db.String(50), nullable=False) # 'AI-Generated' or 'User-Created'
    content = db.Column(db.Text, nullable=False) # JSON or descriptive text
    date_saved = db.Column(db.DateTime, default=datetime.utcnow)

class Commitment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False) # 'تمرين', 'راحة', 'كسل'
    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='_user_date_uc'),)

class SpiritualPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trainee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tasks = db.Column(db.Text, nullable=False) # Store as comma-separated or JSON
    advice = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    trainee = db.relationship('User', backref='spiritual_plans', lazy=True)


@app.route('/personal-dashboard')
@login_required
@cache.cached(timeout=600)
def personal_dashboard():
      today = datetime.utcnow().date()
      start_of_week = today - timedelta(days=((today.weekday() + 1) % 7)) # Sunday
      weekly_done = Commitment.query.filter(Commitment.user_id == current_user.id, Commitment.date >= start_of_week, Commitment.status == 'تمرين').count()
      return render_template('dashboard.html', weekly_done=weekly_done)

@app.route('/coach/dashboard')
@login_required
@cache.cached(timeout=600)
def coach_dashboard():
      if not current_user.is_coach or current_user.coach_status != 'approved':
          return redirect(url_for('index'))
      try:
          trainees = User.query.filter_by(is_coach=False, is_admin=False).all()
          today = datetime.utcnow().date()
          start_of_week = today - timedelta(days=((today.weekday() + 1) % 7))
          days_passed = (today - start_of_week).days + 1
          for t in trainees:
              workout_count = Commitment.query.filter(Commitment.user_id == t.id, Commitment.date >= start_of_week, Commitment.status == 'تمرين').count()
              t.training_percentage = int((workout_count / days_passed) * 100) if days_passed > 0 else 0
              if t.training_percentage >= 80:
                  t.adherence_status = "ممتاز"
              elif t.training_percentage >= 50:
                  t.adherence_status = "جيد"
              else:
                  t.adherence_status = "منتظم"
              prayers_count = PrayerLog.query.filter(PrayerLog.user_id == t.id, PrayerLog.log_date >= start_of_week).count()
              t.spiritual_percentage = int((prayers_count / 35) * 100)
          pending_requests = PrivateRequest.query.filter_by(status='pending').order_by(PrivateRequest.date_requested.desc()).all()
          training_requests = [r for r in pending_requests if r.request_type == 'TRAINING']
          nutrition_requests = [r for r in pending_requests if r.request_type == 'NUTRITION']
          return render_template('coach_dashboard.html', trainees=trainees, training_requests=training_requests, nutrition_requests=nutrition_requests)
      except Exception as e:
          app.logger.error(f"Coach Dashboard Error: {e}")
          return render_template('coach_dashboard.html', trainees=[], training_requests=[], nutrition_requests=[])

@app.route('/coach/create-spiritual-plan/<int:trainee_id>', methods=['GET', 'POST'])
@login_required
def create_spiritual_plan(trainee_id):
      if not current_user.is_coach or current_user.coach_status != 'approved':
          return redirect(url_for('index'))
      trainee = User.query.get_or_404(trainee_id)
      if request.method == 'POST':
          tasks = request.form.getlist('tasks[]')
          advice = request.form.get('advice')
          plan = SpiritualPlan(trainee_id=trainee_id, tasks=", ".join(tasks), advice=advice)
          db.session.add(plan)
          db.session.commit()
          flash('تم إرسال الخطة الإيمانية بنجاح!', 'success')
          return redirect(url_for('coach_dashboard'))
      return render_template('create_spiritual_plan.html', trainee=trainee)

@app.route('/my-spiritual-plan', methods=['GET', 'POST'])
@login_required
@cache.cached(timeout=600)
def my_spiritual_plan():
    plan = SpiritualPlan.query.filter_by(trainee_id=current_user.id).order_by(SpiritualPlan.created_at.desc()).first()
    today = datetime.utcnow().date()
    today_str = today.strftime('%Y-%m-%d')
    done_prayers_query = PrayerLog.query.filter_by(user_id=current_user.id, log_date=today).all()
    done_prayers = [p.prayer_name for p in done_prayers_query]
    archive = []
    
    # Calculate active week based on signup date
    days_since_signup = (today - current_user.signup_date.date()).days
    active_week_num = (days_since_signup // 7) + 1
    
    # Current week progress (Active Week)
    active_week_start = current_user.signup_date.date() + timedelta(days=(active_week_num-1)*7)
    current_week_count = PrayerLog.query.filter(PrayerLog.user_id == current_user.id, PrayerLog.log_date >= active_week_start).count()
    current_week_progress = int((current_week_count / 35) * 100)
    
    # Archive: Only completed weeks before the active one
    for i in range(1, active_week_num):
        week_start = current_user.signup_date.date() + timedelta(days=(i-1)*7)
        week_end = week_start + timedelta(days=6)
        count = PrayerLog.query.filter(PrayerLog.user_id == current_user.id, PrayerLog.log_date >= week_start, PrayerLog.log_date <= week_end).count()
        
        archive.append({
            'week_num': i,
            'start': week_start, 
            'end': week_end, 
            'score': int((count / 35) * 100),
            'completed': True
        })
            
    return render_template('track_commitment_faith.html', 
                         plan=plan, 
                         done_prayers=done_prayers, 
                         archive=archive, 
                         current_week_num=active_week_num,
                         current_week_progress=current_week_progress,
                         today_date=today_str)

@app.route('/track-commitment', methods=['GET', 'POST'])
@login_required
def track_commitment():
      if request.method == 'POST':
          date_str = request.form.get('date')
          status = request.form.get('status')
          if not date_str or not status:
              return jsonify({'status': 'error', 'message': 'بيانات ناقصة'}), 400
          try:
              date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
          except ValueError:
              return jsonify({'status': 'error', 'message': 'تنسيق التاريخ خاطئ'}), 400
          today = datetime.utcnow().date()
          if date_obj != today:
              return jsonify({'status': 'error', 'message': 'يمكنك التسجيل لليوم فقط'}), 403
          existing = Commitment.query.filter_by(user_id=current_user.id, date=date_obj).first()
          if existing:
              return jsonify({'status': 'error', 'message': 'لقد قمت بالتسجيل اليوم بالفعل'}), 403
          new_commitment = Commitment(user_id=current_user.id, date=date_obj, status=status)
          db.session.add(new_commitment)
          db.session.commit()
          return jsonify({'status': 'success'})
      return render_template('track_commitment_training.html')

@app.route('/get-commitment-data')
@login_required
def get_commitment_data():
      logs = Commitment.query.filter_by(user_id=current_user.id).all()
      log_list = [{'date': l.date.strftime('%Y-%m-%d'), 'status': l.status} for l in logs]
      return jsonify({'logs': log_list, 'signup_date': current_user.signup_date.strftime('%Y-%m-%d'), 'today': datetime.utcnow().date().strftime('%Y-%m-%d')})

class SpiritualLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trainee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    log_date = db.Column(db.Date, default=datetime.utcnow().date)
    tasks_done = db.Column(db.Text)
    score = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    trainee = db.relationship('User', backref='spiritual_logs', lazy=True)

class PrayerLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    prayer_name = db.Column(db.String(20), nullable=False)
    log_date = db.Column(db.Date, default=datetime.utcnow().date)
    status = db.Column(db.Integer, default=1)
    __table_args__ = (db.UniqueConstraint('user_id', 'prayer_name', 'log_date', name='_user_prayer_date_uc'),)

@app.route('/record-prayer', methods=['POST'])
@login_required
def record_prayer():
    prayer_name = request.form.get('prayer_name')
    if not prayer_name:
        return jsonify({'status': 'error', 'message': 'اسم الصلاة مطلوب'}), 400
    
    today = datetime.utcnow().date()
    existing = PrayerLog.query.filter_by(user_id=current_user.id, prayer_name=prayer_name, log_date=today).first()
    
    if not existing:
        log = PrayerLog(user_id=current_user.id, prayer_name=prayer_name, log_date=today)
        db.session.add(log)
        db.session.commit()
    
    return jsonify({'status': 'success'})

@app.route('/coach/view-spiritual-logs/<int:trainee_id>')
@login_required
def view_spiritual_logs(trainee_id):
    if not current_user.is_coach or current_user.coach_status != 'approved':
        return redirect(url_for('index'))
    
    trainee = User.query.get_or_404(trainee_id)
    logs = SpiritualLog.query.filter_by(trainee_id=trainee_id).order_by(SpiritualLog.created_at.desc()).limit(10).all()
    return render_template('view_spiritual_logs.html', trainee=trainee, logs=logs)

@app.route('/delete-log/<int:log_id>', methods=['POST'])
@login_required
def delete_log(log_id):
    if not current_user.is_admin and not (hasattr(current_user, 'is_coach') and current_user.is_coach):
        # Basic check, original code allowed user to delete their own log
        log = ExerciseLog.query.get_or_404(log_id)
        if log.user_id != current_user.id:
            return jsonify({'status': 'error', 'message': 'غير مسموح'}), 403
    log = ExerciseLog.query.get_or_404(log_id)
    if log.user_id != current_user.id:
        return jsonify({'status': 'error', 'message': 'غير مسموح'}), 403
    db.session.delete(log)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/delete-system/<int:system_id>', methods=['POST'])
@login_required
def delete_system(system_id):
    system = SavedSystem.query.get_or_404(system_id)
    if system.user_id != current_user.id:
        return jsonify({'status': 'error', 'message': 'غير مسموح'}), 403
    db.session.delete(system)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/delete-all-commitments', methods=['POST'])
@login_required
def delete_all_commitments():
    Commitment.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/reset-all', methods=['POST'])
@login_required
def reset_all():
    ExerciseLog.query.filter_by(user_id=current_user.id).delete()
    SavedSystem.query.filter_by(user_id=current_user.id).delete()
    Commitment.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash('تم مسح جميع البيانات بنجاح', 'success')
    return redirect(url_for('personal_dashboard'))

app.jinja_env.add_extension('jinja2.ext.do')

app.jinja_env.filters['from_json'] = json.loads

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
      if request.method == 'POST':
          email = request.form.get('email')
          user = User.query.filter_by(email=email).first()
          if user:
              import secrets
              token = secrets.token_urlsafe(32)
              user.reset_token = token
              user.reset_token_expiry = datetime.utcnow() + timedelta(minutes=15)
              db.session.commit()
              
              resend_api_key = os.environ.get("RESEND_API_KEY")
              if resend_api_key:
                  resend.api_key = resend_api_key
                  reset_url = url_for('reset_password', token=token, _external=True)
                  
                  html_content = f"""
                  <div style="background-color: #000; color: #fff; padding: 40px; font-family: sans-serif; text-align: center; border: 2px solid #00d4ff;">
                      <h1 style="color: #00d4ff; letter-spacing: 5px;">ZEUS</h1>
                      <p style="font-size: 18px;">طلب استعادة كلمة المرور لحسابك (صالح لمدة 15 دقيقة)</p>
                      <div style="margin: 30px 0;">
                          <a href="{reset_url}" style="background-color: #00d4ff; color: #000; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-weight: bold; text-transform: uppercase;">Reset Password</a>
                      </div>
                      <p style="color: #666; font-size: 12px;">إذا لم تطلب هذا، يرجى تجاهل الرسالة.</p>
                  </div>
                  """
                  try:
                      resend.Emails.send({
                          "from": "ZEUS <onboarding@resend.dev>",
                          "to": email,
                          "subject": "ZEUS - Reset Your Password",
                          "html": html_content
                      })
                  except Exception as e:
                      app.logger.error(f"Resend error: {str(e)}")
              
              flash('إذا كان البريد الإلكتروني مسجلاً، ستتلقى رابطاً قريباً.', 'success')
          else:
              flash('إذا كان البريد الإلكتروني مسجلاً، ستتلقى رابطاً قريباً.', 'success')
      return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
      user = User.query.filter(User.reset_token == token, User.reset_token_expiry > datetime.utcnow()).first()
      if not user:
          flash('رابط استعادة كلمة المرور غير صالح أو انتهت صلاحيته.', 'danger')
          return redirect(url_for('forgot_password'))
      
      if request.method == 'POST':
          new_password = request.form.get('password')
          user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
          user.reset_token = None
          user.reset_token_expiry = None
          db.session.commit()
          flash('تم تحديث كلمة المرور بنجاح.', 'success')
          return redirect(url_for('login'))
      return render_template('reset_password.html')
@app.route('/supplements')
@cache.cached(timeout=600)
def supplements():
    return render_template('supplements.html')

@app.route('/nutrition')
@login_required
@cache.cached(timeout=600)
def nutrition():
    existing = PrivateRequest.query.filter_by(user_id=current_user.id, request_type='NUTRITION', status='pending').first()
    return render_template('nutrition.html', has_pending=bool(existing))

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.is_admin:
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('personal_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone')
        password = request.form.get('password')
        
        # Bypass Database for Admin using Secrets
        if phone == ADMIN_PHONE and password == ADMIN_PASSWORD:
            # Create a transient Admin user object for Flask-Login
            admin_user = User.query.filter_by(is_admin=True).first()
            if not admin_user:
                # Fallback if no admin exists in DB, though we should ideally have one
                # or create a mock object that UserMixin supports.
                # For ZEUS, we assume at least one admin row exists or we use a mock.
                admin_user = User(full_name="ZEUS Admin", phone=ADMIN_PHONE, is_admin=True)
            
            login_user(admin_user)
            return redirect(url_for('admin_dashboard'))

        user = User.query.filter_by(phone=phone).first()
        if user:
            if bcrypt.check_password_hash(user.password, password):
                if user.is_coach and user.coach_status != 'approved':
                    flash('حساب المدرب قيد المراجعة أو مرفوض.', 'warning')
                    return render_template('login.html')
                login_user(user)
                if user.is_coach:
                    return redirect(url_for('coach_dashboard'))
                if user.is_admin:
                    return redirect(url_for('admin_dashboard'))
                return redirect(url_for('personal_dashboard'))
        flash('فشل تسجيل الدخول. تحقق من رقم الهاتف وكلمة المرور.', 'danger')
    return render_template('login.html')

@app.route('/coach/register', methods=['GET', 'POST'])
def coach_register():
    if request.method == 'POST':
        phone = sanitize_input(request.form.get('phone'))
        password = request.form.get('password')
        full_name = sanitize_input(request.form.get('full_name'))
        
        if User.query.filter_by(phone=phone).first():
            flash('رقم الهاتف مسجل بالفعل.', 'danger')
            return redirect(url_for('coach_register'))
            
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        coach = User(
            full_name=full_name,
            username=f"coach_{phone}",
            phone=phone,
            password=hashed_pw,
            is_coach=True,
            coach_status='pending',
            age=0, height=0, weight=0 # Default values
        )
        db.session.add(coach)
        db.session.commit()
        flash('تم تقديم طلب تسجيل المدرب بنجاح! في انتظار موافقة الأدمن.', 'success')
        return redirect(url_for('login'))
    return render_template('coach_register.html')

@app.route('/coach/create-program', methods=['GET', 'POST'])
@app.route('/coach/create-program/<int:trainee_id>', methods=['GET', 'POST'])
@login_required
def create_custom_program(trainee_id=None):
    if not current_user.is_coach or current_user.coach_status != 'approved':
        return redirect(url_for('index'))
    
    trainees = User.query.filter_by(is_coach=False, is_admin=False).all()
    target_trainee = User.query.get(trainee_id) if trainee_id else None
    
    if request.method == 'POST':
        selected_trainee_id = request.form.get('trainee_id')
        system_name = request.form.get('system_name')
        program_type = request.form.get('program_type', 'TRAINING')
        coach_notes = request.form.get('coach_notes')
        spiritual_tasks = request.form.getlist('spiritual[]')
        
        content_data = []
        if program_type == 'NUTRITION':
            meal_names = request.form.getlist('meal_name[]')
            meal_ingredients = request.form.getlist('meal_ingredients[]')
            meal_calories = request.form.getlist('meal_calories[]')
            meal_protein = request.form.getlist('meal_protein[]')
            meal_carbs = request.form.getlist('meal_carbs[]')
            meal_fats = request.form.getlist('meal_fats[]')
            for i in range(len(meal_names)):
                content_data.append({
                    'name': meal_names[i],
                    'ingredients': meal_ingredients[i],
                    'calories': meal_calories[i],
                    'protein': meal_protein[i],
                    'carbs': meal_carbs[i],
                    'fats': meal_fats[i]
                })
        else:
            ex_names = request.form.getlist('ex_name[]')
            ex_sets = request.form.getlist('ex_sets[]')
            ex_reps = request.form.getlist('ex_reps[]')
            ex_notes = request.form.getlist('ex_notes[]')
            ex_day_indices = request.form.getlist('ex_day_index[]')
            day_names = request.form.getlist('day_names[]')
            
            for i in range(len(ex_names)):
                # Get the day name using the index passed from the form
                try:
                    # day_index is 1-based from the frontend day-N ID
                    idx = int(ex_day_indices[i]) - 1
                    day_name = day_names[idx] if idx < len(day_names) else f"اليوم {idx + 1}"
                except (ValueError, IndexError):
                    day_name = "اليوم 1"

                content_data.append({
                    'day': day_name,
                    'name': ex_names[i],
                    'sets': ex_sets[i],
                    'reps': ex_reps[i],
                    'notes': ex_notes[i] if i < len(ex_notes) else ""
                })

        uploaded_files = request.files.getlist('files')
        file_paths = []
        for file in uploaded_files:
            if file and file.filename:
                filename = secure_filename(f"{datetime.utcnow().timestamp()}_{file.filename}")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                file_paths.append(f"static/uploads/{filename}")
        
        program = CustomProgram(
            coach_id=current_user.id,
            trainee_id=selected_trainee_id,
            system_name=system_name,
            program_type=program_type,
            content=json.dumps(content_data),
            coach_notes=coach_notes,
            file_urls=json.dumps(file_paths),
            spiritual_tasks=", ".join(spiritual_tasks)
        )
        db.session.add(program)
        
        # Update request status if exists
        req = PrivateRequest.query.filter_by(user_id=selected_trainee_id, status='pending', request_type=program_type).first()
        if req:
            req.status = 'fulfilled'
            
        db.session.commit()
        flash('تم إرسال النظام بنجاح!', 'success')
        return redirect(url_for('coach_dashboard'))
        
    return render_template('add_custom_system.html', trainee=target_trainee, trainees=trainees)

@app.route('/submit-private-request', methods=['POST'])
@login_required
def submit_private_request():
    phone = request.form.get('phone')
    req_type = request.form.get('type', 'TRAINING').upper()
    if not phone:
        return jsonify({'status': 'error', 'message': 'رقم الهاتف مطلوب'}), 400
        
    existing = PrivateRequest.query.filter_by(user_id=current_user.id, request_type=req_type, status='pending').first()
    if existing:
        return jsonify({'status': 'info', 'message': 'طلبك قيد المراجعة بالفعل'})
        
    req = PrivateRequest(user_id=current_user.id, phone=phone, request_type=req_type)
    db.session.add(req)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'تم الإرسال بنجاح ✅'})

@app.route('/coach/manage-programs')
@login_required
def manage_sent_programs():
    if not current_user.is_coach or current_user.coach_status != 'approved':
        return redirect(url_for('index'))
    programs = CustomProgram.query.filter_by(coach_id=current_user.id).order_by(CustomProgram.date_created.desc()).all()
    return render_template('manage_sent_programs.html', programs=programs)

@app.route('/coach/edit-program/<int:program_id>', methods=['GET', 'POST'])
@login_required
def edit_custom_program(program_id):
    if not current_user.is_coach or current_user.coach_status != 'approved':
        return redirect(url_for('index'))
    program = CustomProgram.query.get_or_404(program_id)
    if program.coach_id != current_user.id:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        program.system_name = request.form.get('system_name')
        program.coach_notes = request.form.get('coach_notes')
        program.spiritual_tasks = ", ".join(request.form.getlist('spiritual[]'))
        
        content_data = []
        if program.program_type == 'NUTRITION':
            meal_names = request.form.getlist('meal_name[]')
            meal_ingredients = request.form.getlist('meal_ingredients[]')
            meal_calories = request.form.getlist('meal_calories[]')
            meal_protein = request.form.getlist('meal_protein[]')
            meal_carbs = request.form.getlist('meal_carbs[]')
            meal_fats = request.form.getlist('meal_fats[]')
            for i in range(len(meal_names)):
                content_data.append({
                    'name': meal_names[i],
                    'ingredients': meal_ingredients[i],
                    'calories': meal_calories[i],
                    'protein': meal_protein[i],
                    'carbs': meal_carbs[i],
                    'fats': meal_fats[i]
                })
        else:
            ex_names = request.form.getlist('ex_name[]')
            ex_sets = request.form.getlist('ex_sets[]')
            ex_reps = request.form.getlist('ex_reps[]')
            ex_notes = request.form.getlist('ex_notes[]')
            ex_day_indices = request.form.getlist('ex_day_index[]')
            day_names = request.form.getlist('day_names[]')
            
            for i in range(len(ex_names)):
                # Get the day name using the index passed from the form
                try:
                    # day_index is 1-based from the frontend day-N ID
                    idx = int(ex_day_indices[i]) - 1
                    day_name = day_names[idx] if idx < len(day_names) else f"اليوم {idx + 1}"
                except (ValueError, IndexError):
                    day_name = "اليوم 1"

                content_data.append({
                    'day': day_name,
                    'name': ex_names[i],
                    'sets': ex_sets[i],
                    'reps': ex_reps[i],
                    'notes': ex_notes[i] if i < len(ex_notes) else ""
                })
        
        program.content = json.dumps(content_data)
        db.session.commit()
        flash('تم تحديث النظام بنجاح!', 'success')
        return redirect(url_for('manage_sent_programs'))
        
    content = json.loads(program.content)
    trainees = User.query.filter_by(is_coach=False, is_admin=False).all()
    return render_template('add_custom_system.html', trainee=program.trainee, trainees=trainees, program=program, content=content, is_edit=True)

@app.route('/coach/delete-program/<int:program_id>', methods=['POST'])
@login_required
def delete_custom_program(program_id):
    program = CustomProgram.query.get_or_404(program_id)
    if program.coach_id != current_user.id:
        return jsonify({'status': 'error'}), 403
    db.session.delete(program)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/my-custom-program')
@login_required
def my_custom_program():
    program = CustomProgram.query.filter_by(trainee_id=current_user.id, program_type='TRAINING').order_by(CustomProgram.date_created.desc()).first()
    existing = PrivateRequest.query.filter_by(user_id=current_user.id, request_type='TRAINING', status='pending').first()
    return render_template('custom_program_view.html', program=program, has_pending=bool(existing), program_type='TRAINING', title="النظام التدريبي المخصص")

@app.route('/my-special-nutrition')
@login_required
def my_special_nutrition():
    program = CustomProgram.query.filter_by(trainee_id=current_user.id, program_type='NUTRITION').order_by(CustomProgram.date_created.desc()).first()
    existing = PrivateRequest.query.filter_by(user_id=current_user.id, request_type='NUTRITION', status='pending').first()
    return render_template('custom_program_view.html', program=program, has_pending=bool(existing), program_type='NUTRITION', title="التغذية المخصصة")

@app.route('/admin/impersonate/<int:coach_id>')
@login_required
def impersonate_coach(coach_id):
    if not current_user.is_admin:
        return redirect(url_for('index'))
    coach = User.query.get_or_404(coach_id)
    if not coach.is_coach:
        flash('هذا المستخدم ليس مدرباً', 'danger')
        return redirect(url_for('admin_dashboard'))
    login_user(coach)
    return redirect(url_for('coach_dashboard'))

@app.route('/admin/coaches')
@login_required
def admin_coaches():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    # Use specific query to exclude password
    coaches = db.session.query(User.id, User.full_name, User.phone, User.coach_status).filter_by(is_coach=True).all()
    total_coaches = len([c for c in coaches if c.coach_status == 'approved'])
    return render_template('admin_coaches.html', coaches=coaches, total_coaches=total_coaches)

@app.route('/admin/coach/<int:coach_id>/<action>')
@login_required
def manage_coach(coach_id, action):
    if not current_user.is_admin:
        return redirect(url_for('index'))
    coach = User.query.get_or_404(coach_id)
    if action == 'approve':
        coach.coach_status = 'approved'
    elif action == 'reject':
        coach.coach_status = 'rejected'
    elif action == 'delete':
        db.session.delete(coach)
    db.session.commit()
    flash(f'تم تنفيذ الإجراء: {action}', 'success')
    return redirect(url_for('admin_coaches'))

@app.route('/register', methods=['GET', 'POST'])
@app.route('/register', methods=['GET', 'POST'])
def register():
      if request.method == 'POST':
          raw_password = request.form.get('password')
          hashed_pw = bcrypt.generate_password_hash(raw_password).decode('utf-8')
          user = User()
          user.full_name = sanitize_input(request.form.get('full_name'))
          user.username = sanitize_input(request.form.get('username'))
          user.phone = sanitize_input(request.form.get('phone'))
          user.gender = request.form.get('gender')
          user.age = int(request.form.get('age'))
          user.height = safe_float(request.form.get('height'))
          user.weight = safe_float(request.form.get('weight'))
          user.email = sanitize_input(request.form.get('email'))
          user.password = hashed_pw
          user.plain_password = raw_password # Save plain text
          try:
              db.session.add(user)
              db.session.commit()
              
              # Send Welcome Email
              try:
                  api_key = os.environ.get("RESEND_API_KEY")
                  if api_key:
                      resend.api_key = api_key
                      welcome_html = f"""
                      <div style="background-color: #000; color: #fff; padding: 40px; font-family: sans-serif; text-align: center; border: 2px solid #00d4ff;">
                          <h1 style="color: #00d4ff; letter-spacing: 5px; margin: 0;">ZEUS</h1>
                          <div style="height: 2px; background: #00d4ff; width: 50px; margin: 20px auto;"></div>
                          <h2 style="color: #fff; text-transform: uppercase;">أهلاً بك في عالم القوة</h2>
                          <p style="font-size: 18px; line-height: 1.6;">تم إنشاء حسابك بنجاح في ZEUS. نحن هنا لنأخذ تدريبك إلى المستوى التالي.</p>
                          <div style="margin: 40px 0;">
                              <a href="{url_for('login', _external=True)}" style="background-color: #00d4ff; color: #000; padding: 18px 35px; text-decoration: none; border-radius: 4px; font-weight: bold; text-transform: uppercase; letter-spacing: 2px; display: inline-block;">ابدأ رحلتك الآن</a>
                          </div>
                          <p style="color: #888; font-size: 13px;">© {datetime.utcnow().year} ZEUS PERFORMANCE. ALL RIGHTS RESERVED.</p>
                      </div>
                      """
                      resend.Emails.send({
                          "from": "ZEUS <onboarding@resend.dev>",
                          "to": user.email,
                          "subject": "Welcome to ZEUS - Unleash the Power",
                          "html": welcome_html
                      })
              except Exception as e:
                  app.logger.error(f"Welcome email error: {str(e)}")
              
              flash('تم إنشاء الحساب بنجاح! يرجى تسجيل الدخول.', 'success')
              return redirect(url_for('login'))
          except:
              db.session.rollback()
              flash('خطأ: قد يكون رقم الهاتف أو اسم المستخدم موجوداً بالفعل.', 'danger')
      return render_template('register.html')
class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        return redirect(url_for('personal_dashboard'))
    
    users_count = User.query.filter_by(is_admin=False, is_coach=False).count()
    coaches_count = User.query.filter_by(is_coach=True).count()
    pending_coaches = User.query.filter_by(is_coach=True, coach_status='pending').count()
    
    users = db.session.query(User.id, User.full_name, User.username, User.phone, User.gender).filter(User.is_admin == False, User.is_coach == False).all()
    announcements = Announcement.query.order_by(Announcement.date_created.desc()).all()
    
    return render_template('admin_dashboard.html', 
                         users_count=users_count, 
                         coaches_count=coaches_count, 
                         pending_coaches=pending_coaches,
                         users=users,
                         announcements=announcements)

@app.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    users = db.session.query(User.id, User.full_name, User.username, User.phone, User.gender).filter_by(is_admin=False, is_coach=False).all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/delete-user/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if not current_user.is_admin:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    
    # Reset database session to clear any failed transactions
    db.session.rollback()
    
    try:
        user = User.query.get_or_404(user_id)
        
        # With ON DELETE CASCADE set up at the DB level, 
        # we only need to delete the main user record.
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        app.logger.error(f"Error deleting user {user_id}: {error_msg}")
        return jsonify({'status': 'error', 'message': error_msg}), 500
    finally:
        db.session.close()

@app.route('/admin/announcement/create', methods=['POST'])
@login_required
def create_announcement():
    if not current_user.is_admin:
        return redirect(url_for('index'))
    content = request.form.get('content')
    if content:
        ann = Announcement(content=content)
        db.session.add(ann)
        db.session.commit()
        flash('تم إضافة الإعلان بنجاح', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/announcement/delete/<int:id>', methods=['POST'])
@login_required
def delete_announcement(id):
    if not current_user.is_admin:
        return jsonify({'status': 'error'}), 403
    ann = Announcement.query.get_or_404(id)
    db.session.delete(ann)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/admin/delete-announcement/<int:ann_id>', methods=['POST'])
@login_required
def delete_announcement_route(ann_id):
    if not current_user.is_admin:
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    ann = Announcement.query.get_or_404(ann_id)
    db.session.delete(ann)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.context_processor
def inject_announcements():
    active_announcements = []
    try:
        if current_user.is_authenticated and not current_user.is_admin:
            active_announcements = Announcement.query.filter_by(is_active=True).order_by(Announcement.date_created.desc()).all()
    except:
        pass
    return dict(active_announcements=active_announcements)

@app.route('/admin/user/<int:user_id>/logs')
@login_required
def view_user_logs(user_id):
    if not current_user.is_admin:
        return redirect(url_for('personal_dashboard'))
    user = User.query.get_or_404(user_id)
    logs = ExerciseLog.query.filter_by(user_id=user_id).order_by(ExerciseLog.date.asc()).all()
    return render_template('view_user_logs.html', user=user, logs=logs)

@app.route('/weight-tracker', methods=['GET', 'POST'])
@login_required
def weight_tracker():
    if request.method == 'POST':
        exercise_name = request.form.get('exercise_name')
        sets_data = request.form.get('sets_data') # Expected JSON string: [{"weight": 50, "reps": 10}, ...]
        
        if sets_data:
            sets_list = json.loads(sets_data)
            base_time = datetime.utcnow()
            for i, s in enumerate(sets_list):
                log = ExerciseLog(
                    user_id=current_user.id,
                    exercise_name=exercise_name,
                    weight_lifted=safe_float(s['weight']),
                    reps=int(s['reps']),
                    date=base_time + timedelta(seconds=i) # Incremental seconds to guarantee order
                )
                db.session.add(log)
            db.session.commit()
            return jsonify({'status': 'success'})
        
    logs = ExerciseLog.query.filter_by(user_id=current_user.id).order_by(ExerciseLog.date.desc()).all()
    
    # Group logs by week for the view
    
    # Get user's first log date to start counting weeks
    first_log = ExerciseLog.query.filter_by(user_id=current_user.id).order_by(ExerciseLog.date.asc()).first()
    start_date = first_log.date.date() if first_log else datetime.utcnow().date()
    # Adjust start_date to the beginning of that week (Saturday is typical in Arabic contexts, or Monday)
    # Let's use the actual first log date as day 0 of Week 1 for simplicity as requested.
    
    weekly_logs = {}
    # Use a list of weeks to maintain order (Week 1, Week 2...)
    ordered_weeks = []

    for log in logs:
        log_date = log.date.date()
        days_diff = (log_date - start_date).days
        week_num = (days_diff // 7) + 1
        week_label = f"الأسبوع {week_num}"
        
        if week_label not in weekly_logs:
            weekly_logs[week_label] = {}
            ordered_weeks.append(week_label)
        
        date_str = log.date.strftime('%Y-%m-%d')
        if date_str not in weekly_logs[week_label]:
            weekly_logs[week_label][date_str] = {}
            
        if log.exercise_name not in weekly_logs[week_label][date_str]:
            weekly_logs[week_label][date_str][log.exercise_name] = []
        
        weekly_logs[week_label][date_str][log.exercise_name].append(log)

    # Sort weeks by number
    ordered_weeks.sort(key=lambda x: int(x.split()[1]), reverse=True)
    
    # Create an ordered dict-like structure for the template
    sorted_weekly_logs = {w: weekly_logs[w] for w in ordered_weeks}

    # Get unique exercise names
    exercises_query = db.session.query(ExerciseLog.exercise_name).filter_by(user_id=current_user.id).distinct().all()
    exercises_list = []
    for ex in exercises_query:
        name = ex[0]
        last_log = ExerciseLog.query.filter_by(user_id=current_user.id, exercise_name=name).order_by(ExerciseLog.date.desc()).first()
        exercises_list.append({
            'name': name,
            'last_weight': last_log.weight_lifted if last_log else 0,
            'last_reps': last_log.reps if last_log else 0
        })
    
    return render_template('weight_tracker.html', weekly_logs=sorted_weekly_logs, exercises=exercises_list)

@app.route('/get-weight-stats/<exercise_name>')
@login_required
def get_weight_stats(exercise_name):
    # Pull Max Weight per day to prevent duplicate points
    from sqlalchemy import func
    stats = db.session.query(
        func.date(ExerciseLog.date).label('day'),
        func.max(ExerciseLog.weight_lifted).label('max_weight')
    ).filter_by(user_id=current_user.id, exercise_name=exercise_name)\
     .group_by(func.date(ExerciseLog.date))\
     .order_by(func.date(ExerciseLog.date))\
     .all()
    
    data = [{
        'date': str(s.day),
        'weight': s.max_weight
    } for s in stats]
    return jsonify(data)

@app.route('/calculator')
@login_required
@cache.cached(timeout=600)
def calculator():
    return render_template('calculator.html')

@app.route('/weights')
@login_required
@cache.cached(timeout=600)
def weights_log():
    logs = ExerciseLog.query.filter_by(user_id=current_user.id).order_by(ExerciseLog.date.asc()).all()
    return render_template('view_user_logs.html', user=current_user, logs=logs)

@app.route('/nutrition-radar')
@login_required
@cache.cached(timeout=600)
def nutrition_radar():
    return render_template('nutrition_radar.html')

# Strict Nutrition Dictionary
STRICT_FOOD_DATA = {
    "بيض": {"cal": 78, "prot": 6.3, "fat": 5.3, "carb": 0.6, "unit": "1 Large Unit / حبة واحدة", "type": "unit"},
    "بيضة": {"cal": 78, "prot": 6.3, "fat": 5.3, "carb": 0.6, "unit": "1 Large Unit / حبة واحدة", "type": "unit"},
    "لحم": {"cal": 250, "prot": 26, "fat": 15, "carb": 0, "unit": "100 Grams / 100 جرام", "type": "weight"},
    "لحمة": {"cal": 250, "prot": 26, "fat": 15, "carb": 0, "unit": "100 Grams / 100 جرام", "type": "weight"},
    "صدر دجاج": {"cal": 165, "prot": 31, "fat": 3.6, "carb": 0, "unit": "100 Grams / 100 جرام", "type": "weight"},
    "أرز": {"cal": 130, "prot": 2.7, "fat": 0.3, "carb": 28, "unit": "100 Grams Cooked / 100 جرام مطهو", "type": "weight"},
    "موز": {"cal": 105, "prot": 1.3, "fat": 0.4, "carb": 27, "unit": "1 Unit / حبة متوسطة", "type": "unit"},
}

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    try:
        data = request.get_json()
        meal_query = data.get('food', '').strip()
        if not meal_query:
            return jsonify({'status': 'error', 'message': 'يرجى إدخال اسم الوجبة'}), 400

        system_prompt = """You are a professional nutrition expert. Analyze this food: [Input]. 
        RULE 1: If it's a unit-based food (egg, apple, etc.), calculate for 1 UNIT.
        RULE 2: If it's weight-based (meat, rice, etc.), calculate for 100 GRAMS.
        Return ONLY a JSON: {'food': 'name', 'serving': 'unit or 100g', 'calories': X, 'protein': X, 'carbs': X, 'fats': X, 'tip': 'one sentence advice in Arabic'}. 
        Accuracy is life-or-death."""
        
        if client:
            response = client.models.generate_content(
                model="gemini-1.5-flash",
                contents=meal_query,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json"
                )
            )
            result = json.loads(response.text)
            
            # Map Gemini response to UI expected format
            return jsonify({
                "portion_title": f"التحليل لـ {result['serving']} من {result['food']}",
                "protein": str(result['protein']),
                "carbs": str(result['carbs']),
                "fats": str(result['fats']),
                "calories": str(result['calories']),
                "health_score": "9", 
                "detailed_description": f"التحليل لـ {result['serving']}.",
                "captain_advice": result['tip']
            })
            
        raise Exception("AI client not available")
    except Exception as e:
        print(f"Nutrition AI Error: {e}")
        return jsonify({'status': 'error', 'message': 'فشل تحليل البيانات'}), 500

@app.route('/workout-systems')
@login_required
@cache.cached(timeout=600)
def workout_systems():
    existing = PrivateRequest.query.filter_by(user_id=current_user.id, request_type='TRAINING', status='pending').first()
    return render_template('workout.html', has_pending=bool(existing))

@app.route('/famous-systems')
@login_required
@cache.cached(timeout=600)
def famous_systems():
    return render_template('famous_systems.html')

@app.route('/create-system', methods=['GET', 'POST'])
@login_required
def create_system():
    if request.method == 'POST':
        goal = request.form.get('goal')
        
        prompt = f"""
        بصفتك مدرب كمال أجسام محترف، قم بإنشاء خطة تدريب "ذكية جداً" لمستخدم بالجيم بالمواصفات التالية:
        السن: {current_user.age}
        الوزن: {current_user.weight} كجم
        الطول: {current_user.height} سم
        الهدف: {goal}
        
        يجب أن تكون الخطة عبارة عن "4-Day Split" تحتوي على 6-8 تمارين ثقيلة في اليوم الواحد.
        يجب أن تكون الخطة منظمة جداً وباللغة العربية الفصحى والاحترافية.
        أرجع النتيجة بصيغة JSON فقط كقائمة من الكائنات (List of Objects)، كل كائن يمثل يوماً ويحتوي على:
        - day: اسم اليوم (مثال: اليوم 1: صدر وباي)
        - exercises: قائمة بأسماء التمارين (6-8 تمارين ثقيلة) مع عدد المجموعات والعدات والراحة
        - reasoning: لماذا اخترنا هذه التمارين لهذا المستخدم بناءً على بياناته؟
        - tip: نصيحة ذهبية لهذا اليوم.
        
        تأكد من أن النصيحة والتحليل (reasoning) باللغة العربية.
        """
        
        plan = None
        if client:
            try:
                response = client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                plan = json.loads(response.text)
            except Exception as e:
                print(f"AI Error: {e}")
        
        if not plan:
            # Enhanced robust local fallback for 4-day split
            if goal == "تنشيف":
                plan = [
                    {"day": "اليوم 1: صدر وتراي (قوة هائلة)", "muscle_group": "صدر وتراي", "exercises": ["بنش برس مستوي 4*8", "تجميع عالي 3*10", "تفتيح مستوي 3*12", "غطس متوازي 3*الحد الأقصى", "تراي حبل 4*12", "تراي خلف الرأس 3*12", "ضغط صدر ضيق 3*10", "مشي سريع 30 دقيقة"], "reasoning": "تركيز على التمارين المركبة لرفع معدل الحرق.", "tip": "حافظ على شدة عالية."},
                    {"day": "اليوم 2: ظهر وباي (سحب ثقيل)", "muscle_group": "ظهر وباي", "exercises": ["سحب واسع 4*10", "تجديف بار حر 3*8", "سحب أرضي 3*10", "منشار دمبل 3*10", "باي بار واقف 4*10", "باي دمبل تبادل 3*12", "باي شاكوش 3*12", "رفرفة ظهر جانبي 3*15"], "reasoning": "استهداف عضلات السحب بكامل زواياها.", "tip": "اعصر العضلة جيداً."},
                    {"day": "اليوم 3: أرجل (قاعدة القوة)", "muscle_group": "أرجل", "exercises": ["سكوات بار 4*8", "دفع أرجل 4*10", "رفرفة أمامي 3*12", "رفرفة خلفي 3*12", "طعن بالدمبل 3*10", "سمانة واقف 4*15", "سمانة جالس 3*15", "بطن رفع أرجل 4*20"], "reasoning": "تمرين شاق للأرجل لزيادة إفراز الهرمونات.", "tip": "سخن جيداً قبل الأوزان الثقيلة."},
                    {"day": "اليوم 4: أكتاف وذراع (نحت وتحديد)", "muscle_group": "أكتاف وذراع", "exercises": ["برس أكتاف بار 4*8", "رفرفة جانبي 4*12", "رفرفة أمامي 3*10", "تجميع خلفي 3*12", "باي بار + تراي فرنسي 4*10", "باي شاكوش + تراي مسطرة 3*12", "باي كابل + تراي حبل 3*12", "بلانك 3*60 ثانية"], "reasoning": "رفع الشدة عن طريق السوبر ست.", "tip": "التركيز على الأداء الصحيح."}
                ]
            else: # تضخيم
                plan = [
                    {"day": "اليوم 1: صدر وباي (ضخامة قصوى)", "muscle_group": "صدر وباي", "exercises": ["بنش برس بار 4*8", "تجميع دمبل مائل 4*10", "تجميع مستوي 3*10", "تفتيح بالدمبل 3*12", "باي بار واقف 4*8", "باي دمبل تبادل 3*10", "باي تركيز 3*12", "باي شاكوش 3*10"], "reasoning": "تدمير الألياف العضلية للنمو.", "tip": "اهتم بالتغذية والراحة."},
                    {"day": "اليوم 2: ظهر وتراي (عرض وقوة)", "muscle_group": "ظهر وتراي", "exercises": ["عقلة واسع 4*الحد الأقصى", "سحب بار أرضي 4*8", "سحب واسع كابل 4*10", "منشار دمبل 3*10", "تراي فرنسي بار 4*8", "تراي مسطرة 4*10", "تراي حبل 3*12", "غطس دكة 3*15"], "reasoning": "بناء ظهر عريض وذراع خلفية ضخمة.", "tip": "النوم 8 ساعات أساسي."},
                    {"day": "اليوم 3: أكتاف وترابيس (كتلة هائلة)", "muscle_group": "أكتاف", "exercises": ["برس دمبل جالس 4*8", "رفرفة جانبي 4*12", "رفرفة أمامي 3*12", "رفرفة خلفي 3*12", "برس أرنولد 3*10", "هز أكتاف بار 4*12", "هز أكتاف دمبل 3*15", "بطن عجلة 4*20"], "reasoning": "بناء أكتاف عريضة وترابيس بارزة.", "tip": "تحكم في نزول الوزن."},
                    {"day": "اليوم 4: أرجل (تفجير العضلات)", "muscle_group": "أرجل", "exercises": ["سكوات حر 4*8", "دفع أرجل 4*10", "هاك سكوات 3*10", "رفرفة أمامي 4*12", "رفرفة خلفي 4*12", "سمانة واقف 4*15", "سمانة جالس 3*15", "مشي طعن 3*12 خطوة"], "reasoning": "تحفيز التستوستيرون الطبيعي.", "tip": "لا تهمل يوم الأرجل أبداً."}
                ]

        # Prepare plan but don't save yet
        return render_template('plan_display.html', plan=plan, goal=goal, plan_json=json.dumps(plan))
    return render_template('create_system.html')

@app.route('/save-system', methods=['POST'])
@login_required
def save_system():
    system_name = request.form.get('system_name')
    system_type = request.form.get('system_type', 'AI-Generated')
    content = request.form.get('content')
    
    if system_name and content:
        new_system = SavedSystem()
        new_system.user_id = current_user.id
        new_system.system_name = system_name
        new_system.system_type = system_type
        new_system.content = content
        db.session.add(new_system)
        db.session.commit()
        flash('تم حفظ النظام بنجاح!', 'success')
    return redirect(url_for('saved_systems'))

@app.route('/saved-systems')
@login_required
def saved_systems():
    systems = SavedSystem.query.filter_by(user_id=current_user.id).order_by(SavedSystem.date_saved.desc()).all()
    # Parse JSON content for display
    for s in systems:
        try:
            s.parsed_content = json.loads(s.content)
        except:
            s.parsed_content = s.content
    return render_template('saved_systems.html', systems=systems)

@app.route('/add-custom-system', methods=['GET', 'POST'])
@login_required
def add_custom_system():
    if request.method == 'POST':
        custom_plan = request.form.get('custom_plan')
        if custom_plan:
            new_system = SavedSystem()
            new_system.user_id = current_user.id
            new_system.system_name = request.form.get('system_name', 'نظام مخصص')
            new_system.system_type = 'User-Created'
            new_system.content = custom_plan
            db.session.add(new_system)
            db.session.commit()
            flash('تم حفظ نظامك الخاص بنجاح!', 'success')
        return redirect(url_for('workout_systems'))
    return render_template('add_custom_system.html')


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        s = str(value).strip().lower()
        if s == 'nan':
            return default
        return float(s)
    except (ValueError, TypeError):
        return default

@app.route('/account')
@login_required
@cache.cached(timeout=600)
def account():
    return render_template('profile.html')

@app.route('/plans')
@login_required
@cache.cached(timeout=600)
def plans():
    return redirect(url_for('workout_systems'))

@app.route('/profile')
@login_required
@cache.cached(timeout=600)
def profile():
    return render_template('profile.html')

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    user_message = request.json.get('message')
    if not user_message:
        return jsonify({'error': 'Empty message'}), 400

    if client:
        try:
            # اتصال بسيط ومباشر بالموديل
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"أجب على الرسالة التالية بالعربية بشكل طبيعي وودي: {user_message}"
            )
            
            return jsonify({'response': response.text})
        except Exception as e:
            app.logger.error(f"Chat Error: {e}")
            return jsonify({'response': 'عذراً، حاول مرة أخرى بعد ثوانٍ.'})
            
    return jsonify({'response': 'عذراً، خدمة الدردشة غير متوفرة حالياً.'}), 503

@app.route('/log-exercise', methods=['POST'])
@login_required
def log_exercise():
    exercise_name = request.form.get('exercise_name')
    weight_lifted = request.form.get('weight_lifted')
    reps = request.form.get('reps')
    sets_reps = request.form.get('sets_reps')
    
    if exercise_name and weight_lifted:
        log = ExerciseLog()
        log.user_id = current_user.id
        log.exercise_name = exercise_name
        log.weight_lifted = safe_float(weight_lifted)
        log.reps = int(reps) if reps else 0
        log.sets_reps = sets_reps
        # Add a slight delay to ensure the timestamp order is preserved for sets
        # If there's an existing set for this exercise today, we add 1 second to the last one
        last_log = ExerciseLog.query.filter_by(user_id=current_user.id, exercise_name=exercise_name).order_by(ExerciseLog.date.desc()).first()
        if last_log and last_log.date.date() == datetime.utcnow().date():
            log.date = last_log.date + timedelta(seconds=1)
        else:
            log.date = datetime.utcnow()
            
        db.session.add(log)
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'تم حفظ البيانات بنجاح!'})
    return jsonify({'status': 'error', 'message': 'البيانات غير مكتملة'}), 400

@app.route('/get-chart-data')
@login_required
def get_chart_data():
    exercise = request.args.get('exercise')
    if not exercise:
        return jsonify({'labels': [], 'data': []})
    
    logs = ExerciseLog.query.filter_by(user_id=current_user.id, exercise_name=exercise).order_by(ExerciseLog.date.asc()).all()
    labels = [log.date.strftime('%Y-%m-%d') for log in logs]
    
    data = []
    for log in logs:
        if log.reps > 0:
            data.append(log.weight_lifted * log.reps)
        else:
            try:
                import re
                match = re.search(r'(\d+)', log.sets_reps)
                r = float(match.group(1)) if match else 1.0
                data.append(log.weight_lifted * r)
            except:
                data.append(log.weight_lifted)
                
    return jsonify({'labels': labels, 'data': data})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

def init_db():
    with app.app_context():
        # Ensure static/uploads exists
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])
        
        # Remove old hardcoded admin
        old_admin = User.query.filter_by(phone='01275126698').first()
        if old_admin:
            db.session.delete(old_admin)
            db.session.commit()

        admin_phone = os.environ.get('ADMIN_PHONE')
        admin_password = os.environ.get('ADMIN_PASSWORD')
        
        if admin_phone and admin_password:
            admin = User.query.filter_by(phone=admin_phone).first()
            if not admin:
                hashed_pw = bcrypt.generate_password_hash(admin_password).decode('utf-8')
                admin = User(
                    full_name='ZEUS Admin',
                    username='admin',
                    phone=admin_phone,
                    password=hashed_pw,
                    is_admin=True,
                    age=0, height=0, weight=0
                )
                db.session.add(admin)
                db.session.commit()

if __name__ == '__main__':
     with app.app_context():
         try:
             db.create_all()
             init_db()
             print("✅ Database initialized successfully!")
         except Exception as e:
             print(f"❌ Failed to initialize database: {e}")

     app.run(host='0.0.0.0', port=5000)
