# Trading Dashboard — Deployment Guide

This document covers deployment options for the Trading Dashboard, from local development to production behind nginx.

## Prerequisites

- **Python 3.11+** — Required for type hints and modern features
- **pip** — Package installer
- **Node.js** — Not required; the dashboard is server-rendered with embedded Plotly.js

### Dependencies

All Python dependencies are listed in `requirements.txt`:

- `pandas`, `numpy` — Data processing
- `plotly` — Chart rendering
- `yfinance` — Market data
- `pyarrow` — Parquet storage
- `numba` — Performance acceleration

## Local Development Setup

1. **Clone and install**

   ```bash
   cd trading_app
   pip install -e .
   ```

2. **Build the dashboard data** (first run or after config changes)

   ```bash
   python -m apps.dashboard.build_dashboard --mode all
   ```

3. **Start the server**

   ```bash
   python -m apps.dashboard.serve_dashboard
   ```

4. **Open** `http://localhost:8050` (or `http://127.0.0.1:8050`)

### Environment variables for local dev

| Variable   | Default      | Description                    |
|-----------|--------------|--------------------------------|
| `TD_HOST` | `127.0.0.1` | Bind address                   |
| `TD_PORT` | `8050`      | Server port                    |
| `AUTH_USER` | (none)    | Optional Basic Auth username   |
| `AUTH_PASS` | (none)    | Optional Basic Auth password  |

## Docker Deployment

A `Dockerfile` is provided for containerized deployment.

### Build

```bash
docker build -t trading-dashboard .
```

### Run

```bash
docker run -p 8050:8050 -v $(pwd)/data:/app/data trading-dashboard
```

Mount the `data` directory to persist dashboard artifacts, symbol lists, and enriched stock data.

### Docker Compose example

```yaml
services:
  dashboard:
    build: .
    ports:
      - "8050:8050"
    volumes:
      - ./data:/app/data
    environment:
      - TD_HOST=0.0.0.0
      - TD_PORT=8050
    restart: unless-stopped
```

## Environment Variables Reference

| Variable            | Default       | Description                                |
|---------------------|---------------|--------------------------------------------|
| `TD_HOST`           | `127.0.0.1`   | HTTP server bind address                   |
| `TD_PORT`           | `8050`        | HTTP server port                           |
| `AUTH_USER`         | (empty)       | Basic Auth username (omit to disable)      |
| `AUTH_PASS`         | (empty)       | Basic Auth password                        |
| `CORS_ORIGIN`       | `*`           | Access-Control-Allow-Origin header value   |
| `TELEGRAM_BOT_TOKEN`| (from config) | Overrides `alerts_config.json` for Telegram|
| `TELEGRAM_CHAT_ID`  | (from config) | Overrides `alerts_config.json` for Telegram|
| `SMTP_HOST`         | (from config) | Overrides email SMTP host                  |
| `SMTP_PORT`         | (from config) | Overrides email SMTP port                  |
| `SMTP_USER`         | (from config) | Overrides email username                   |
| `SMTP_PASS`         | (from config) | Overrides email password                   |

## Running Behind nginx

Use nginx as a reverse proxy for SSL termination, load balancing, or subpath routing.

### Basic reverse proxy

```nginx
server {
    listen 443 ssl;
    server_name dashboard.example.com;

    ssl_certificate     /etc/ssl/certs/dashboard.crt;
    ssl_certificate_key /etc/ssl/private/dashboard.key;

    location / {
        proxy_pass http://127.0.0.1:8050;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }
}
```

### SSE (Server-Sent Events) for scan/refresh streams

The dashboard uses SSE for long-running scan and refresh operations. Ensure nginx does not buffer:

```nginx
location /api/scan {
    proxy_pass http://127.0.0.1:8050;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
}

location /api/refresh {
    proxy_pass http://127.0.0.1:8050;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
}
```

## Monitoring and Health Checks

### Health endpoint

- **GET** `/health` — Returns `{"ok": true}` when the server is running.

### Task status

- **GET** `/api/scan/status` — JSON with `scan_running`, `refresh_running`, `enrich_running`.

### Logging

The server uses **structured JSON logging** when started via `main()`. Each log line is a JSON object:

```json
{"ts": "2025-03-03 12:00:00", "level": "INFO", "logger": "apps.dashboard.serve_dashboard", "msg": "Dashboard: http://0.0.0.0:8050"}
```

Parse with tools like `jq` for monitoring:

```bash
python -m apps.dashboard.serve_dashboard 2>&1 | jq -c .
```

## Data Management and Backup

### Directory layout

| Path                                    | Contents                               |
|----------------------------------------|----------------------------------------|
| `data/feature_store/enriched/<dataset>/stock_data/` | Parquet files per symbol/timeframe |
| `data/dashboard_artifacts/`            | Dashboard shell HTML, assets, screener JSON |
| `apps/dashboard/configs/lists/`        | Group CSVs (watchlist, entry_stocks, etc.) |

### Backup recommendations

1. **Symbol lists** — Back up `apps/dashboard/configs/lists/*.csv`.
2. **Enriched data** — Optionally back up `data/feature_store/enriched/` (large).
3. **Dashboard artifacts** — Can be regenerated with `build_dashboard --mode all`.

### Rebuild after data loss

```bash
# Full rebuild (download + enrich + dashboard)
python -m apps.dashboard.build_dashboard --mode all

# Refresh from cached downloads
python -m apps.dashboard.build_dashboard --mode re_enrich
```
