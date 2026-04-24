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
- **Content Processing**: One-click transcript fetching for YouTube videos and content extraction for blog posts
- **Activity Logging**: Comprehensive logging of content operations (fetching, transcript extraction, filtering)
- **China Filter**: Automatic filtering of videos for China-related content (configurable per source)
- **Post Ideas Management**: Generate, manage, and organize blog post ideas with AI-powered generation and automatic duplicate prevention
- **Blog Post Generation**: Generate complete blog posts from post ideas using AI (Ollama, OpenAI, or Gemini) with RAG support
- **Automatic Metadata Generation**: Generate SEO metadata including meta title, meta description, URL slug, tags, featured image alt text, and FAQ
- **FAQ Generation**: Automatically generate 4 FAQ items (question/answer pairs) during metadata generation
- **REST API**: Token-based API endpoints for programmatic content management

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
│           ├── generate_embeddings.py  # Generate vector embeddings
│           ├── find_similar_post_ideas.py  # Find duplicates (text-based)
│           ├── find_similar_post_ideas_embeddings.py  # Find duplicates (embedding-based)
│           └── generate_scheduled_post_ideas.py  # Scheduled post idea generation
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

**post_ideas** - Blog post ideas:
- Title, description, creation/update timestamps
- Title embedding (1536 dimensions) for similarity checking
- HNSW index for fast similarity queries

**blog_posts** - Generated blog posts:
- Title, content (HTML), slug, published status
- SEO metadata: meta_title, meta_description, featured_image_description
- FAQ field: JSON array with question/answer pairs (4 items)
- Post idea reference, tags (many-to-many)
- Featured image, creation/update timestamps

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
   - Filter Videos: (YouTube only) Enable China-relevance filtering
   - Active: Whether source is currently monitored

**Note:** The `link` field is optional for ebook sources since travel books typically don't have URLs.

### Editing Content

When editing content, you have access to several action buttons:

- **Get Transcript** (Videos only): Fetches the transcript from YouTube and replaces the current content
- **Fetch Content** (Blog posts only): Extracts content from the blog post URL
- **Add Tags**: Automatically generates and adds tags to the content
- **Generate Embeddings**: Creates vector embeddings for semantic search

All actions are logged in the activity log, including success and failure states.

### Activity Logging

The dashboard maintains a comprehensive activity log that tracks:
- Content creation, updates, and deletion
- Transcript fetching (success/failure)
- Content extraction (success/failure)
- China filter results (pass/fail with matched keywords)
- Tagging and embedding operations
- Source collection activities

View logs at: http://127.0.0.1:8000/logs/

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
API_TOKEN=your-api-token-here
OPENAI_API_KEY=your-openai-api-key
GEMINI_API_KEY=your-gemini-api-key
OLLAMA_URL=http://localhost:11434
WEBSHARE_PROXY_USERNAME=your-proxy-username
WEBSHARE_PROXY_PASSWORD=your-proxy-password
```

Variables:
- `DB_NAME`: Database name (default: 'china_blog')
- `DB_USER`: Database user (default: 'postgres')
- `DB_PASSWORD`: Database password (default: '')
- `DB_HOST`: Database host (default: 'localhost')
- `DB_PORT`: Database port (default: '5432')
- `DJANGO_SECRET_KEY`: Django secret key (default: insecure key for dev only)
- `API_TOKEN`: Token for API authentication (required for API endpoints)
- `OPENAI_API_KEY`: OpenAI API key (required for embeddings and post idea generation)
- `GEMINI_API_KEY`: Google Gemini API key (optional, for post idea generation)
- `OPENAI_EMBEDDING_MODEL`: Embedding model name (default: 'text-embedding-3-small')
- `OPENAI_EMBEDDING_DIMENSIONS`: Embedding dimensions (default: 1536)
- `OLLAMA_URL`: Ollama API URL (default: 'http://localhost:11434')
- `WEBSHARE_PROXY_USERNAME`: Webshare proxy username (optional, for transcript fetching)
- `WEBSHARE_PROXY_PASSWORD`: Webshare proxy password (optional, for transcript fetching)

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

## API Endpoints

The dashboard provides REST API endpoints for programmatic content management. All API endpoints require token-based authentication.

### Authentication

API requests must include an authentication token in one of the following ways:

1. **Authorization Header** (recommended):
   ```
   Authorization: Token your-api-token-here
   ```
   or
   ```
   Authorization: Bearer your-api-token-here
   ```

2. **Query Parameter**:
   ```
   ?token=your-api-token-here
   ```

Set your API token in the environment variable `API_TOKEN` or in Django settings.

### Get YouTube Channels

**Endpoint**: `GET /api/youtube-channels/`

Returns a list of all YouTube channel sources.

**Example Request**:
```bash
curl -H "Authorization: Token your-api-token" \
     http://127.0.0.1:8000/api/youtube-channels/
```

**Response**:
```json
{
  "success": true,
  "channels": [
    {
      "id": 1,
      "name": "Channel Name",
      "channel_id": "UCxxxxx",
      "include_shorts": false
    }
  ],
  "count": 1
}
```

### Get Blog Sources

**Endpoint**: `GET /api/blog-sources/`

Returns a list of all blog sources that have a sitemap link configured.

**Example Request**:
```bash
curl -H "Authorization: Token your-api-token" \
     http://127.0.0.1:8000/api/blog-sources/
```

**Response**:
```json
{
  "success": true,
  "sources": [
    {
      "id": 1,
      "name": "Source Name",
      "sitemap": "https://example.com/sitemap.xml",
      "filter_china": true
    }
  ],
  "count": 1
}
```

### Create Video Content

**Endpoint**: `POST /api/video-content/`

Creates a new video content entry. If the source has China filtering enabled, videos will be automatically filtered for China-relevance.

**Request Body**:
```json
{
  "source_id": 1,
  "external_id": "VIDEO_ID",
  "title": "Video Title",
  "link": "https://www.youtube.com/watch?v=VIDEO_ID",
  "date": "2025-01-15",
  "description": "Video description (optional, for filtering)",
  "tags": ["tag1", "tag2"],
  "auto_process": true
}
```

**Required Fields**:
- `source_id`: ID of the YouTube source
- `external_id`: YouTube video ID
- `title`: Video title

**Optional Fields**:
- `link`: Full YouTube URL (auto-generated if not provided)
- `date`: Publication date in YYYY-MM-DD format (defaults to today)
- `description`: Video description (used for China filtering)
- `tags`: Array of tags or comma-separated string (used for China filtering)
- `auto_process`: Whether to automatically extract transcript, tag, and embed (default: true)

**Example Request**:
```bash
curl -X POST \
     -H "Authorization: Token your-api-token" \
     -H "Content-Type: application/json" \
     -d '{
       "source_id": 1,
       "external_id": "VIDEO_ID",
       "title": "My Video Title",
       "description": "Video about China travel",
       "tags": ["china", "travel"]
     }' \
     http://127.0.0.1:8000/api/video-content/
```

**Response (Success)**:
```json
{
  "success": true,
  "content_id": 123,
  "message": "Content created successfully",
  "filtered": false,
  "processing": {
    "transcript": true,
    "tags": true,
    "embeddings": true
  }
}
```

**Response (Filtered Out)**:
```json
{
  "success": false,
  "error": "Video was filtered out - not China-related",
  "filtered": true,
  "reason": "Video does not appear to be relevant to China based on title, description, and tags",
  "matched_keywords": []
}
```

**Response (Error)**:
```json
{
  "success": false,
  "error": "Error message here"
}
```

### Create Blog Post

**Endpoint**: `POST /api/blog-post/`

Creates a new blog post content entry. The content will be automatically processed (extracted, translated, tagged, embedded) if `auto_process` is enabled.

**Request Body**:
```json
{
  "source_id": 1,
  "title": "Blog Post Title",
  "link": "https://example.com/blog-post",
  "date": "2025-01-15",
  "auto_process": true
}
```

**Required Fields**:
- `source_id`: ID of the blog source
- `title`: Blog post title
- `link`: Full URL to the blog post

**Optional Fields**:
- `date`: Publication date in YYYY-MM-DD format or ISO 8601 (defaults to today)
- `auto_process`: Whether to automatically extract content, translate, tag, and embed (default: true)

**Example Request**:
```bash
curl -X POST \
     -H "Authorization: Token your-api-token" \
     -H "Content-Type: application/json" \
     -d '{
       "source_id": 1,
       "title": "My Blog Post Title",
       "link": "https://example.com/my-blog-post",
       "date": "2025-01-15"
     }' \
     http://127.0.0.1:8000/api/blog-post/
```

**Response (Success)**:
```json
{
  "success": true,
  "content_id": 123,
  "message": "Blog post created successfully",
  "processing": {
    "extracted": true,
    "translated": false,
    "tagged": true,
    "embedded": true
  }
}
```

**Response (Error)**:
```json
{
  "success": false,
  "error": "Error message here"
}
```

**Note**: If a blog post with the same link already exists for the source, the API will return a 409 Conflict error.

### Generate Post Ideas

**Endpoint**: `POST /post-ideas/api/generate/` or `POST /post-ideas/api/generate`

Generates blog post ideas using AI (Ollama, OpenAI, or Gemini). This endpoint is designed for automation tools like n8n.

**Authentication**: Token-based (via query parameter or Authorization header)

**Request Body**:
```json
{
  "num_ideas": 5,
  "provider": "ollama",
  "model": "llama3.2",
  "tags": [1, 2, 3],
  "contents": [10, 20, 30]
}
```

**Required Fields**:
- `num_ideas`: Number of post ideas to generate (integer)
- `provider`: AI provider - `"ollama"`, `"openai"`, or `"gemini"` (string)
- `model`: Model name to use (string, e.g., `"llama3.2"`, `"gpt-4"`, `"gemini-pro"`)

**Optional Fields**:
- `tags`: Array of tag IDs to base ideas on (array of integers)
- `contents`: Array of content IDs to base ideas on (array of integers)

**Example Request**:
```bash
curl -X POST \
     -H "Content-Type: application/json" \
     -d '{
       "num_ideas": 5,
       "provider": "openai",
       "model": "gpt-4",
       "tags": [1, 2]
     }' \
     "http://127.0.0.1:8000/post-ideas/api/generate/?token=your-api-token"
```

**Response (Success)**:
```json
{
  "success": true,
  "created_count": 5,
  "num_requested": 5,
  "provider": "openai",
  "model": "gpt-4",
  "ideas": [
    {"id": 123, "title": "Post Idea Title 1"},
    {"id": 124, "title": "Post Idea Title 2"}
  ]
}
```

**Note**: The API automatically filters out duplicate ideas using embedding-based similarity checking. If some ideas are skipped due to similarity, the `created_count` may be less than `num_requested`. Check the activity log for details about skipped ideas.

**Response (Error)**:
```json
{
  "success": false,
  "error": "Error message here"
}
```

**Note**: The endpoint supports both trailing slash (`/post-ideas/api/generate/`) and without (`/post-ideas/api/generate`) for compatibility with different automation tools.

### China Filtering

When a source has `filter_videos` enabled, videos added via the API are automatically checked for China-relevance. The filter analyzes:
- Video title
- Video description
- Video tags

If a video doesn't match China-related keywords, it will be rejected with a `filtered: true` response. Filter results (pass/fail and matched keywords) are logged in the activity log.

## Testing Transcript Extraction

A management command is available for testing YouTube transcript extraction:

```bash
python manage.py test_transcript VIDEO_ID --source-id 1
```

**Options**:
- `VIDEO_ID`: YouTube video ID to test
- `--source-id ID`: Source ID to use (creates one if not provided)
- `--use-proxy`: Use Webshare proxy for fetching (default: True if configured)
- `--title TITLE`: Title for the test content (optional)
- `--keep`: Keep the test content after testing (default: deletes it)

**Example**:
```bash
python manage.py test_transcript dQw4w9WgXcQ --source-id 1 --use-proxy
```

This command helps debug transcript fetching issues, especially related to proxy configuration.

## Post Ideas Management

The dashboard includes a Post Ideas feature for generating and managing blog post ideas using AI.

### Features

- **Manual Creation**: Add post ideas manually with title and description
- **AI Generation**: Generate multiple post ideas using Ollama, OpenAI, or Gemini
- **Tag-Based Generation**: Generate ideas based on specific tags
- **Content-Based Generation**: Generate ideas inspired by existing content
- **Automatic Duplicate Prevention**: New ideas are automatically checked for similarity to prevent duplicates
- **Similarity Detection**: Find and manage duplicate or similar ideas using text-based or embedding-based methods
- **Idea Count Display**: View the total number of post ideas in the sidebar and filtered count on the list page
- **Activity Logging**: All idea creation, updates, and generation activities are logged

### Generating Post Ideas

1. Navigate to **Post Ideas** in the sidebar (shows total count)
2. Click **Generate Ideas**
3. Configure generation settings:
   - **Number of Ideas**: How many ideas to generate
   - **Provider**: Choose between Ollama (free, local), OpenAI, or Gemini
   - **Model**: Select the specific model to use
   - **Tags** (optional): Select tags to base ideas on
   - **Content** (optional): Search and select existing content to inspire ideas
4. Click **Generate Ideas**

The system will generate post ideas based on your selected criteria and add them to your ideas list. **Duplicate ideas are automatically filtered out** using embedding-based similarity checking (85% similarity threshold).

### Finding and Managing Duplicate Ideas

The dashboard provides two methods to find similar or duplicate post ideas:

#### Text-Based Similarity Checking

Fast, free method using string comparison and keyword overlap:

```bash
# Find similar ideas (default threshold: 0.8)
python manage.py find_similar_post_ideas --group

# Use custom threshold
python manage.py find_similar_post_ideas --threshold 0.85 --group

# Export results to file
python manage.py find_similar_post_ideas --group --export report.txt

# Preview deletions (dry run)
python manage.py find_similar_post_ideas --group --threshold 0.8

# Actually delete duplicates (keeps oldest in each group)
python manage.py find_similar_post_ideas --group --delete
```

**Options:**
- `--threshold`: Similarity threshold (0.0-1.0, default: 0.8)
- `--min-similarity`: Minimum similarity to report (default: 0.5)
- `--group`: Group similar ideas together for easier review
- `--export`: Export results to a text file
- `--delete`: Actually delete duplicates (without this flag, only previews)

#### Embedding-Based Similarity Checking

More accurate semantic similarity detection using OpenAI embeddings:

```bash
# Generate embeddings for ideas that don't have them
python manage.py find_similar_post_ideas_embeddings --generate-embeddings

# Find similar ideas using embeddings (default threshold: 0.85)
python manage.py find_similar_post_ideas_embeddings --group

# Use custom threshold
python manage.py find_similar_post_ideas_embeddings --threshold 0.9 --group

# Preview deletions
python manage.py find_similar_post_ideas_embeddings --group

# Actually delete duplicates
python manage.py find_similar_post_ideas_embeddings --group --delete
```

**Options:**
- `--generate-embeddings`: Generate embeddings for ideas without them
- `--threshold`: Similarity threshold (0.0-1.0, default: 0.85)
- `--min-similarity`: Minimum similarity to report (default: 0.7)
- `--group`: Group similar ideas together
- `--export`: Export results to a text file
- `--delete`: Actually delete duplicates

**Note**: Embedding-based checking requires OpenAI API key and generates embeddings for ideas that don't have them yet.

### Automatic Duplicate Prevention

When generating new post ideas (via web interface or API), the system automatically:

1. **Generates embeddings** for each new idea title
2. **Checks similarity** against all existing ideas with embeddings
3. **Skips ideas** that are ≥80% similar to existing ones
4. **Stores embeddings** for future similarity checks
5. **Reports skipped ideas** in the activity log

This prevents duplicate ideas from being created during generation. The similarity threshold is set to 80% (0.8) by default.

### API Integration

The Post Ideas generation can be triggered via API for automation (e.g., with n8n). See the [API Endpoints](#generate-post-ideas) section for details.

**Note**: The API also includes automatic duplicate prevention - similar ideas will be automatically skipped during generation.

### Cost Estimation

- **text-embedding-3-small**: ~$0.02 per 1M tokens
- Average content item (~8000 chars) ≈ ~2000 tokens
- **Cost per 1000 items**: ~$0.04 (very affordable)

## Blog Post Generation

The dashboard includes a comprehensive blog post generation system that creates complete blog posts from post ideas using AI.

### Features

- **AI-Powered Content Generation**: Generate full blog post content using Ollama, OpenAI, or Gemini
- **RAG Support**: Use Retrieval-Augmented Generation (RAG) to include relevant context from your content library
- **Automatic Metadata Generation**: Generate SEO metadata including:
  - Meta title (50-60 characters)
  - Meta description (145-160 characters)
  - URL slug (SEO-friendly)
  - Tags (5-8 relevant tags)
  - Featured image alt text (50-125 characters)
  - **FAQ Section**: 4 FAQ items with question/answer pairs
- **FAQ Management**: View and edit FAQ items in the blog post detail and edit pages
- **Content Processing**: Automatic image extraction and management from blog post content

### Generating a Blog Post

1. Navigate to **Post Ideas** in the sidebar
2. Find a post idea you want to turn into a blog post
3. Click **Generate Blog Post** next to the idea
4. Configure generation settings:
   - **Provider**: Choose between Ollama, OpenAI, or Gemini
   - **Model**: Select the specific model to use
   - **Use RAG**: Enable to include relevant context from your content library
   - **Number of Chunks**: How many content chunks to include (if RAG is enabled)
5. Click **Generate Blog Post**

The system will:
1. Generate the full blog post content based on the post idea
2. Extract and create image records from the content
3. Optionally generate metadata (if not done separately)

### Generating Metadata

After generating blog post content, you can generate SEO metadata:

1. Navigate to the blog post detail page
2. Click **Generate Metadata**
3. Select provider and model
4. Click **Generate Metadata**

The system will automatically generate:
- Meta title optimized for SEO
- Meta description with call-to-action
- URL slug
- Relevant tags
- Featured image alt text
- **4 FAQ items** with question/answer pairs

### FAQ Generation

FAQ items are automatically generated during metadata generation. The system:

- Generates exactly 4 FAQ items related to the blog post content
- Creates "People Also Ask" style questions that travelers might search for
- Provides concise answers (2-4 sentences) that directly address each question
- Stores FAQ items as JSON in the database

**Viewing FAQ**: FAQ items are displayed in the blog post detail page sidebar with a clean, readable format.

**Editing FAQ**: FAQ items can be edited in the blog post edit form. The FAQ field accepts JSON format:
```json
[
  {
    "question": "Question 1?",
    "answer": "Answer 1"
  },
  {
    "question": "Question 2?",
    "answer": "Answer 2"
  }
]
```

The edit form includes:
- JSON formatting button
- JSON validation
- Clear button to remove FAQ

### API: Generate Blog Post

**Endpoint**: `POST /api/generate-blog-post`

Generates a complete blog post from a post idea, including content and metadata.

**Request Body**:
```json
{
  "post_idea_id": 123,
  "provider": "gemini",
  "model": "gemini-3-pro-preview",
  "use_rag": true,
  "num_chunks": 5,
  "metadata_provider": "gemini",
  "metadata_model": "gemini-3-pro-preview",
  "enable_internal_links": true,
  "internal_links_limit": 5,
  "internal_links_mode": "ai"
}
```

**Required Fields**:
- `post_idea_id`: ID of the post idea to generate from

**Optional Fields**:
- `provider`: AI provider for content generation (`ollama`, `openai`, `gemini`). Default: `gemini`
- `model`: Model name for content generation. Default: `gemini-3-pro-preview`
- `use_rag`: Whether to use RAG context. Default: `false`
- `num_chunks`: Number of RAG chunks to use. Default: `5`
- `metadata_provider`: AI provider for metadata generation. Default: same as `provider`
- `metadata_model`: Model name for metadata generation. Default: same as `model`
- `enable_internal_links`: Automatically insert internal links into generated content. Default: `true`
- `internal_links_limit`: Max number of inserted internal links (`1-10`). Default: `5`
- `internal_links_mode`: Linking strategy (`ai` or `rule_based`). Default: `ai`
- Internal links are quality-gated: weak one-word anchors and low-confidence matches are skipped.

**Response (Success)**:
```json
{
  "success": true,
  "blog_post": {
    "id": 456,
    "title": "Blog Post Title",
    "slug": "blog-post-slug",
    "meta_title": "SEO Meta Title",
    "meta_description": "SEO meta description...",
    "published": false,
    "created_at": "2025-01-15T10:30:00Z",
    "post_idea_id": 123,
    "tags": ["tag1", "tag2"],
    "featured_image_description": "Alt text for featured image"
  },
  "generation_info": {
    "content_provider": "gemini",
    "content_model": "gemini-3-pro-preview",
    "metadata_provider": "gemini",
    "metadata_model": "gemini-3-pro-preview",
    "used_rag": true,
    "internal_linking": {
      "enabled": true,
      "mode": "ai",
      "limit": 5,
      "suggestions_count": 5,
      "inserted_count": 3,
      "used_ai": true,
      "fallback_to_rule_based": false,
      "ai_failure_reason": null,
      "ai_failure_details": {},
      "inserted": [
        {
          "target_post_id": 512,
          "target_url": "https://sinotales.com/destinations/shanghai/where-to-stay-shanghai-first-time-guide/",
          "anchor": "where to stay"
        }
      ]
    }
  }
}
```

**Note**: The API automatically generates both blog post content and metadata (including FAQ) in a single request.

If `mode` is `ai` but `used_ai` is `false`, check:
- `ai_failure_reason` (e.g. `invalid_json`, `missing_updated_html`, `invalid_applied_links_type`, `no_valid_links_after_validation`, `provider_error`)
- `ai_failure_details` for extra debug context.
  - For `provider_error`, details include `provider`, `model`, `error_message`, and `traceback_preview`.

### API: Internal Link Suggestions

**Endpoint**: `GET /api/internal-links/suggestions`

Returns ranked internal-link suggestions for a blog post so automation workflows (e.g., n8n) can insert contextual links in article content.

**Authentication**: Token-based (same as other API endpoints).

**Query Parameters**:
- `post_id` (optional): Blog post ID to generate suggestions for
- `slug` (optional): Blog post slug to generate suggestions for
- `limit` (optional): Number of suggestions to return (default: `5`, max: `10`)

You must provide **either** `post_id` **or** `slug`.

**Example Request (by post ID)**:
```bash
curl -H "Authorization: Token your-api-token" \
     "http://127.0.0.1:8000/api/internal-links/suggestions?post_id=456&limit=5"
```

**Example Request (by slug)**:
```bash
curl -H "Authorization: Token your-api-token" \
     "http://127.0.0.1:8000/api/internal-links/suggestions?slug=shanghai-french-concession-walking-guide"
```

**Response (Success)**:
```json
{
  "success": true,
  "source_post": {
    "id": 456,
    "title": "Shanghai French Concession Walking Guide: Best Streets & Hidden Cafes",
    "slug": "shanghai-french-concession-walking-guide"
  },
  "count": 5,
  "suggestions": [
    {
      "target_post_id": 512,
      "target_title": "Where to Stay in Shanghai First Time: Best Areas Explained",
      "target_slug": "where-to-stay-shanghai-first-time-guide",
      "target_url": "https://sinotales.com/destinations/shanghai/where-to-stay-shanghai-first-time-guide/",
      "suggested_anchor": "where to stay",
      "shared_tag_count": 2,
      "shared_tags": ["shanghai", "itinerary"],
      "title_appears_in_source": false
    }
  ],
  "filters": {
    "limit": 5,
    "published_only": true,
    "exclude_source_post": true
  }
}
```

**Ranking / Selection Rules**:
- Uses only `published=true` blog posts as candidates
- Excludes the source post itself
- Prioritizes posts sharing tags with the source post
- Uses recency as a secondary sort signal
- Returns an anchor suggestion only when a natural multi-word phrase is found in source content
- Skips low-confidence suggestions with no shared tags and no title-overlap signal
- In `ai` mode, suggestions are passed to an LLM to place links naturally; if parsing/validation fails, backend falls back to deterministic `rule_based` insertion

## Next Steps

- [x] Add Contents tab for managing collected content
- [x] Add authentication system
- [x] Add CSV import for blogs, posts, and ebooks
- [x] Add translation support for French content
- [x] Add automatic content tagging system
- [x] Set up embedding generation pipeline
- [x] Integrate with PostgreSQL pgvector for vector search
- [x] Add Post Ideas tab for generated article ideas
- [x] Add duplicate detection and prevention for post ideas
- [x] Add Blog Posts tab for managing generated posts
- [x] Add blog post generation from post ideas
- [x] Add automatic metadata generation (meta title, description, slug, tags, alt text)
- [x] Add FAQ generation (4 FAQ items with question/answer pairs)
- [x] Add RAG pipeline for content generation
- [x] Add internal link suggestion API endpoint for automation workflows
- [ ] Add semantic search functionality (query embeddings and similarity search)


