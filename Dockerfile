FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py store.py ./
COPY templates templates
ENV PORT=8000 DB_PATH=/data/event.db
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "waitress-serve --host=0.0.0.0 --port=${PORT} app:app"]
