FROM python:3.11-slim

WORKDIR /app

# System deps for pillow/reportlab
RUN apt-get update && apt-get install -y build-essential libjpeg-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Create data directory for sqlite mount
RUN mkdir -p /data

ENV DB_PATH=/data/diary.db
CMD ["python", "diabetes_diary_bot.py"]
