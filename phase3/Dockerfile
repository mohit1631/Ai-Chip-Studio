FROM python:3.11-slim
RUN apt-get update && apt-get install -y yosys && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt google-generativeai python-dotenv flask-cors
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
