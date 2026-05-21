# Floor555

Personal web app for downloading YouTube videos and Spotify tracks.
**Not intended for public deployment.** Run behind localhost, VPN, or HTTP Basic Auth.

---

## Stack

- **Backend**: FastAPI + uvicorn (Python 3.10+)
- **Downloaders**: yt-dlp (YouTube), spotdl (Spotify)
- **Frontend**: Vanilla HTML/CSS/JS, no build step
- **Streaming**: Server-Sent Events for live progress

---

## Quick start (local)

```bash
cd floor555
bash install.sh
source .venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000

---

## Deploy on Ubuntu (systemd)

```bash
sudo cp -r . /opt/floor555
sudo chown -R www-data:www-data /opt/floor555
cd /opt/floor555 && bash install.sh

sudo cp media-fetcher.service /etc/systemd/system/floor555.service
sudo systemctl daemon-reload
sudo systemctl enable --now floor555

sudo journalctl -u floor555 -f
```

---

## Nginx + TLS

```bash
sudo certbot certonly --nginx -d YOUR_DOMAIN

sudo cp nginx.conf /etc/nginx/sites-available/floor555
sudo ln -s /etc/nginx/sites-available/floor555 /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Optional HTTP Basic Auth:
```bash
sudo apt install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd youruser
# Uncomment auth_basic lines in nginx.conf
```

---

## Logo

Replace the placeholder logo in `static/index.html`:
- Drop your logo file into `static/` (e.g. `static/logo.png`)
- In `index.html`, find the `.logo` div and replace it with:
  ```html
  <img src="/static/logo.png" alt="Floor555" class="logo" style="background:none;" />
  ```

## Artist image

Replace the XXXTentacion placeholder:
- Drop the image into `static/` (e.g. `static/xxxtentacion.jpg`)
- In `index.html`, find `.artist-img-placeholder` and replace it with:
  ```html
  <img src="/static/xxxtentacion.jpg" alt="XXXTentacion" class="artist-img" />
  ```

---

## Spotify setup

```bash
source .venv/bin/activate
spotdl --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET save
```

Get credentials at https://developer.spotify.com/dashboard

---

## Features

- **Preview before download**: Both YouTube and Spotify support previewing the full track list
- **Selective download**: Uncheck any tracks you don't want — only selected items download
- **Live console**: Real-time progress via Server-Sent Events
- **Cancel anytime**: Terminates the full download process tree

---

## Security

- URL allowlisting (YouTube + open.spotify.com only)
- Path traversal prevention on output folders
- Subprocesses use argument lists, never `shell=True`
- CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy headers
- systemd unit: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

---

## Updating yt-dlp

```bash
source .venv/bin/activate
pip install -U yt-dlp
sudo systemctl restart floor555
```
