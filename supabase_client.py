import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# Теперь берем данные из переменных окружения
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Проверяем, что данные загрузились
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL или SUPABASE_KEY не найдены в .env файле!")

# Создаем клиент Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)