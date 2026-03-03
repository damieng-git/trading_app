FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8050

ENV TD_HOST=0.0.0.0
ENV TD_PORT=8050

CMD ["python", "-m", "apps.dashboard.serve_dashboard"]
