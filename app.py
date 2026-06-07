from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
from supabase_client import supabase
from datetime import datetime
import os
import json
import logging
import re
import random
import smtplib
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import bleach
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================
# КОНФИГУРАЦИЯ (все данные из переменных окружения)
# ============================================

# Секретный ключ Flask (из .env!)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise ValueError("❌ SECRET_KEY не задан! Добавьте его в .env файл.")

# Production-настройки (для безопасности)
app.config['DEBUG'] = False
app.config['TESTING'] = False
app.config['PROPAGATE_EXCEPTIONS'] = True
app.config['SESSION_COOKIE_SECURE'] = True  # Только HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Защита от XSS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # Защита от CSRF

# Настройка для загрузки файлов
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_MIMES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}

# Папка для загрузок (вне static, для безопасности)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'news')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB максимум

# Создаем папку для загрузок, если её нет
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Настройки почты (из .env)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.mail.ru")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_FROM = os.environ.get("EMAIL_FROM")

# Проверяем наличие почтовых настроек
if not EMAIL_USER or not EMAIL_PASSWORD:
    logger.warning("⚠️ Почтовые настройки не заданы в .env! Email-уведомления не будут работать.")

# Разрешенные HTML-теги для безопасного форматирования новостей
ALLOWED_HTML_TAGS = ['b', 'strong', 'i', 'em', 'u', 'p', 'br', 'ul', 'ol', 'li', 'a', 'h3', 'h4', 'span']
ALLOWED_HTML_ATTRIBUTES = {'a': ['href', 'title', 'target'], 'span': ['style']}

# Токен для безопасной верификации
serializer = URLSafeTimedSerializer(app.secret_key)

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def allowed_file(file):
    """Безопасная проверка загружаемого файла (расширение + сигнатура)"""
    if not file or not file.filename:
        return False
    
    # Проверяем расширение
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning(f"❌ Запрещенное расширение файла: {ext}")
        return False
    
    # Проверяем сигнатуры файлов (magic numbers) - базовая защита от подделки
    try:
        file_head = file.read(12)
        file.seek(0)
        
        if ext in ['jpg', 'jpeg']:
            if file_head[:2] != b'\xff\xd8':
                logger.warning(f"❌ Файл не является JPEG")
                return False
        elif ext == 'png':
            if file_head[:8] != b'\x89PNG\r\n\x1a\n':
                logger.warning(f"❌ Файл не является PNG")
                return False
        elif ext == 'gif':
            if file_head[:3] != b'GIF':
                logger.warning(f"❌ Файл не является GIF")
                return False
        elif ext == 'webp':
            if file_head[:4] != b'RIFF' or file_head[8:12] != b'WEBP':
                logger.warning(f"❌ Файл не является WEBP")
                return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки сигнатуры: {e}")
        return False
    
    return True

def safe_html(text):
    """Очистка HTML от опасных тегов и скриптов (защита от XSS)"""
    if not text:
        return ""
    return bleach.clean(
        text, 
        tags=ALLOWED_HTML_TAGS, 
        attributes=ALLOWED_HTML_ATTRIBUTES,
        strip=True
    )

def is_valid_email(email):
    """Валидация email адреса"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def is_valid_phone(phone):
    """Валидация номера телефона (+7XXXXXXXXXX или 8XXXXXXXXXX)"""
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    pattern = r'^(\+7|8)[0-9]{10}$'
    return re.match(pattern, cleaned) is not None

def format_phone(phone):
    """Форматирование телефона в единый формат +7XXXXXXXXXX"""
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    if cleaned.startswith('8'):
        cleaned = '+7' + cleaned[1:]
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return cleaned

def generate_verification_code():
    """Генерация 6-значного кода подтверждения"""
    return str(random.randint(100000, 999999))

def send_verification_code(email, code):
    """Отправка кода подтверждения на email"""
    if not EMAIL_USER or not EMAIL_PASSWORD:
        logger.error("❌ Почта не настроена, код не отправлен")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = email
        msg['Subject'] = 'Подтверждение регистрации - КиберКачалка'
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #0a0a14; color: #e0e0e0; }}
                .container {{ max-width: 500px; margin: 0 auto; padding: 30px; background: linear-gradient(135deg, #14142a, #0d0d1f); border-radius: 20px; border: 1px solid #54fdf6; }}
                h2 {{ color: #54fdf6; text-align: center; }}
                .code {{ font-size: 36px; font-weight: bold; text-align: center; padding: 20px; background: rgba(84, 253, 246, 0.1); border-radius: 15px; margin: 20px 0; letter-spacing: 5px; color: #ff68ff; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #888; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>🔐 Добро пожаловать в КиберКачалку!</h2>
                <p>Для завершения регистрации введите следующий код подтверждения:</p>
                <div class="code">{code}</div>
                <p>Код действителен в течение 10 минут.</p>
                <p>Если вы не регистрировались на нашем сайте, просто проигнорируйте это письмо.</p>
                <div class="footer">
                    <p>© 2026 КиберКачалка | Фиджитал центр НТПТиС</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"✅ Код подтверждения отправлен на {email}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка отправки email: {e}")
        return False

def send_reset_password_email(email, reset_url):
    """Отправка ссылки для сброса пароля"""
    if not EMAIL_USER or not EMAIL_PASSWORD:
        logger.error("❌ Почта не настроена, ссылка не отправлена")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = email
        msg['Subject'] = 'Сброс пароля - КиберКачалка'
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #0a0a14; color: #e0e0e0; }}
                .container {{ max-width: 500px; margin: 0 auto; padding: 30px; background: linear-gradient(135deg, #14142a, #0d0d1f); border-radius: 20px; border: 1px solid #54fdf6; }}
                h2 {{ color: #54fdf6; text-align: center; }}
                .button {{ display: inline-block; padding: 12px 30px; background: linear-gradient(135deg, #54fdf6, #2bb8b2); color: #0a0a14; text-decoration: none; border-radius: 30px; font-weight: bold; margin: 20px 0; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #888; }}
                .warning {{ color: #ff9800; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>🔐 Сброс пароля</h2>
                <p>Вы запросили сброс пароля для аккаунта <strong>{email}</strong>.</p>
                <p>Для установки нового пароля нажмите на кнопку ниже:</p>
                <div style="text-align: center;">
                    <a href="{reset_url}" class="button">Сбросить пароль</a>
                </div>
                <p class="warning">⚠️ Если вы не запрашивали сброс пароля, просто проигнорируйте это письмо.</p>
                <p>Ссылка действительна в течение 1 часа.</p>
                <div class="footer">
                    <p>© 2026 КиберКачалка | Фиджитал центр НТПТиС</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(html, 'html'))
        
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"✅ Ссылка для сброса пароля отправлена на {email}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка отправки письма для сброса пароля: {e}")
        return False

# ============================================
# ФИЛЬТРЫ ДЛЯ ШАБЛОНОВ
# ============================================

def from_json_filter(value):
    """Фильтр для парсинга JSON в шаблонах"""
    try:
        return json.loads(value) if value else []
    except:
        return []

app.jinja_env.filters['from_json'] = from_json_filter
app.jinja_env.filters['safe_html'] = safe_html

# ============================================
# ГЛАВНЫЕ СТРАНИЦЫ
# ============================================

@app.route('/')
def index():
    """Главная страница"""
    try:
        response = supabase.table('news')\
            .select('*')\
            .eq('is_active', True)\
            .order('date_published', desc=True)\
            .limit(3)\
            .execute()
        latest_news = response.data
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки последних новостей: {e}")
        latest_news = []
    
    # Передаем данные авторизованного пользователя в шаблон для авто-заполнения формы
    user_data = None
    if session.get('user_id'):
        try:
            user_response = supabase.table('users')\
                .select('full_name, phone, email')\
                .eq('id', session['user_id'])\
                .execute()
            if user_response.data:
                user_data = user_response.data[0]
                logger.info(f"✅ Данные пользователя загружены для авто-заполнения формы")
        except Exception as e:
            logger.error(f"Ошибка получения данных пользователя: {e}")
    
    return render_template('index.html', latest_news=latest_news, user_data=user_data)

@app.route('/news')
def news_page():
    """Страница всех новостей"""
    try:
        response = supabase.table('news')\
            .select('*')\
            .eq('is_active', True)\
            .order('date_published', desc=True)\
            .execute()
        news_list = response.data
        return render_template('news.html', news=news_list)
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки новостей: {e}")
        flash('Не удалось загрузить новости. Попробуйте позже.', 'error')
        return render_template('news.html', news=[])

# ============================================
# РЕГИСТРАЦИЯ С ПОДТВЕРЖДЕНИЕМ EMAIL
# ============================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Регистрация нового пользователя"""
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        full_name = request.form['full_name'].strip()
        phone = request.form['phone'].strip()
        
        # Валидация
        if not is_valid_email(email):
            flash('Введите корректный email адрес (например: name@domain.ru)', 'error')
            return render_template('register.html')
        
        if not is_valid_phone(phone):
            flash('Введите корректный номер телефона (например: +79161234567 или 89161234567)', 'error')
            return render_template('register.html')
        
        phone = format_phone(phone)
        
        if len(password) < 6:
            flash('Пароль должен содержать не менее 6 символов', 'error')
            return render_template('register.html')
        
        # Проверка существующего пользователя
        existing = supabase.table('users')\
            .select('*')\
            .eq('email', email)\
            .execute()
        
        if existing.data:
            flash('Пользователь с таким email уже существует', 'error')
            return render_template('register.html')
        
        # Генерируем код подтверждения
        verification_code = generate_verification_code()
        
        # Отправляем код на email
        if not send_verification_code(email, verification_code):
            flash('Не удалось отправить код подтверждения. Проверьте правильность email или попробуйте позже.', 'error')
            return render_template('register.html')
        
        # Сохраняем данные пользователя в сессию для подтверждения
        session['pending_user'] = {
            'email': email,
            'password_hash': generate_password_hash(password),
            'full_name': full_name,
            'phone': phone,
            'verification_code': verification_code,
            'code_expires': datetime.now().timestamp() + 600
        }
        
        flash(f'Код подтверждения отправлен на {email}. Введите его для завершения регистрации.', 'info')
        return redirect(url_for('verify_code'))
    
    return render_template('register.html')

@app.route('/verify_code', methods=['GET', 'POST'])
def verify_code():
    """Страница подтверждения email-кодом"""
    if 'pending_user' not in session:
        flash('Сначала заполните форму регистрации', 'error')
        return redirect(url_for('register'))
    
    pending_user = session['pending_user']
    email = pending_user['email']
    
    # Проверяем не истек ли код
    if datetime.now().timestamp() > pending_user['code_expires']:
        session.pop('pending_user', None)
        flash('Время подтверждения истекло. Пожалуйста, зарегистрируйтесь заново.', 'error')
        return redirect(url_for('register'))
    
    if request.method == 'POST':
        entered_code = request.form.get('code', '').strip()
        
        if entered_code == pending_user['verification_code']:
            # Код верный - создаем пользователя
            user_data = {
                'email': pending_user['email'],
                'password_hash': pending_user['password_hash'],
                'full_name': pending_user['full_name'],
                'phone': pending_user['phone'],
                'role': 'user',
                'email_verified': True,
                'created_at': datetime.now().isoformat()
            }
            
            try:
                response = supabase.table('users').insert(user_data).execute()
                
                if response.data:
                    session.pop('pending_user', None)
                    flash('Регистрация успешна! Теперь войдите в систему.', 'success')
                    return redirect(url_for('login'))
                else:
                    flash('Ошибка регистрации. Попробуйте позже.', 'error')
                    return redirect(url_for('register'))
            except Exception as e:
                logger.error(f"❌ Ошибка создания пользователя: {e}")
                flash('Ошибка регистрации. Попробуйте позже.', 'error')
                return redirect(url_for('register'))
        else:
            flash('Неверный код подтверждения. Попробуйте еще раз.', 'error')
    
    masked_email = email[:3] + '***' + email[email.find('@'):] if '@' in email else email
    return render_template('verify_code.html', email=masked_email, full_email=email)

@app.route('/resend_code', methods=['POST'])
def resend_code():
    """Повторная отправка кода подтверждения"""
    if 'pending_user' not in session:
        return jsonify({'success': False, 'message': 'Сессия истекла. Заполните форму заново.'})
    
    pending_user = session['pending_user']
    email = pending_user['email']
    
    new_code = generate_verification_code()
    
    if send_verification_code(email, new_code):
        session['pending_user']['verification_code'] = new_code
        session['pending_user']['code_expires'] = datetime.now().timestamp() + 600
        session.modified = True
        return jsonify({'success': True, 'message': 'Новый код отправлен!'})
    else:
        return jsonify({'success': False, 'message': 'Не удалось отправить код. Попробуйте позже.'})

# ============================================
# ВХОД И ВЫХОД
# ============================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Авторизация пользователя"""
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        
        # Ищем пользователя по email
        response = supabase.table('users')\
            .select('*')\
            .eq('email', email)\
            .execute()
        
        if response.data:
            user = response.data[0]
            
            # Проверяем пароль с помощью безопасного сравнения
            if check_password_hash(user['password_hash'], password):
                # Проверяем, подтвержден ли email
                if not user.get('email_verified', False):
                    flash('Подтвердите email перед входом в систему. Проверьте вашу почту.', 'error')
                    return render_template('login.html')
                
                session['user_id'] = user['id']
                session['user_email'] = user['email']
                session['user_name'] = user['full_name']
                session['user_role'] = user['role']
                flash(f'Добро пожаловать, {user["full_name"]}!', 'success')
                
                if user['role'] == 'admin':
                    return redirect(url_for('admin_dashboard'))
                return redirect(url_for('profile'))
            else:
                flash('Неверный email или пароль', 'error')
        else:
            flash('Неверный email или пароль', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Выход из системы"""
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))

# ============================================
# API МАРШРУТЫ
# ============================================

@app.route('/api/prices')
def get_prices():
    """Получение списка услуг (публичный)"""
    try:
        response = supabase.table('prices')\
            .select('*')\
            .eq('is_active', True)\
            .order('sort_order')\
            .execute()
        return jsonify(response.data)
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки цен: {e}")
        return jsonify([])

@app.route('/api/document/<doc_key>')
def get_document(doc_key):
    """Получение документа (публичный)"""
    try:
        response = supabase.table('documents')\
            .select('*')\
            .eq('doc_key', doc_key)\
            .eq('is_active', True)\
            .execute()
        if response.data:
            return jsonify(response.data[0])
        return jsonify({'error': 'Document not found'}), 404
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки документа: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================
# СОЗДАНИЕ ЗАЯВКИ
# ============================================

@app.route('/create_booking', methods=['POST'])
def create_booking():
    """Создание заявки на запись"""
    data = request.get_json() or request.form
    
    phone = data.get('phone', '')
    if not is_valid_phone(phone):
        return jsonify({'success': False, 'message': 'Введите корректный номер телефона (например: +79161234567)'})
    
    email = data.get('email', '')
    if email and not is_valid_email(email):
        return jsonify({'success': False, 'message': 'Введите корректный email адрес'})
    
    customer_name = data.get('name', '').strip()
    client_type = data.get('client_type', 'individual')
    
    if not customer_name and client_type == 'organization':
        customer_name = data.get('org_name', '').strip()
    
    if not customer_name:
        return jsonify({'success': False, 'message': 'Укажите имя или название организации'})
    
    services = data.get('services', '')
    if isinstance(services, list):
        services = ', '.join(services)
    
    services_details = data.get('services_details', [])
    total_price = 0
    
    if isinstance(services_details, list):
        for detail in services_details:
            if detail.get('is_excursion') and detail.get('persons_count'):
                persons = int(detail.get('persons_count', 1))
                base_price = float(detail.get('price', 0))
                detail['price'] = base_price
                detail['total_price'] = base_price * persons
                detail['persons_count'] = persons
                total_price += detail['total_price']
            else:
                total_price += float(detail.get('price', 0))
        
        services_details_json = json.dumps(services_details, ensure_ascii=False)
    else:
        services_details_json = services_details
        total_price = float(data.get('total_price', 0))
    
    if total_price == 0:
        total_price = float(data.get('total_price', 0))
    
    booking_data = {
        'user_id': session.get('user_id'),
        'customer_name': customer_name,
        'client_type': client_type,
        'phone': format_phone(phone),
        'email': email.strip().lower() if email else '',
        'service': services,
        'services_details': services_details_json,
        'total_price': total_price,
        'booking_date': data.get('date', ''),
        'booking_time': data.get('time', ''),
        'duration': data.get('duration', '1'),
        'comment': data.get('comment', ''),
        'status': 'new',
        'bonus_added': False,
        'bonus_points': 0,
        'created_at': datetime.now().isoformat()
    }
    
    try:
        response = supabase.table('bookings').insert(booking_data).execute()
        
        if response.data:
            return jsonify({'success': True, 'message': 'Заявка успешно отправлена! Администратор свяжется с вами для подтверждения.'})
        else:
            return jsonify({'success': False, 'message': 'Ошибка при сохранении заявки'})
    except Exception as e:
        logger.error(f"❌ Ошибка создания заявки: {e}")
        return jsonify({'success': False, 'message': f'Ошибка сервера: {str(e)}'})

# ============================================
# ОТПРАВКА СООБЩЕНИЯ
# ============================================

@app.route('/send_contact', methods=['POST'])
def send_contact():
    """Отправка сообщения через форму обратной связи"""
    data = request.get_json() or request.form
    
    phone = data.get('phone', '')
    if not is_valid_phone(phone):
        return jsonify({'success': False, 'message': 'Введите корректный номер телефона'})
    
    contact_data = {
        'name': data.get('name', '').strip(),
        'phone': format_phone(phone),
        'message': data.get('message', '').strip(),
        'created_at': datetime.now().isoformat()
    }
    
    try:
        response = supabase.table('contacts').insert(contact_data).execute()
        
        if response.data:
            return jsonify({'success': True, 'message': 'Сообщение отправлено!'})
        return jsonify({'success': False, 'message': 'Ошибка отправки'})
    except Exception as e:
        logger.error(f"❌ Ошибка отправки контакта: {e}")
        return jsonify({'success': False, 'message': str(e)})

# ============================================
# ЛИЧНЫЙ КАБИНЕТ
# ============================================

@app.route('/profile')
def profile():
    """Личный кабинет пользователя"""
    if not session.get('user_id'):
        flash('Пожалуйста, войдите в систему', 'error')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    
    try:
        user_response = supabase.table('users')\
            .select('*')\
            .eq('id', user_id)\
            .execute()
        
        if not user_response.data:
            flash('Пользователь не найден', 'error')
            return redirect(url_for('index'))
        
        user = user_response.data[0]
        
        bookings_response = supabase.table('bookings')\
            .select('*')\
            .eq('user_id', user_id)\
            .order('created_at', desc=True)\
            .execute()
        
        bookings = bookings_response.data
        
        bonus_response = supabase.table('user_bonuses')\
            .select('*')\
            .eq('user_id', user_id)\
            .execute()
        
        bonus = bonus_response.data[0] if bonus_response.data else None
        
        if not bonus:
            bonus_data = {
                'user_id': user_id,
                'balance': 0,
                'total_earned': 0,
                'level': 1,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            supabase.table('user_bonuses').insert(bonus_data).execute()
            bonus = bonus_data
        
        completed_bookings = [b for b in bookings if b.get('status') == 'completed']
        total_spent = sum(float(b.get('total_price', 0)) for b in completed_bookings)
        
        next_level_points = max(0, 500 - (bonus.get('balance', 0) if bonus else 0))
        next_level_progress = min(100, int((bonus.get('balance', 0) / 500) * 100)) if bonus else 0
        
        stats = {
            'total_bookings': len(bookings),
            'completed_bookings': len(completed_bookings),
            'total_spent': int(total_spent)
        }
        
        bonus_data = {
            'balance': bonus.get('balance', 0) if bonus else 0,
            'total_earned': bonus.get('total_earned', 0) if bonus else 0,
            'level': bonus.get('level', 1) if bonus else 1,
            'next_level_points': next_level_points,
            'next_level_progress': next_level_progress
        }
        
        return render_template('profile.html', 
                             user=user, 
                             bookings=bookings,
                             stats=stats,
                             bonus=bonus_data)
                             
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки профиля: {e}")
        flash('Ошибка загрузки профиля', 'error')
        return redirect(url_for('index'))

# ============================================
# БЕЗОПАСНАЯ ОТДАЧА ИЗОБРАЖЕНИЙ
# ============================================

@app.route('/uploads/news/<filename>')
def serve_news_image(filename):
    """Безопасная отдача изображений новостей"""
    # Защита от path traversal атак
    if '..' in filename or filename.startswith('/') or '\\' in filename:
        return "Invalid filename", 400
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return "Image not found", 404
    
    # Проверяем расширение файла
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return "Invalid file type", 403
    
    return send_file(filepath)

# ============================================
# АДМИН ПАНЕЛЬ
# ============================================

@app.route('/admin')
def admin_dashboard():
    """Административная панель"""
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    for attempt in range(3):
        try:
            logger.info(f"🔄 Попытка {attempt+1} загрузки админ-панели")
            
            bookings = supabase.table('bookings')\
                .select('*')\
                .order('created_at', desc=True)\
                .limit(100)\
                .execute()
            
            contacts = supabase.table('contacts')\
                .select('*')\
                .order('created_at', desc=True)\
                .limit(100)\
                .execute()
            
            news = supabase.table('news')\
                .select('*')\
                .order('date_published', desc=True)\
                .limit(100)\
                .execute()
            
            users = supabase.table('users')\
                .select('*')\
                .limit(100)\
                .execute()
            
            logger.info("✅ Данные админ-панели успешно загружены")
            return render_template('admin.html', 
                                 bookings=bookings.data,
                                 contacts=contacts.data,
                                 news=news.data,
                                 users=users.data)
                                 
        except Exception as e:
            logger.error(f"❌ Ошибка при попытке {attempt+1}: {e}")
            if attempt == 2:
                flash('Ошибка загрузки данных админ-панели. Попробуйте позже.', 'error')
                return render_template('admin.html', 
                                     bookings=[],
                                     contacts=[],
                                     news=[],
                                     users=[])
            import time
            time.sleep(2)

@app.route('/admin/update_booking/<int:booking_id>', methods=['POST'])
def update_booking(booking_id):
    """Обновление статуса заявки (только админ)"""
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    status = request.json.get('status')
    if status not in ['new', 'confirmed', 'cancelled', 'completed']:
        return jsonify({'error': 'Invalid status'}), 400
    
    response = supabase.table('bookings')\
        .update({'status': status, 'updated_at': datetime.now().isoformat()})\
        .eq('id', booking_id)\
        .execute()
    
    return jsonify({'success': True})

@app.route('/admin/add_news', methods=['POST'])
def add_news():
    """Добавление новости (только админ) с безопасной загрузкой файлов"""
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    image_filename = None
    
    if 'image_file' in request.files:
        file = request.files['image_file']
        if file and file.filename and allowed_file(file):
            ext = file.filename.rsplit('.', 1)[1].lower()
            image_filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
            logger.info(f"✅ Изображение сохранено: {image_filename}")
    
    # Очищаем HTML-контент от опасных тегов (защита от XSS!)
    title = request.form['title'].strip()
    content = request.form['content'].strip()
    excerpt = request.form.get('excerpt', title[:100])[:200]
    
    news_data = {
        'title': title,
        'content': safe_html(content),  # Безопасное экранирование!
        'excerpt': safe_html(excerpt),
        'image_emoji': request.form.get('image_emoji', '📢'),
        'image_url': image_filename,
        'date_published': datetime.now().strftime('%d %B %Y'),
        'is_active': True,
        'created_by': session.get('user_id')
    }
    
    response = supabase.table('news').insert(news_data).execute()
    
    if response.data:
        flash('Новость добавлена!', 'success')
    else:
        flash('Ошибка добавления', 'error')
    
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_news/<int:news_id>')
def delete_news(news_id):
    """Удаление новости (только админ)"""
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    # Получаем новость перед удалением, чтобы удалить файл
    news_item = supabase.table('news').select('image_url').eq('id', news_id).execute()
    if news_item.data and news_item.data[0].get('image_url'):
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], news_item.data[0]['image_url'])
        if os.path.exists(image_path):
            os.remove(image_path)
            logger.info(f"✅ Удалено изображение: {image_path}")
    
    supabase.table('news').delete().eq('id', news_id).execute()
    flash('Новость удалена', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/get_news/<int:news_id>')
def get_news(news_id):
    """Получение новости для редактирования (только админ)"""
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        response = supabase.table('news').select('*').eq('id', news_id).execute()
        if response.data:
            return jsonify(response.data[0])
        return jsonify({'error': 'News not found'}), 404
    except Exception as e:
        logger.error(f"❌ Ошибка получения новости: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin/edit_news/<int:news_id>', methods=['POST'])
def edit_news(news_id):
    """Редактирование новости (только админ)"""
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    title = request.form.get('title', '').strip()
    excerpt = request.form.get('excerpt', '')
    content = request.form.get('content', '')
    image_emoji = request.form.get('image_emoji', '📢')
    delete_image = request.form.get('delete_image') == 'true'
    
    # Получаем текущую новость
    current_news = supabase.table('news').select('image_url').eq('id', news_id).execute()
    image_filename = current_news.data[0].get('image_url') if current_news.data else None
    
    # Если нужно удалить изображение
    if delete_image and image_filename:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
        if os.path.exists(image_path):
            os.remove(image_path)
            logger.info(f"✅ Удалено изображение при редактировании: {image_path}")
        image_filename = None
    
    # Если загружено новое изображение
    if 'image_file' in request.files:
        file = request.files['image_file']
        if file and file.filename and allowed_file(file):
            # Удаляем старое изображение
            if image_filename:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
                if os.path.exists(old_path):
                    os.remove(old_path)
            # Сохраняем новое
            ext = file.filename.rsplit('.', 1)[1].lower()
            image_filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
            logger.info(f"✅ Новое изображение сохранено: {image_filename}")
    
    # Обновляем новость с безопасной очисткой HTML
    news_data = {
        'title': title,
        'content': safe_html(content),
        'excerpt': safe_html(excerpt[:200]),
        'image_emoji': image_emoji,
        'image_url': image_filename,
        'updated_at': datetime.now().isoformat()
    }
    
    response = supabase.table('news').update(news_data).eq('id', news_id).execute()
    
    if response.data:
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Ошибка обновления новости'}), 500

@app.route('/admin/add_bonus/<int:booking_id>', methods=['POST'])
def add_bonus(booking_id):
    """Начисление бонусов за запись (только админ)"""
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        booking_response = supabase.table('bookings')\
            .select('*')\
            .eq('id', booking_id)\
            .execute()
        
        if not booking_response.data:
            return jsonify({'error': 'Booking not found'}), 404
        
        booking = booking_response.data[0]
        
        if booking.get('status') != 'completed':
            return jsonify({'error': 'Booking must be completed first'}), 400
        
        if booking.get('bonus_added'):
            return jsonify({'error': 'Bonuses already added'}), 400
        
        user_id = booking.get('user_id')
        if not user_id:
            return jsonify({'error': 'User not found for this booking'}), 400
        
        total_price = float(booking.get('total_price', 0))
        
        bonus_response = supabase.table('user_bonuses')\
            .select('*')\
            .eq('user_id', user_id)\
            .execute()
        
        points_to_add = int(total_price * 0.05)
        
        if points_to_add > 0:
            if bonus_response.data:
                current_bonus = bonus_response.data[0]
                new_balance = current_bonus.get('balance', 0) + points_to_add
                new_total_earned = current_bonus.get('total_earned', 0) + points_to_add
                
                new_level = current_bonus.get('level', 1)
                if new_balance >= 500 and current_bonus.get('level', 1) == 1:
                    new_level = 2
                elif new_balance >= 1500 and current_bonus.get('level', 1) == 2:
                    new_level = 3
                elif new_balance >= 3000 and current_bonus.get('level', 1) == 3:
                    new_level = 4
                
                supabase.table('user_bonuses')\
                    .update({
                        'balance': new_balance,
                        'total_earned': new_total_earned,
                        'level': new_level,
                        'updated_at': datetime.now().isoformat()
                    })\
                    .eq('user_id', user_id)\
                    .execute()
            else:
                supabase.table('user_bonuses').insert({
                    'user_id': user_id,
                    'balance': points_to_add,
                    'total_earned': points_to_add,
                    'level': 1,
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat()
                }).execute()
            
            supabase.table('bookings')\
                .update({'bonus_added': True, 'bonus_points': points_to_add})\
                .eq('id', booking_id)\
                .execute()
            
            return jsonify({'success': True, 'points': points_to_add})
        
        return jsonify({'error': 'No points to add'}), 400
        
    except Exception as e:
        logger.error(f"❌ Ошибка начисления бонусов: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    """Удаление пользователя (только админ)"""
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    supabase.table('users').delete().eq('id', user_id).execute()
    flash('Пользователь удален', 'success')
    return redirect(url_for('admin_dashboard'))

# ============================================
# ВОССТАНОВЛЕНИЕ ПАРОЛЯ
# ============================================

@app.route('/reset_password_request', methods=['POST'])
def reset_password_request():
    """Запрос на сброс пароля - отправка ссылки на email"""
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    
    if not email:
        return jsonify({'success': False, 'message': 'Введите email адрес'})
    
    if not is_valid_email(email):
        return jsonify({'success': False, 'message': 'Введите корректный email адрес'})
    
    # Проверяем, существует ли пользователь
    response = supabase.table('users').select('*').eq('email', email).execute()
    
    if not response.data:
        # Не сообщаем, что email не найден (безопасность)
        return jsonify({'success': True, 'message': 'Если пользователь с таким email существует, ссылка для сброса пароля будет отправлена.'})
    
    # Генерируем токен (действителен 1 час)
    token = serializer.dumps(email, salt='password-reset-salt')
    reset_url = url_for('reset_password', token=token, _external=True)
    
    if send_reset_password_email(email, reset_url):
        return jsonify({'success': True, 'message': 'Ссылка для сброса пароля отправлена на вашу почту.'})
    else:
        return jsonify({'success': False, 'message': 'Ошибка отправки письма. Попробуйте позже.'})

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Страница сброса пароля"""
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        flash('Ссылка для сброса пароля истекла. Запросите новую.', 'error')
        return redirect(url_for('login'))
    except BadSignature:
        flash('Неверная ссылка для сброса пароля.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        new_password = request.form.get('password', '')
        
        if len(new_password) < 6:
            flash('Пароль должен содержать не менее 6 символов', 'error')
            return render_template('reset_password.html', token=token)
        
        password_hash = generate_password_hash(new_password)
        
        response = supabase.table('users').update({
            'password_hash': password_hash,
            'updated_at': datetime.now().isoformat()
        }).eq('email', email).execute()
        
        if response.data:
            flash('Пароль успешно изменен! Теперь войдите в систему.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Ошибка при смене пароля. Попробуйте позже.', 'error')
    
    return render_template('reset_password.html', token=token)

# ============================================
# ЗАПУСК (только для локальной разработки)
# ============================================

# Для production используйте gunicorn:
# gunicorn --bind 0.0.0.0:8000 wsgi:app

if __name__ == '__main__':
    # Только для локальной разработки!
    # На хостинге используйте gunicorn
    app.run(debug=False, host='0.0.0.0', port=5000)