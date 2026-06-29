FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask flask-cors python-dotenv google-generativeai
COPY . .
EXPOSE 8005
CMD ["python", "app.py"]
