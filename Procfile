bot: python run_bot.py
web: gunicorn wsgi:app --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4
