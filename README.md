# China Blog Dashboard

Django-based dashboard for managing content sources and generating blog posts about China travel.

## Features

- **Source Management**: Add and manage YouTube channels, blogs, ebooks, and RSS feeds
- **PostgreSQL Database**: Robust data storage with proper indexing
- **Admin Interface**: Django admin for advanced management
- **CSV Import**: Import channels, blogs, posts, and ebooks from CSV files
- **Authentication**: Secure login system to protect the dashboard
- **Content Translation**: Automatic translation of French content to English for ebooks
- **Flexible Content Types**: Support for videos, blog posts, and ebooks with optional links

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

### 5. Import Content from CSV Files

#### Import YouTube Channels

```bash
python manage.py import_channels channels.csv
```

Or with skip-existing flag to avoid duplicates:

```bash
python manage.py import_channels channels.csv --skip-existing
```

#### Import Blogs

```bash
python manage.py import_blogs blogs.csv
```

#### Import Blog Posts

```bash
python manage.py import_posts posts.csv --load-content
```

The `--load-content` flag will load content from files in the `content/` directory.

#### Import Ebooks

```bash
python manage.py import_ebooks ebooks.csv --load-content
```

**Ebook Import Features:**
- Automatically translates French content to English (requires `deep-translator` package)
- Can scan directory: `python manage.py import_ebooks --scan-dir`
- Loads content from TXT files in `ebooks/txt/` directory
- Supports optional links (ebooks don't require URLs)

**Translation Options:**
- `--no-translate`: Skip translation even for French content
- Translation is enabled by default for French ebooks

### 6. Run Development Server

```bash
python manage.py runserver
```

Then visit:
- Dashboard: http://127.0.0.1:8000/ (requires login)
- Login: http://127.0.0.1:8000/login/
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
│           ├── import_channels.py  # Import YouTube channels from CSV
│           ├── import_blogs.py     # Import blogs from CSV
│           ├── import_posts.py    # Import blog posts from CSV
│           ├── import_ebooks.py   # Import ebooks from CSV (with translation)
│           └── import_videos.py   # Import videos from CSV
├── templates/             # HTML templates
│   ├── base.html          # Base template with sidebar
│   ├── registration/      # Authentication templates
│   │   └── login.html     # Login page
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

1. Navigate to http://127.0.0.1:8000/ (login required)
2. Click "Add New Source"
3. Fill in the form:
   - Name: Channel or source name
   - Source Type: Select YouTube, Blog, Ebook, or RSS
   - Link: Full URL to the source (optional for ebooks)
   - Language: Primary language of content
   - Channel ID: (Required for YouTube) YouTube channel ID
   - Include Shorts: (YouTube only) Whether to include Shorts
   - Active: Whether source is currently monitored

**Note:** The `link` field is optional for ebook sources since travel books typically don't have URLs.

### Using Django Admin

1. Go to http://127.0.0.1:8000/admin/
2. Login with superuser credentials
3. Navigate to "Sources" section
4. Add, edit, or delete sources

## Authentication

The dashboard is protected by authentication. You need to log in to access any pages.

### Creating a Superuser

```bash
python manage.py createsuperuser
```

After creating a superuser, you can:
- Log in to the dashboard at `/login/`
- Access the Django admin at `/admin/`

## Environment Variables

You can configure the database using environment variables. Create a `.env` file in the project root:

```env
DB_NAME=china_blog
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
DJANGO_SECRET_KEY=your-secret-key-here
```

Variables:
- `DB_NAME`: Database name (default: 'china_blog')
- `DB_USER`: Database user (default: 'postgres')
- `DB_PASSWORD`: Database password (default: '')
- `DB_HOST`: Database host (default: 'localhost')
- `DB_PORT`: Database port (default: '5432')
- `DJANGO_SECRET_KEY`: Django secret key (default: insecure key for dev only)

## CSV File Formats

### Blogs CSV (`blogs.csv`)
```csv
name,url,language,rss_feed,sitemaps,filter_china,blog_only
Wild China,https://wildchina.com/,English,https://wildchina.com/feed/,https://wildchina.com/sitemap.xml,False,True
```

### Posts CSV (`posts.csv`)
```csv
id,title,link,date,tags,source,content_file
44f55dff5f455836,The Complete Guide to Your Layover in Shanghai,https://www.chinahighlights.com/shanghai/layover-guide.htm,,,China Highlights,china-highlights_44f55dff5f455836.txt
```

### Ebooks CSV (`ebooks.csv`)
```csv
title,author,source,language,date,link,txt_file
Behind the Wall,Colin Thubron,Travel Books,en,1987,,Behind the Wall - Colin Thubron.txt
Guide Chine 2025/2026,Petit Futé,Travel Books,fr,2025,,Guide Chine 2025_2026 Petit Fut - Dominique Auzias.txt
```

**Note:** The `link` field is optional for ebooks. French ebooks (`language=fr`) will be automatically translated to English during import.

## Dependencies

Install additional dependencies:

```bash
pip install -r requirements.txt
```

For translation support:
```bash
pip install deep-translator
```

For OpenAI tagging (optional):
```bash
pip install openai
```

## Automatic Content Tagging

The dashboard includes an AI-powered tagging system that can automatically tag your content using local LLMs (Ollama) or OpenAI.

### Setup Ollama (Recommended - Free & Local)

1. **Install Ollama**: Download from https://ollama.ai

2. **Pull a model** (recommended: llama3.2 for good quality/speed balance):
   ```bash
   ollama pull llama3.2
   ```

3. **Start Ollama** (usually runs automatically):
   ```bash
   ollama serve
   ```

### Tag All Existing Content

Tag all content using Ollama (default):
```bash
python manage.py auto_tag_content
```

Tag only content that has text:
```bash
python manage.py auto_tag_content --has-content-only
```

Tag specific number of items (for testing):
```bash
python manage.py auto_tag_content --limit 10
```

Skip content that already has tags:
```bash
python manage.py auto_tag_content --skip-tagged
```

Dry run (see what would be tagged without saving):
```bash
python manage.py auto_tag_content --dry-run
```

### Using OpenAI Instead

If you prefer OpenAI (requires API key):

1. Set your API key in settings or environment:
   ```python
   # settings.py or .env
   OPENAI_API_KEY = "your-api-key-here"
   ```

2. Run with OpenAI:
   ```bash
   python manage.py auto_tag_content --provider openai --model gpt-3.5-turbo
   ```

### Tagging Options

- `--provider`: `ollama` (default) or `openai`
- `--model`: Model name (e.g., `llama3.2`, `mistral`, `gpt-3.5-turbo`)
- `--limit`: Process only N items
- `--skip-tagged`: Skip content that already has tags
- `--has-content-only`: Only tag content with text
- `--source ID`: Only tag content from specific source
- `--batch-size N`: Show progress every N items (default: 10)
- `--dry-run`: Preview without saving

### Cost Comparison

- **Ollama**: Free (runs locally, no API costs)
- **OpenAI GPT-3.5-turbo**: ~$0.01-0.03 per 1000 items (very affordable)
- **OpenAI GPT-4**: ~$0.30-0.60 per 1000 items (higher quality but more expensive)

For most use cases, **Ollama with llama3.2** provides excellent quality at zero cost.

## Next Steps

- [x] Add Contents tab for managing collected content
- [x] Add authentication system
- [x] Add CSV import for blogs, posts, and ebooks
- [x] Add translation support for French content
- [ ] Add Post Ideas tab for generated article ideas
- [ ] Add Blog Posts tab for managing generated posts
- [ ] Set up embedding generation pipeline
- [ ] Integrate with vector database (Qdrant/Weaviate/Chroma)
- [ ] Add RAG pipeline for content generation


