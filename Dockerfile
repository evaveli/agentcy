FROM alpine

WORKDIR /app
# Install necessary system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libc-dev \
    && apt-get clean

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 80
EXPOSE 5678

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "80", "--reload", "--log-level", "info", "--reload-dir", "/app"]
