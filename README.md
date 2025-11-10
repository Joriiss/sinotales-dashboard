# China Blog Dashboard

Django-based dashboard for managing content sources and generating blog posts about China travel.

## Features

- **Source Management**: Add and manage YouTube channels, blogs, ebooks, and RSS feeds
- **PostgreSQL Database**: Robust data storage with proper indexing and pgvector for semantic search
- **Admin Interface**: Django admin for advanced management
- **CSV Import**: Import channels, blogs, posts, and ebooks from CSV files
- **Authentication**: Secure login system to protect the dashboard
- **Content Translation**: Automatic translation of French content to English for ebooks
- **Flexible Content Types**: Support for videos, blog posts, and ebooks with optional links
- **AI-Powered Tagging**: Automatic content tagging using local LLMs (Ollama) or OpenAI
- **Vector Embeddings**: Generate embeddings for semantic search using OpenAI's text-embedding-3-small
- **Content Chunking**: Automatic chunking of long content for efficient embedding and retrieval

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure PostgreSQL Database

Create a PostgreSQL database with the `pgvector` extension:

```sql
CREATE DATABASE china_blog;
\c china_blog
CREATE EXTENSION IF NOT EXISTS vector;
```

**Note**: The `pgvector` extension is required for vector embeddings. Make sure your PostgreSQL version supports it (PostgreSQL 11+).

Or set environment variables for database connection:

```bash
DB_NAME="china_blog"
DB_USER="postgres"
DB_PASSWORD="pwd"
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
│   ├── models.py          # Source, Content, Tag, ContentChunk models
│   ├── views.py           # View functions
│   ├── forms.py           # Form definitions
│   ├── admin.py           # Django admin configuration
│   ├── urls.py            # App URL routing
│   ├── services.py        # TaggingService for AI-powered tagging
│   ├── embedding_service.py  # EmbeddingService for vector embeddings
│   └── management/
│       └── commands/
│           ├── import_channels.py  # Import YouTube channels from CSV
│           ├── import_blogs.py     # Import blogs from CSV
│           ├── import_posts.py    # Import blog posts from CSV
│           ├── import_ebooks.py   # Import ebooks from CSV (with translation)
│           ├── import_videos.py   # Import videos from CSV
│           ├── auto_tag_content.py  # Automatic content tagging
│           └── generate_embeddings.py  # Generate vector embeddings
├── templates/             # HTML templates
│   ├── base.html          # Base template with sidebar
│   ├── registration/      # Authentication templates
│   │   └── login.html     # Login page
│   └── sources/           # Source management templates
├── manage.py              # Django management script
└── requirements.txt       # Python dependencies
```

## Database Table Structure

See `DATABASE_DIAGRAM.md` for the complete database schema diagram.

The main tables are:

**sources** - Content sources (YouTube channels, blogs, ebooks, RSS):
- Basic info: name, type, link, language
- YouTube-specific: channel_id, include_shorts
- Status: is_active, last_collected
- Metadata: JSONB field for additional data

**contents** - Individual content items (videos, blog posts, ebooks):
- Source reference, title, link, content text
- Content type, publication date
- Status flags: has_content, processed

**tags** - Content tags for categorization:
- Tag name, slug, optional description
- Many-to-many relationship with contents

**content_chunks** - Chunked content with vector embeddings:
- Content reference, chunk index, text
- Vector embedding (1536 dimensions) for semantic search
- HNSW index for fast similarity queries

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
- `OPENAI_API_KEY`: OpenAI API key (required for embeddings)
- `OPENAI_EMBEDDING_MODEL`: Embedding model name (default: 'text-embedding-3-small')
- `OPENAI_EMBEDDING_DIMENSIONS`: Embedding dimensions (default: 1536)
- `OLLAMA_URL`: Ollama API URL (default: 'http://localhost:11434')

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

For OpenAI tagging and embeddings (required for embedding generation):
```bash
pip install openai
```

For vector embeddings (required):
```bash
pip install pgvector
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

## Vector Embeddings and Semantic Search

The dashboard includes a vector embedding system for semantic search using OpenAI's `text-embedding-3-small` model and PostgreSQL's `pgvector` extension.

### Setup pgvector

1. **Install pgvector extension** in PostgreSQL:
   ```sql
   \c china_blog
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

2. **Set OpenAI API Key** in settings or environment:
   ```python
   # settings.py or .env
   OPENAI_API_KEY = "your-api-key-here"
   OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"  # Optional, default
   OPENAI_EMBEDDING_DIMENSIONS = 1536  # Optional, default
   ```

### Generate Embeddings

**Important**: The embedding script only processes content that has **both text content AND at least one tag**. This ensures embeddings are only generated for properly categorized content.

Generate embeddings for all eligible content:

```bash
python manage.py generate_embeddings
```

Generate embeddings for specific number of items (for testing):

```bash
python manage.py generate_embeddings --limit 10
```

Skip content that already has embeddings:

```bash
python manage.py generate_embeddings --skip-embedded
```

Generate embeddings for content from a specific source:

```bash
python manage.py generate_embeddings --source 1
```

### Embedding Options

- `--limit N`: Process only N content items
- `--skip-embedded`: Skip content that already has all chunks embedded
- `--has-content-only`: (Deprecated) Now always enforced - content must have text
- `--source ID`: Only process content from specific source ID
- `--chunk-size N`: Max characters per chunk (default: 8000)
- `--overlap N`: Overlap between chunks in characters (default: 200)
- `--workers N`: Number of parallel workers (default: 1, recommended: 3-5)
- `--delay N`: Delay between API requests in seconds (default: 0.05)
- `--dry-run`: Show what would be processed without saving

**Note**: Content without tags or without text content will be automatically skipped during processing.

### How Embeddings Work

1. **Content Chunking**: Long content is automatically split into chunks (default: 8000 characters with 200 character overlap)
2. **Embedding Generation**: Each chunk is embedded using OpenAI's `text-embedding-3-small` model (1536 dimensions)
3. **Embedding Content**: Each chunk's embedding includes:
   - Content title
   - Chunk text content
   - Associated tags (required - content must have tags to be embedded)
4. **Storage**: Embeddings are stored in the `content_chunks` table with an HNSW vector index for fast similarity search
5. **Metadata**: Other fields (source, date, link) are stored as metadata for filtering, not included in embeddings

### Cost Estimation

- **text-embedding-3-small**: ~$0.02 per 1M tokens
- Average content item (~8000 chars) ≈ ~2000 tokens
- **Cost per 1000 items**: ~$0.04 (very affordable)

## Next Steps

- [x] Add Contents tab for managing collected content
- [x] Add authentication system
- [x] Add CSV import for blogs, posts, and ebooks
- [x] Add translation support for French content
- [x] Add automatic content tagging system
- [x] Set up embedding generation pipeline
- [x] Integrate with PostgreSQL pgvector for vector search
- [ ] Add semantic search functionality (query embeddings and similarity search)
- [ ] Add Post Ideas tab for generated article ideas
- [ ] Add Blog Posts tab for managing generated posts
- [ ] Add RAG pipeline for content generation


