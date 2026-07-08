# Link Forge

A Telegram file uploader bot that:
- accepts documents, photos, videos, audio, voice notes, animations, video notes, stickers, and direct URLs
- uploads files to Telegram or hosts them on a local Flask server
- generates download links with QR codes
- supports password-protected links
- tracks downloads and basic file statistics
- auto-expires hosted files after a retention period

## Features

- Telegram media upload handling
- URL fetching and re-upload / hosting
- Batch processing for multiple media items
- QR code generation for download links
- Password protection for hosted downloads
- Admin commands for user management and stats
- Local Flask download server
- SQLite persistence
- Automatic cleanup of expired files
- Per-user storage quota enforcement (with admin overrides)

## Requirements

- Python 3.11+
- A Telegram bot token
- Telegram API ID and API hash
- Docker and Docker Compose, if using container deployment

## Environment Variables

Create a `.env` file in the project root:

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
BASE_URL=https://your-public-domain-or-ip
ADMIN_IDS=your_telegram_user_id,other_admin_id
PROCESS_MEDIA_TIME=3.0 # Time in seconds to wait before processing media batch
RETENTION_HOURS=24 # Time in hours to retain files before deletion
USER_STORAGE_LIMIT_MB=2048 # Per-user storage quota in MB (0 = unlimited)
```

### Variable Notes

- `API_ID`, `API_HASH`, `BOT_TOKEN`: required for the Telegram bot
- `BASE_URL`: the public URL used in generated download links and stats pages
- `ADMIN_IDS`: comma-separated Telegram user IDs allowed to use admin commands
- `PROCESS_MEDIA_TIME`: delay before batching media into one upload
- `RETENTION_HOURS`: how long hosted files remain available
- `USER_STORAGE_LIMIT_MB`: per-user storage limit in MB (defaults to 2048, set to 0 for unlimited)

## Local Deployment

1. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your `.env` file with the required variables.

4. Run the bot:

```bash
python run.py
```

The Flask download server listens on port `5000` inside the app.

## Docker Deployment

This project includes a Dockerfile and docker-compose.yml for containerized deployment.

### Build and run

```bash
docker compose up -d --build
```

### What it does

- builds the image from the local source
- mounts `./data` into `/app/data` so SQLite and downloaded files persist
- reads environment variables from `.env`
- exposes the Flask server on `127.0.0.1:20124`

### Port mapping

The compose file maps:

- host: `127.0.0.1:20124`
- container: `5000`

If you want public access, change the bind address in `docker-compose.yml` carefully and place the app behind a reverse proxy.

## Data Storage

The bot stores runtime data in:

- `data/database.db` for SQLite
- `data/downloads/` for downloaded or processed files
- `data/file_to_link_bot.session` for the Pyrogram session

These are created automatically on startup.

## Web Server Configs

**Nginx**

```nginx
server {
    listen 80;
    server_name files.example.com;

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name files.example.com;

    ssl_certificate     /etc/letsencrypt/live/files.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/files.example.com/privkey.pem;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:20124;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }
}
```

**Caddy**

```caddy
your-public-domain.com {
    encode gzip
    reverse_proxy 127.0.0.1:20124
}
```

## How It Works

1. Users send media or a direct URL.
2. The bot downloads the content.
3. If needed, multiple items are bundled into a ZIP archive.
4. The file is stored locally and registered in SQLite.
5. A download link and QR code are returned to the user.
6. The Flask app serves the file and stats page.
7. A background cleanup thread removes expired records and files.

## Admin Commands

Available to user IDs listed in `ADMIN_IDS`:

- `/admin_stats` - view user/file/download metrics
- `/admin_search <query>` - search for users by username or display name and open their interactive management panel
- `<user_id>` (direct numeric text) - admins sending a numeric user ID to the bot will instantly open that user's management panel
- `/admin_ban <user_id>` - ban a user
- `/admin_delete <uuid>` - delete a hosted file record
- `/admin_broadcast <text>` - send a message to all tracked users
- `/admin_setquota <user_id> <limit_in_MB>` - set custom storage limit for a user (0 for unlimited)

### Interactive Admin Panel Features

When managing a user via `/admin_search` or direct user ID lookup, the bot provides an interactive dashboard with the following features:
- **Ban/Unban Control**: Toggle a user's access restrict status directly.
- **Quota Overrides**: Adjust storage quotas dynamically using interactive buttons (`➕ 1 GB`, `➖ 1 GB`, `♾️ Unlimited` / `⚙️ Set Limit`). Going to Unlimited can be reversed or managed from any state.
- **Active Links Viewer**: Inspect all active files hosted by the user in an inline keyboard list, featuring details like download counts, expiration timers, and a `🗑️` button next to each link for instant live deletion.
- **Bulk Delete**: Clear all active links generated by a user with a double-confirmation prompt.

## User Commands

- `/start` - show the welcome message
- `/mylinks` - list your active links
- `/clone <uuid>` - duplicate an existing link
- `/removepwd <uuid>` - remove password protection from your own link

## Deployment Checklist

Before going live, make sure:

- `BASE_URL` points to a reachable public domain or IP
- your bot token and Telegram API credentials are valid
- port `5000` is proxied or exposed correctly
- the `data/` directory is writable
- the system clock is correct, since expiration uses timestamps
- `ADMIN_IDS` contains your Telegram numeric user ID

## Notes

- Expired files are cleaned up by a background thread every hour.
- Password-protected files require the correct password before download.
- The download stats page is available at `/stats/<file_uuid>`.
- The download endpoint is available at `/download/<file_uuid>`.