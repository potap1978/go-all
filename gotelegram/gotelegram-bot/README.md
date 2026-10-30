# goTelegram Pro v2.5.0 Bot

Production-quality Telegram bot for managing MTProxy (telemt engine) on Linux servers.

## Features

- **Complete CLI Feature Parity** - All menu items from CLI version
  - Install (Quick/Stealth modes)
  - Status monitoring
  - Proxy link generation
  - Share with QR codes
  - Service restart
  - Logs viewing
  - Mode/template changes
  - Backup/restore
  - telemt updates
  - Website/SSL management
  - Remove installation
  - Promotional links

- **Template Browsing** - Browse categories → templates → preview → install
- **Per-user MTProxy Keys** - Manage telemt `[access.users]` from inline bot menus
- **Per-user QR Import** - Show QR codes for every Telegram proxy key
- **Local Web Admin** - Shows SSH tunnel instructions for the 127.0.0.1:1984 dashboard
- **V1 Migration** - Detects old mtg Docker container and offers migration
- **Access Control** - ALLOWED_IDS from .env
- **Async/Await** - Full async support via python-telegram-bot v21+
- **Inline Keyboards** - Modern UI with callback-based navigation
- **Shell Integration** - Executes system commands via asyncio subprocess
- **Error Handling** - Production-ready error handling

## Installation

Recommended installation path is the main CLI menu:

```bash
gotelegram
```

Then choose `12) Telegram-bot` → install/update. On repeat goTelegram Pro bootstrap/update, an already installed bot is refreshed automatically: code, i18n files and requirements are copied to `/opt/gotelegram-bot`, `.env` is preserved, dependencies are checked and `gotelegram-bot` is restarted.

### Prerequisites

- Python 3.8+
- Linux system with systemd
- telemt installed and running
- Telegram Bot Token from @BotFather

### Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create .env file:
```bash
cp config.example.env .env
# Edit .env and set your BOT_TOKEN
nano .env
```

3. (Optional) Restrict access to specific users:
```bash
# Edit .env and uncomment ALLOWED_IDS
# ALLOWED_IDS=123456789,987654321
```

### Running the Bot

```bash
python3 bot.py
```

For systemd service:

```bash
[Unit]
Description=goTelegram Pro Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/gotelegram-bot
ExecStart=/opt/gotelegram-bot/venv/bin/python /opt/gotelegram-bot/bot.py
Restart=always
RestartSec=5
Environment=PATH=/opt/gotelegram-bot/venv/bin:/usr/bin

[Install]
WantedBy=multi-user.target
```

## Configuration

### .env Variables

- `BOT_TOKEN` - Telegram bot token (required)
- `ALLOWED_IDS` - Comma-separated user IDs (optional, all users allowed if empty)
- `BOT_LANG` - Default language inherited from goTelegram Pro install language

### System Paths

- `GOTELEGRAM_CONFIG` - `/opt/gotelegram/config.json`
- `TELEMT_CONFIG` - `/etc/telemt/config.toml`
- `TELEMT_SERVICE` - `telemt` (systemd service name)
- `WEBSITE_ROOT` - `/var/www/gotelegram-site`
- `BACKUP_DIR` - `/opt/gotelegram/backups`
- `BACKUP_SCHEDULE_FILE` - `/opt/gotelegram/backup_schedule.json`
- `TEMPLATES_CATALOG` - `/opt/gotelegram/templates_catalog.json`

## Architecture

### Single File Design
All functionality in one `bot.py` for simplicity and ease of deployment.

### Command Handlers
- `/start` - Main menu
- `/help` - Help text
- `/status` - Quick status
- `/logs` - Recent logs

### Callback Handlers
Organized by feature:
- Installation (quick/stealth modes)
- Status monitoring
- Backup/restore
- Backup schedules: off, daily, weekly, monthly
- SSL management
- Updates
- Removal

### Shell Integration
Async subprocess wrapper:
```python
code, stdout, stderr = await sh("command", "arg1", "arg2")
```

## Callback Data Convention

- `menu_*` - Menu items
- `install_mode_*` - Install options
- `quick_dom_*` - Domain selection
- `stealth_cat_*` - Template categories
- `stealth_tpl_*` - Template selection
- `stealth_confirm_*` - Confirm installation
- `backup_*` - Backup operations
- `ssl_*` - SSL operations
- `restore_backup_*` - Restore operations

## Credits

- **telemt** - MTProxy engine foundation
- **HTML5UP** - Beautiful web templates
- **Learning Zone** - Educational resources
- **Start Bootstrap** - Bootstrap framework
- **Community** - Your feedback and support

## License

goTelegram Pro v2.5.0 - Open source community project
