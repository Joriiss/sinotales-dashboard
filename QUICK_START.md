# Quick Start Guide

## 1. Create and Activate Virtual Environment (Recommended)

```bash
# Windows PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

## 2. Set Up PostgreSQL Database

### Option A: Using psql

```sql
CREATE DATABASE china_blog;
```

### Option B: Using pgAdmin

Create a new database named `china_blog` through the GUI.

## 3. Configure Database Connection

Create a `.env` file (optional) or set environment variables:

```bash
# Windows PowerShell
$env:DB_NAME="china_blog"
$env:DB_USER="postgres"
$env:DB_PASSWORD="your_password"
```

Or edit `config/settings.py` directly if you prefer.

## 4. Initialize Database

```bash
python manage.py makemigrations
python manage.py migrate
```

## 5. Import Your Existing Channels

```bash
python manage.py import_channels ../china-blog-data/videos/channels.csv
```

This will import all 3 channels from your CSV file.

## 6. Start the Server

```bash
python manage.py runserver
```

## 7. Access the Dashboard

- **Main Dashboard**: http://127.0.0.1:8000/
- **Admin Panel**: http://127.0.0.1:8000/admin/ (create superuser first: `python manage.py createsuperuser`)

## What You'll See

The dashboard has a sidebar with:
- **Sources** (active) - View and manage your YouTube channels
- **Contents** (coming soon)
- **Post Ideas** (coming soon)
- **Blog Posts** (coming soon)
- **Admin** - Django admin interface

## Adding a New Source

1. Click "Add New Source" button
2. Fill in:
   - Name: e.g., "My Travel Channel"
   - Source Type: YouTube Channel
   - Link: Full YouTube URL
   - Language: English, French, etc.
   - Channel ID: (optional) YouTube channel ID
   - Include Shorts: Check if you want Shorts included
3. Click "Save Source"

## Troubleshooting

### Database Connection Error

Make sure PostgreSQL is running and the database exists:
```bash
# Check if PostgreSQL is running (Windows)
Get-Service postgresql*
```

### Import Command Fails

Make sure the CSV file path is correct. Use absolute path if relative path doesn't work:
```bash
python manage.py import_channels "C:\Users\Joris\Desktop\china-blog\china-blog-data\videos\channels.csv"
```

