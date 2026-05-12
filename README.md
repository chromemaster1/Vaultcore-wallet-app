# VaultCore Wallet — Deployment Guide

This repository is a Flask app. The live site must run a WSGI process (not served as static files). This README contains quick instructions for local testing, Heroku-style deployment (Procfile), and a production example using `gunicorn` + `systemd` + `nginx`.

## Quick local run (development)

1. Create and activate a virtualenv:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
pip install gunicorn
```

3. Copy or create a `.env` with required environment variables (see below). Then run for development:

```bash
# development (flask built-in server)
python wsgi.py
```

or run with gunicorn locally:

```bash
gunicorn -w 4 -b 0.0.0.0:8000 wsgi:app
```

## Procfile (Heroku / similar)

This repository includes a `Procfile` with the following entry:

- `web: gunicorn -w 4 -b 0.0.0.0:$PORT wsgi:app`

Push to a platform that reads `Procfile` (Heroku, Render with `Dockerfile` disabled, etc.). Ensure `requirements.txt` lists `gunicorn` or add it at deploy time.

## Production example (gunicorn + systemd + nginx)

Example `systemd` unit (place in `/etc/systemd/system/vaultcore.service`):

```
[Unit]
Description=VaultCore Wallet
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/path/to/your/app
Environment=PATH=/path/to/your/app/.venv/bin
Environment=FLASK_ENV=production
EnvironmentFile=/path/to/your/app/.env
ExecStart=/path/to/your/app/.venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 wsgi:app

[Install]
WantedBy=multi-user.target
```

Basic `nginx` site config to reverse-proxy (example):

```
server {
    listen 80;
    server_name your.domain.com;

    location /static/ {
        alias /path/to/your/app/static/;
        expires 30d;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

After creating the `systemd` unit:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vaultcore.service
```

## Required environment variables

Set these in `.env` or your host environment (minimum):

- `FLASK_SECRET_KEY` — Flask session secret.
- `ENCRYPTION_KEY` — app encryption key if used.
- `CMC_PRO_API_KEY` — (optional) CoinMarketCap API key for market data.
- `NEWS_API_KEY` — (optional) NewsAPI key for crypto news.
- `COMING_SOON` — `true` or `false` to enable coming-soon by env (file flag takes priority).

## Coming Soon toggle

- Admin endpoints added:
  - `POST /admin/coming-soon/toggle` — toggles the `instance/COMING_SOON` flag file.
  - `GET  /admin/coming-soon/state` — returns current state as JSON.

- The presence of the file `instance/COMING_SOON` forces the site into coming-soon mode. The admin toggle will create/remove that file. Ensure the runtime user can write to the `instance/` directory.

## Troubleshooting

- If you see raw template code (e.g. `{{ url_for('send') }}`) in the browser the server is serving files statically — ensure a WSGI process is running (gunicorn/uWSGI) and the host points HTTP traffic to it.
- Check logs from `gunicorn` or `systemd` (`journalctl -u vaultcore.service`) for startup errors (missing env vars, DB errors).
- Permissions: the app must be able to create `instance/COMING_SOON` for the toggle to work.

## Notes

- Do not upload the `templates/` directory to a static-only host — templates must be rendered server-side by Flask.
- Consider using a process manager (systemd, supervisor) and serving static assets with Nginx/CDN in production.
