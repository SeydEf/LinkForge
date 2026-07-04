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

## Requirements

- Python 3.11+
- A Telegram bot token
- Telegram API ID and API hash
- Docker and Docker Compose, if using container deployment

## Environment Variables

Create a `.env` file in the project root:

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:your_bot_token
BASE_URL=https://your-public-domain-or-ip
ADMIN_IDS=123456789,987654321
PROCESS_MEDIA_TIME=3.0
RETENTION_HOURS=24
```

### Variable Notes

- `API_ID`, `API_HASH`, `BOT_TOKEN`: required for the Telegram bot
- `BASE_URL`: the public URL used in generated download links and stats pages
- `ADMIN_IDS`: comma-separated Telegram user IDs allowed to use admin commands
- `PROCESS_MEDIA_TIME`: delay before batching media into one upload
- `RETENTION_HOURS`: how long hosted files remain available

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
python bot.py
```

The Flask download server listens on port `5000` inside the app.

## Docker Deployment

This project includes a [Dockerfile](/home/seydef/Project/Python/uploader_bot_v2/Dockerfile) and [docker-compose.yml](/home/seydef/Project/Python/uploader_bot_v2/docker-compose.yml) for containerized deployment.

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
- `/admin_ban <user_id>` - ban a user
- `/admin_delete <uuid>` - delete a hosted file record
- `/admin_broadcast <text>` - send a message to all tracked users

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