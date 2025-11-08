web: hypercorn main:app --bind "[::]:$PORT"
worker: celery -A celery_app worker --loglevel=info
