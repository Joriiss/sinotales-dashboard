# China Blog Dashboard

Django-based dashboard for managing content sources and generating blog posts about China travel.

## Features

- **Source Management**: Add and manage YouTube channels, blogs, ebooks, and RSS feeds
- **PostgreSQL Database**: Robust data storage with proper indexing
- **Admin Interface**: Django admin for advanced management
- **CSV Import**: Import existing channels from CSV files

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure PostgreSQL Database

Create a PostgreSQL database:

```sql
CREATE DATABASE china_blog;
```

Or set environment variables for database connection:

```bash
DB_NAME="china_blog"
DB_USER="postgres"
DB_PASSWORD=""pwd
DB_HOST="localhost"
DB_PORT="5432"
```

### 3. Run Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### 4. Create Superuser (Optional, for Admin)

```bash
python manage.py createsuperuser
```

### 5. Import Existing Channels from CSV

Import channels from your existing CSV file:

```bash
python manage.py import_channels ../china-blog-data/videos/channels.csv
```

Or with skip-existing flag to avoid duplicates:

```bash
python manage.py import_channels ../china-blog-data/videos/channels.csv --skip-existing
```

### 6. Run Development Server

```bash
python manage.py runserver
```

Then visit:
- Dashboard: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/

## Project Structure

```
china-blog-dashboard/
├── config/                 # Django project settings
│   ├── settings.py        # Database and app configuration
│   ├── urls.py            # Main URL routing
│   └── ...
├── sources/                # Sources app
│   ├── models.py          # Source model (YouTube channels, blogs, etc.)
│   ├── views.py           # View functions
│   ├── forms.py           # Form definitions
│   ├── admin.py           # Django admin configuration
│   ├── urls.py            # App URL routing
│   └── management/
│       └── commands/
│           └── import_channels.py  # CSV import command
├── templates/             # HTML templates
│   ├── base.html          # Base template with sidebar
│   └── sources/           # Source management templates
├── manage.py              # Django management script
└── requirements.txt       # Python dependencies
```

## Database Table Structure

See `SQL_TABLE_STRUCTURE.md` for the complete PostgreSQL table schema.

The main table is `sources` which stores:
- Basic info: name, type, link, language
- YouTube-specific: channel_id, include_shorts
- Status: is_active, last_collected
- Metadata: JSONB field for additional data

## Usage

### Adding a Source via Web Interface

1. Navigate to http://127.0.0.1:8000/
2. Click "Add New Source"
3. Fill in the form:
   - Name: Channel or source name
   - Source Type: Select YouTube, Blog, Ebook, or RSS
   - Link: Full URL to the source
   - Language: Primary language of content
   - Channel ID: (Optional) YouTube channel ID
   - Include Shorts: (YouTube only) Whether to include Shorts
   - Active: Whether source is currently monitored

### Using Django Admin

1. Go to http://127.0.0.1:8000/admin/
2. Login with superuser credentials
3. Navigate to "Sources" section
4. Add, edit, or delete sources

## Environment Variables

You can configure the database using environment variables:

- `DB_NAME`: Database name (default: 'china_blog')
- `DB_USER`: Database user (default: 'postgres')
- `DB_PASSWORD`: Database password (default: '')
- `DB_HOST`: Database host (default: 'localhost')
- `DB_PORT`: Database port (default: '5432')
- `DJANGO_SECRET_KEY`: Django secret key (default: insecure key for dev only)

## Next Steps

- [ ] Add Contents tab for managing collected content
- [ ] Add Post Ideas tab for generated article ideas
- [ ] Add Blog Posts tab for managing generated posts
- [ ] Set up embedding generation pipeline
- [ ] Integrate with vector database (Qdrant/Weaviate/Chroma)
- [ ] Add RAG pipeline for content generation


