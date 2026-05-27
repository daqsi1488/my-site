from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from supabase_client import supabase
from datetime import datetime
import hashlib
import os
import logging 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask сам найдёт папки templates и static, если они рядом
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-here")

# ============ ХЭШИРОВАНИЕ ПАРОЛЕЙ ============
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

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
        email = request.form['email']
        password = request.form['password']
        full_name = request.form['full_name']
        phone = request.form['phone']
        
        existing = supabase.table('users')\
            .select('*')\
            .eq('email', email)\
            .execute()
        
        if existing.data:
            flash('Пользователь с таким email уже существует', 'error')
            return redirect(url_for('register'))
        
        user_data = {
            'email': email,
            'password_hash': hash_password(password),
            'full_name': full_name,
            'phone': phone,
            'role': 'user'
        }
        
        response = supabase.table('users').insert(user_data).execute()
        
        if response.data:
            flash('Регистрация успешна! Теперь войдите', 'success')
            return redirect(url_for('login'))
        else:
            flash('Ошибка регистрации', 'error')
    
    return render_template('register.html')

# ============ ВХОД ============
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
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
            return redirect(url_for('index'))
        else:
            flash('Неверный email или пароль', 'error')
    
    return render_template('login.html')

# ============ ВЫХОД ============
@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))

# ============ СОЗДАНИЕ ЗАЯВКИ ============
@app.route('/create_booking', methods=['POST'])
def create_booking():
    data = request.get_json() or request.form
    
    booking_data = {
        'user_id': session.get('user_id'),
        'customer_name': data['name'],
        'phone': data['phone'],
        'service': data['service'],
        'booking_date': data['date'],
        'booking_time': data['time'],
        'status': 'new',
        'comment': data.get('comment', '')
    }
    
    response = supabase.table('bookings').insert(booking_data).execute()
    
    if response.data:
        return jsonify({'success': True, 'message': 'Заявка отправлена!'})
    return jsonify({'success': False, 'message': 'Ошибка отправки'})

# ============ ОТПРАВКА СООБЩЕНИЯ ============
@app.route('/send_contact', methods=['POST'])
def send_contact():
    data = request.get_json() or request.form
    
    contact_data = {
        'name': data['name'],
        'phone': data['phone'],
        'message': data['message']
    }
    
    response = supabase.table('contacts').insert(contact_data).execute()
    
    if response.data:
        return jsonify({'success': True, 'message': 'Сообщение отправлено!'})
    return jsonify({'success': False, 'message': 'Ошибка отправки'})

# ============ АДМИН ПАНЕЛЬ ============
@app.route('/admin')
def admin_dashboard():
    if session.get('user_role') != 'admin':
        flash('Доступ запрещен', 'error')
        return redirect(url_for('index'))
    
    # Пытаемся загрузить данные с повторными попытками
    for attempt in range(3):  # 3 попытки
        try:
            logger.info(f"Попытка {attempt+1} загрузки админ-панели")
            
            # Загружаем данные с лимитами и таймаутом
            bookings = supabase.table('bookings')\
                .select('*')\
                .order('created_at', desc=True)\
                .limit(50)\
                .execute()
            
            contacts = supabase.table('contacts')\
                .select('*')\
                .order('created_at', desc=True)\
                .limit(50)\
                .execute()
            
            news = supabase.table('news')\
                .select('*')\
                .order('date_published', desc=True)\
                .limit(50)\
                .execute()
            
            users = supabase.table('users')\
                .select('*')\
                .limit(50)\
                .execute()
            
            logger.info("Данные успешно загружены")
            return render_template('admin.html', 
                                 bookings=bookings.data,
                                 contacts=contacts.data,
                                 news=news.data,
                                 users=users.data)
                                 
        except Exception as e:
            logger.error(f"Ошибка при попытке {attempt+1}: {e}")
            if attempt == 2:  # Последняя попытка
                flash('Ошибка загрузки данных админ-панели. Попробуйте позже.', 'error')
                # Возвращаем пустые данные вместо ошибки
                return render_template('admin.html', 
                                     bookings=[],
                                     contacts=[],
                                     news=[],
                                     users=[])
            import time
            time.sleep(2)  # Ждем 2 секунды перед повторной попыткой

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
        'excerpt': request.form['excerpt'][:200],
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

# Для Render (Gunicorn использует переменную app)
if __name__ == '__main__':
    app.run(debug=True)