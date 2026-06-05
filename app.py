from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from supabase_client import supabase
from datetime import datetime
import hashlib
import os
import json
import logging
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-here")

# Фильтр для парсинга JSON в шаблонах
def from_json_filter(value):
    import json
    try:
        return json.loads(value) if value else []
    except:
        return []

app.jinja_env.filters['from_json'] = from_json_filter

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Валидация email
def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# Валидация телефона (принимает +7XXXXXXXXXX или 8XXXXXXXXXX)
def is_valid_phone(phone):
    # Очищаем от лишних символов
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    # Проверяем формат: +7XXXXXXXXXX (11 цифр после +7) или 8XXXXXXXXXX (10 цифр)
    pattern = r'^(\+7|8)[0-9]{10}$'
    return re.match(pattern, cleaned) is not None

def format_phone(phone):
    # Очищаем от лишних символов
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    if cleaned.startswith('8'):
        cleaned = '+7' + cleaned[1:]
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return cleaned

# ============ ГЛАВНЫЕ СТРАНИЦЫ ============
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/news')
def news_page():
    try:
        response = supabase.table('news')\
            .select('*')\
            .eq('is_active', True)\
            .order('date_published', desc=True)\
            .execute()
        news_list = response.data
        return render_template('news.html', news=news_list)
    except Exception as e:
        logger.error(f"Ошибка загрузки новостей: {e}")
        flash('Не удалось загрузить новости. Попробуйте позже.', 'error')
        return render_template('news.html', news=[])

# ============ РЕГИСТРАЦИЯ ============
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        full_name = request.form['full_name'].strip()
        phone = request.form['phone'].strip()
        
        # Валидация email
        if not is_valid_email(email):
            flash('Введите корректный email адрес (например: name@domain.ru)', 'error')
            return render_template('register.html')
        
        # Валидация телефона
        if not is_valid_phone(phone):
            flash('Введите корректный номер телефона (например: +79161234567 или 89161234567)', 'error')
            return render_template('register.html')
        
        # Форматируем телефон
        phone = format_phone(phone)
        
        # Проверка длины пароля
        if len(password) < 6:
            flash('Пароль должен содержать не менее 6 символов', 'error')
            return render_template('register.html')
        
        existing = supabase.table('users')\
            .select('*')\
            .eq('email', email)\
            .execute()
        
        if existing.data:
            flash('Пользователь с таким email уже существует', 'error')
            return render_template('register.html')
        
        user_data = {
            'email': email,
            'password_hash': hash_password(password),
            'full_name': full_name,
            'phone': phone,
            'role': 'user',
            'email_verified': False,
            'created_at': datetime.now().isoformat()
        }
        
        response = supabase.table('users').insert(user_data).execute()
        
        if response.data:
            flash('Регистрация успешна! Теперь войдите в систему.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Ошибка регистрации. Попробуйте позже.', 'error')
    
    return render_template('register.html')

# ============ ВХОД ============
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        password_hash = hash_password(password)
        
        response = supabase.table('users')\
            .select('*')\
            .eq('email', email)\
            .eq('password_hash', password_hash)\
            .execute()
        
        if response.data:
            user = response.data[0]
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
    
    return render_template('login.html')

# ============ ВЫХОД ============
@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))

# ============ ПОЛУЧЕНИЕ ПРЕЙСКУРАНТА ============
@app.route('/api/prices')
def get_prices():
    try:
        response = supabase.table('prices')\
            .select('*')\
            .eq('is_active', True)\
            .order('sort_order')\
            .execute()
        return jsonify(response.data)
    except Exception as e:
        logger.error(f"Ошибка загрузки цен: {e}")
        return jsonify([])

# ============ ПОЛУЧЕНИЕ ДОКУМЕНТА ============
@app.route('/api/document/<doc_key>')
def get_document(doc_key):
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
        logger.error(f"Ошибка загрузки документа: {e}")
        return jsonify({'error': str(e)}), 500
    
# ============ СОЗДАНИЕ ЗАЯВКИ ============
@app.route('/create_booking', methods=['POST'])
def create_booking():
    data = request.get_json() or request.form
    
    # Валидация телефона
    phone = data.get('phone', '')
    if not is_valid_phone(phone):
        return jsonify({'success': False, 'message': 'Введите корректный номер телефона (например: +79161234567)'})
    
    # Валидация email (если указан)
    email = data.get('email', '')
    if email and not is_valid_email(email):
        return jsonify({'success': False, 'message': 'Введите корректный email адрес'})
    
    services = data.get('services', '')
    if isinstance(services, list):
        services = ', '.join(services)
    
    services_details = data.get('services_details', [])
    if isinstance(services_details, list):
        services_details_json = json.dumps(services_details, ensure_ascii=False)
    else:
        services_details_json = services_details
    
    booking_data = {
        'user_id': session.get('user_id'),
        'customer_name': data.get('name', '').strip(),
        'phone': format_phone(phone),
        'email': email.strip().lower() if email else '',
        'service': services,
        'services_details': services_details_json,
        'total_price': data.get('total_price', 0),
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
        logger.error(f"Ошибка создания заявки: {e}")
        return jsonify({'success': False, 'message': f'Ошибка сервера: {str(e)}'})

# ============ ОТПРАВКА СООБЩЕНИЯ ============
@app.route('/send_contact', methods=['POST'])
def send_contact():
    data = request.get_json() or request.form
    
    # Валидация телефона
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
        logger.error(f"Ошибка отправки контакта: {e}")
        return jsonify({'success': False, 'message': str(e)})

# ============ ЛИЧНЫЙ КАБИНЕТ ============
@app.route('/profile')
def profile():
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
        logger.error(f"Ошибка загрузки профиля: {e}")
        flash('Ошибка загрузки профиля', 'error')
        return redirect(url_for('index'))

# ============ НАЧИСЛЕНИЕ БОНУСОВ ============
@app.route('/admin/add_bonus/<int:booking_id>', methods=['POST'])
def add_bonus(booking_id):
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
        logger.error(f"Ошибка начисления бонусов: {e}")
        return jsonify({'error': str(e)}), 500

# ============ АДМИН ПАНЕЛЬ ============
@app.route('/admin')
def admin_dashboard():
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    for attempt in range(3):
        try:
            logger.info(f"Попытка {attempt+1} загрузки админ-панели")
            
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
            
            logger.info("Данные успешно загружены")
            return render_template('admin.html', 
                                 bookings=bookings.data,
                                 contacts=contacts.data,
                                 news=news.data,
                                 users=users.data)
                                 
        except Exception as e:
            logger.error(f"Ошибка при попытке {attempt+1}: {e}")
            if attempt == 2:
                flash('Ошибка загрузки данных админ-панели. Попробуйте позже.', 'error')
                return render_template('admin.html', 
                                     bookings=[],
                                     contacts=[],
                                     news=[],
                                     users=[])
            import time
            time.sleep(2)

# ============ АДМИН: ОБНОВЛЕНИЕ СТАТУСА ЗАЯВКИ ============
@app.route('/admin/update_booking/<int:booking_id>', methods=['POST'])
def update_booking(booking_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    status = request.json.get('status')
    response = supabase.table('bookings')\
        .update({'status': status, 'updated_at': datetime.now().isoformat()})\
        .eq('id', booking_id)\
        .execute()
    
    return jsonify({'success': True})

# ============ АДМИН: ДОБАВЛЕНИЕ НОВОСТИ ============
@app.route('/admin/add_news', methods=['POST'])
def add_news():
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    news_data = {
        'title': request.form['title'],
        'content': request.form['content'],
        'excerpt': request.form['excerpt'][:200] if request.form.get('excerpt') else request.form['title'][:100],
        'image_emoji': request.form.get('image_emoji', '📢'),
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

# ============ АДМИН: УДАЛЕНИЕ НОВОСТИ ============
@app.route('/admin/delete_news/<int:news_id>')
def delete_news(news_id):
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    supabase.table('news').delete().eq('id', news_id).execute()
    flash('Новость удалена', 'success')
    return redirect(url_for('admin_dashboard'))

# ============ АДМИН: УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ============
@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    supabase.table('users').delete().eq('id', user_id).execute()
    flash('Пользователь удален', 'success')
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(debug=True)