from supabase import create_client, Client
import httpx

# ВСТАВЬТЕ ВАШИ ДАННЫЕ ПРЯМО СЮДА
SUPABASE_URL = "https://mcxwvxhifwzzlznnvbze.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1jeHd2eGhpZnd6emx6bm52YnplIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk3NzMxMTIsImV4cCI6MjA5NTM0OTExMn0.HU_0yuDW5nS9eBdVG7aoUzOrZp_szHF65FrW0fgqDHo"

# Создаем клиент напрямую через httpx
http_client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))

# Создаем клиент Supabase
supabase: Client = create_client(
    SUPABASE_URL, 
    SUPABASE_KEY
)