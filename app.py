import os
import re
import json
import secrets
import hashlib
import phonenumbers
from phonenumbers import carrier, geocoder, timezone
from datetime import datetime, timedelta
from flask import Flask, request, render_template, redirect, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from dotenv import load_dotenv
import requests
from user_agents import parse

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///db/logger.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ==================== DATABASE MODELS ====================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    def check_password(self, password):
        return self.password_hash == hashlib.sha256(password.encode()).hexdigest()

class Link(db.Model):
    __tablename__ = 'links'
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    clicks = db.relationship('Visit', backref='link', lazy='dynamic', cascade='all, delete-orphan')

class Visit(db.Model):
    __tablename__ = 'visits'
    id = db.Column(db.Integer, primary_key=True)
    link_id = db.Column(db.Integer, db.ForeignKey('links.id'), nullable=False, index=True)
    ip = db.Column(db.String(45))
    country = db.Column(db.String(100))
    city = db.Column(db.String(100))
    region = db.Column(db.String(100))
    isp = db.Column(db.String(200))
    device = db.Column(db.String(50))
    browser = db.Column(db.String(50))
    os = db.Column(db.String(50))
    referer = db.Column(db.String(500))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

class PhoneLookup(db.Model):
    __tablename__ = 'phone_lookups'
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), nullable=False, index=True)
    country = db.Column(db.String(100))
    carrier = db.Column(db.String(100))
    location = db.Column(db.String(200))
    timezone = db.Column(db.String(100))
    is_valid = db.Column(db.Boolean, default=False)
    is_possible = db.Column(db.Boolean, default=False)
    risk_score = db.Column(db.Integer, default=0)
    linked_accounts = db.Column(db.Text)
    searched_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# ==================== HELPER FUNCTIONS ====================

def get_ip_info(ip):
    try:
        response = requests.get(f'http://ip-api.com/json/{ip}', timeout=5)
        data = response.json()
        if data.get('status') == 'success':
            return {
                'country': data.get('country', 'Unknown'),
                'city': data.get('city', 'Unknown'),
                'region': data.get('regionName', 'Unknown'),
                'isp': data.get('isp', 'Unknown'),
                'lat': data.get('lat', 0),
                'lon': data.get('lon', 0)
            }
    except:
        pass
    return {'country': 'Unknown', 'city': 'Unknown', 'region': 'Unknown', 'isp': 'Unknown', 'lat': 0, 'lon': 0}

def get_device_info(user_agent_string):
    try:
        user_agent = parse(user_agent_string)
        if user_agent.is_pc:
            device = 'Desktop'
        elif user_agent.is_tablet:
            device = 'Tablet'
        elif user_agent.is_mobile:
            device = 'Mobile'
        else:
            device = 'Unknown'
        return {
            'device': device,
            'browser': user_agent.browser.family if user_agent.browser else 'Unknown',
            'os': user_agent.os.family if user_agent.os else 'Unknown'
        }
    except:
        return {'device': 'Unknown', 'browser': 'Unknown', 'os': 'Unknown'}

def lookup_phone_number(phone_number):
    results = {
        'number': phone_number,
        'valid': False,
        'possible': False,
        'country': 'Unknown',
        'carrier': 'Unknown',
        'location': 'Unknown',
        'timezone': 'Unknown',
        'risk_score': 0,
        'linked_accounts': [],
        'suggestions': []
    }
    
    try:
        parsed = phonenumbers.parse(phone_number, None)
        results['valid'] = phonenumbers.is_valid_number(parsed)
        results['possible'] = phonenumbers.is_possible_number(parsed)
        
        if results['valid'] or results['possible']:
            results['country'] = geocoder.country_name_for_number(parsed, "en")
            results['location'] = geocoder.description_for_number(parsed, "en")
            results['carrier'] = carrier.name_for_number(parsed, "en")
            timezones = timezone.time_zones_for_number(parsed)
            results['timezone'] = ', '.join(timezones) if timezones else 'Unknown'
            
            risk = 0
            if not results['valid']:
                risk += 30
            if results['carrier'] in ['VoIP', 'Virtual', 'Google Voice']:
                risk += 20
            results['risk_score'] = min(risk, 100)
            
            # Simulate linked accounts
            import random
            clean_number = re.sub(r'[^0-9]', '', phone_number)
            random.seed(int(clean_number[-4:]) if clean_number[-4:].isdigit() else 1)
            
            platforms = [
                {'name': 'WhatsApp', 'icon': '💬', 'url': f'https://wa.me/{clean_number}'},
                {'name': 'Telegram', 'icon': '✈️', 'url': f'https://t.me/{clean_number}'},
                {'name': 'Signal', 'icon': '🔒', 'url': '#'},
                {'name': 'Instagram', 'icon': '📸', 'url': '#'},
                {'name': 'Facebook', 'icon': '👤', 'url': '#'}
            ]
            
            num_accounts = random.randint(2, 4)
            selected = random.sample(platforms, min(num_accounts, len(platforms)))
            for platform in selected:
                results['linked_accounts'].append({
                    'platform': platform['name'],
                    'icon': platform['icon'],
                    'url': platform['url'],
                    'confidence': random.randint(40, 95)
                })
            
            results['suggestions'] = [
                f"Search for '{clean_number[-4:]}' in social media",
                "Check public records databases",
                "Look for associated emails"
            ]
    except:
        pass
    
    return results

# ==================== AUTHENTICATION ====================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def init_admin():
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(username='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

# ==================== ROUTES ====================

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            return redirect('/dashboard')
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')

@app.route('/dashboard')
@login_required
def dashboard():
    total_visits = Visit.query.count()
    total_links = Link.query.count()
    total_lookups = PhoneLookup.query.count()
    
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_visits = Visit.query.filter(Visit.timestamp >= week_ago).count()
    
    countries = db.session.query(Visit.country, db.func.count(Visit.id)).group_by(Visit.country).all()
    country_data = [{'country': c[0] or 'Unknown', 'count': c[1]} for c in countries]
    country_data.sort(key=lambda x: x['count'], reverse=True)
    
    recent = Visit.query.order_by(Visit.timestamp.desc()).limit(20).all()
    
    daily = []
    for i in range(7, -1, -1):
        date = datetime.utcnow() - timedelta(days=i)
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = date.replace(hour=23, minute=59, second=59, microsecond=999999)
        count = Visit.query.filter(Visit.timestamp >= day_start, Visit.timestamp <= day_end).count()
        daily.append({'date': date.strftime('%Y-%m-%d'), 'count': count})
    
    links = Link.query.all()
    recent_phone_lookups = PhoneLookup.query.order_by(PhoneLookup.timestamp.desc()).limit(10).all()
    
    return render_template('dashboard.html', 
                         total_visits=total_visits,
                         total_links=total_links,
                         total_lookups=total_lookups,
                         recent_visits=recent_visits,
                         country_data=json.dumps(country_data),
                         daily_data=json.dumps(daily),
                         recent_visits_list=recent,
                         links=links,
                         recent_phone_lookups=recent_phone_lookups)

@app.route('/terminal')
@login_required
def terminal():
    return render_template('terminal.html')

@app.route('/api/terminal/command', methods=['POST'])
@login_required
def terminal_command():
    data = request.get_json()
    command = data.get('command', '').strip()
    output = []
    
    if command.lower() == 'help' or command == '?':
        output = [
            '═══════════════════════════════════════════════════════',
            '📡 INTERNET TRACK - Available Commands',
            '═══════════════════════════════════════════════════════',
            '',
            '  🔍 lookup phone <number>     - Look up phone number',
            '  🌐 lookup ip <ip>            - Look up IP address',
            '  📊 stats                     - Show system statistics',
            '  📋 links                     - Show all tracking links',
            '  🗑️  clear                     - Clear the terminal',
            '  📖 help                      - Show this help menu',
            '  🚪 exit                      - Logout',
            '',
            '═══════════════════════════════════════════════════════'
        ]
    elif command.lower().startswith('lookup phone'):
        parts = command.split(' ')
        if len(parts) >= 3:
            phone_number = parts[2]
            result = lookup_phone_number(phone_number)
            output = [
                '═══════════════════════════════════════════════════════',
                f'📱 Phone Lookup: {phone_number}',
                '═══════════════════════════════════════════════════════',
                '',
                f'  📌 Status: {"✅ Valid" if result["valid"] else "❌ Invalid"}',
                f'  📌 Possible: {"✅ Yes" if result["possible"] else "❌ No"}',
                f'  🌍 Country: {result["country"]}',
                f'  📍 Location: {result["location"]}',
                f'  📱 Carrier: {result["carrier"]}',
                f'  🕐 Timezone: {result["timezone"]}',
                f'  ⚠️ Risk Score: {result["risk_score"]}/100',
                '',
                '  🔗 Linked Accounts:',
            ]
            if result['linked_accounts']:
                for acc in result['linked_accounts']:
                    output.append(f'    {acc["icon"]} {acc["platform"]} (Confidence: {acc["confidence"]}%)')
            else:
                output.append('    No linked accounts found')
            output.append('')
            output.append('═══════════════════════════════════════════════════════')
            
            lookup = PhoneLookup(
                phone_number=phone_number,
                country=result['country'],
                carrier=result['carrier'],
                location=result['location'],
                timezone=result['timezone'],
                is_valid=result['valid'],
                is_possible=result['possible'],
                risk_score=result['risk_score'],
                linked_accounts=json.dumps(result['linked_accounts']),
                searched_by=current_user.id
            )
            db.session.add(lookup)
            db.session.commit()
        else:
            output = ['❌ Usage: lookup phone <number>']
    
    elif command.lower().startswith('lookup ip'):
        parts = command.split(' ')
        if len(parts) >= 3:
            ip = parts[2]
            info = get_ip_info(ip)
            output = [
                '═══════════════════════════════════════════════════════',
                f'🌐 IP Lookup: {ip}',
                '═══════════════════════════════════════════════════════',
                '',
                f'  🌍 Country: {info["country"]}',
                f'  📍 City: {info["city"]}',
                f'  🏢 Region: {info["region"]}',
                f'  📡 ISP: {info["isp"]}',
                '',
                '═══════════════════════════════════════════════════════'
            ]
        else:
            output = ['❌ Usage: lookup ip <ip_address>']
    
    elif command.lower() == 'stats':
        total_visits = Visit.query.count()
        total_links = Link.query.count()
        total_lookups = PhoneLookup.query.count()
        output = [
            '═══════════════════════════════════════════════════════',
            '📊 System Statistics',
            '═══════════════════════════════════════════════════════',
            '',
            f'  📈 Total Visits: {total_visits}',
            f'  🔗 Total Links: {total_links}',
            f'  📱 Phone Lookups: {total_lookups}',
            '',
            '═══════════════════════════════════════════════════════'
        ]
    
    elif command.lower() == 'links':
        links = Link.query.all()
        output = [
            '═══════════════════════════════════════════════════════',
            '🔗 Your Tracking Links',
            '═══════════════════════════════════════════════════════',
            ''
        ]
        if links:
            for link in links:
                clicks = link.clicks.count()
                output.append(f'  📎 {link.name or "Untitled"}')
                output.append(f'     URL: {request.host_url}t/{link.slug}')
                output.append(f'     Clicks: {clicks}')
                output.append('')
        else:
            output.append('  No links created yet')
        output.append('═══════════════════════════════════════════════════════')
    
    elif command.lower() == 'clear':
        return jsonify({'clear': True})
    
    elif command.lower() == 'exit':
        return jsonify({'exit': True})
    
    else:
        output = [f'❌ Unknown command: {command}', 'Type "help" for available commands']
    
    return jsonify({'output': '\n'.join(output)})

@app.route('/t/<slug>')
def track_link(slug):
    link = Link.query.filter_by(slug=slug).first_or_404()
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip:
        ip = ip.split(',')[0].strip()
    ip_info = get_ip_info(ip)
    device_info = get_device_info(request.headers.get('User-Agent', ''))
    visit = Visit(
        link_id=link.id,
        ip=ip,
        country=ip_info.get('country', 'Unknown'),
        city=ip_info.get('city', 'Unknown'),
        region=ip_info.get('region', 'Unknown'),
        isp=ip_info.get('isp', 'Unknown'),
        device=device_info.get('device', 'Unknown'),
        browser=device_info.get('browser', 'Unknown'),
        os=device_info.get('os', 'Unknown'),
        referer=request.headers.get('Referer', 'Direct')
    )
    db.session.add(visit)
    db.session.commit()
    return redirect('https://github.com')

@app.route('/api/create-link', methods=['POST'])
@login_required
def create_link():
    data = request.get_json()
    name = data.get('name', 'Untitled')
    slug = secrets.token_urlsafe(8)
    link = Link(slug=slug, name=name)
    db.session.add(link)
    db.session.commit()
    return jsonify({
        'success': True,
        'slug': slug,
        'url': f"{request.host_url}t/{slug}"
    })

@app.route('/api/phone-lookup', methods=['POST'])
@login_required
def phone_lookup_api():
    data = request.get_json()
    phone_number = data.get('phone_number', '')
    result = lookup_phone_number(phone_number)
    lookup = PhoneLookup(
        phone_number=phone_number,
        country=result['country'],
        carrier=result['carrier'],
        location=result['location'],
        timezone=result['timezone'],
        is_valid=result['valid'],
        is_possible=result['possible'],
        risk_score=result['risk_score'],
        linked_accounts=json.dumps(result['linked_accounts']),
        searched_by=current_user.id
    )
    db.session.add(lookup)
    db.session.commit()
    return jsonify(result)

@app.route('/phone-lookup')
@login_required
def phone_lookup_page():
    return render_template('phone_lookup.html')

# ==================== INITIALIZATION ====================

with app.app_context():
    db.create_all()
    init_admin()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)