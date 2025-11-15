# Management Commands Reference

This document lists all available Django management commands for the China Blog Dashboard project.

## Table of Contents

- [Testing & Debugging](#testing--debugging)
- [Import Commands](#import-commands)
- [Content Processing](#content-processing)
- [Maintenance & Utilities](#maintenance--utilities)

---

## Testing & Debugging

### `test_sitemap_parser`

Test parsing a sitemap URL and extract blog posts. Useful for debugging sitemap fetching and parsing issues.

**Usage:**
```bash
python manage.py test_sitemap_parser <sitemap_url> [options]
```

**Arguments:**
- `sitemap_url` (required): URL of the sitemap to parse

**Options:**
- `--base-url <url>`: Base URL for Referer header (helps bypass 403 errors)
- `--use-proxy`: Use Webshare proxies for requests (helps bypass Cloudflare)

**Example:**
```bash
python manage.py test_sitemap_parser https://example.com/sitemap.xml --use-proxy
```

---

### `test_transcript`

Test transcript extraction from a YouTube video. Useful for debugging transcript fetching issues.

**Usage:**
```bash
python manage.py test_transcript <video_id> [options]
```

**Arguments:**
- `video_id` (required): YouTube video ID to test (e.g., `Lca0ozE0T2o`)

**Options:**
- `--source-id <id>`: Source ID to use (optional - will create a test content entry)
- `--use-proxy`: Use proxy for transcript extraction
- `--title <title>`: Title for the test content (default: "Test Video")

**Example:**
```bash
python manage.py test_transcript Lca0ozE0T2o --use-proxy
```

---

### `test_video_filter`

Test video filtering for China relevance on a YouTube channel. Tests both keyword-based and AI-based filtering.

**Usage:**
```bash
python manage.py test_video_filter <channel_id> [options]
```

**Arguments:**
- `channel_id` (required): YouTube channel ID to test

**Options:**
- `--max-videos <n>`: Maximum number of videos to test (default: 20)
- `--include-shorts`: Include YouTube Shorts in the test
- `--use-ollama`: Use Ollama AI to analyze transcripts for relevance
- `--ollama-model <model>`: Ollama model to use (default: from settings or `gpt-oss:20b-cloud`)
- `--skip-transcript-failures`: Continue testing even if transcript cannot be fetched

**Example:**
```bash
python manage.py test_video_filter UCxxxxx --max-videos 50 --use-ollama
```

---

## Import Commands

### `import_channels`

Import YouTube channels from a CSV file.

**Usage:**
```bash
python manage.py import_channels <csv_file> [options]
```

**Arguments:**
- `csv_file` (required): Path to the channels CSV file

**Options:**
- `--skip-existing`: Skip channels that already exist (based on channel_id or link)

**CSV Format:**
- `name`: Channel name
- `link`: Channel URL
- `channel_id`: YouTube channel ID
- `language`: Language (English, French, etc.)
- `include_shorts`: Boolean (True/False)

**Example:**
```bash
python manage.py import_channels channels.csv --skip-existing
```

---

### `import_videos`

Import videos from a CSV file.

**Usage:**
```bash
python manage.py import_videos <csv_file> [options]
```

**Arguments:**
- `csv_file` (required): Path to the videos CSV file

**Options:**
- `--skip-existing`: Skip videos that already exist (based on source + external_id)
- `--load-transcripts`: Load transcript content from transcript files if available

**CSV Format:**
- `channel_name`: Source channel name
- `video_title`: Video title
- `video_id`: YouTube video ID
- `upload_date`: Upload date

**Example:**
```bash
python manage.py import_videos videos.csv --load-transcripts
```

---

### `import_blogs`

Import blogs from a CSV file.

**Usage:**
```bash
python manage.py import_blogs <csv_file> [options]
```

**Arguments:**
- `csv_file` (required): Path to the blogs CSV file

**Options:**
- `--skip-existing`: Skip blogs that already exist (based on link)

**CSV Format:**
- `name`: Blog name
- `url`: Blog URL
- `language`: Language (English, French, etc.)
- `rss_feed`: RSS feed URL (optional)
- `sitemaps`: Sitemap URLs (optional)
- `filter_china`: Boolean (True/False)
- `blog_only`: Boolean (True/False)

**Example:**
```bash
python manage.py import_blogs blogs.csv --skip-existing
```

---

### `import_posts`

Import blog posts from a CSV file.

**Usage:**
```bash
python manage.py import_posts <csv_file> [options]
```

**Arguments:**
- `csv_file` (required): Path to the posts CSV file

**Options:**
- `--skip-existing`: Skip posts that already exist (based on source + external_id)
- `--load-content`: Load content from content files if available
- `--content-dir <dir>`: Path to content directory (default: `content/` relative to CSV file or project root)

**CSV Format:**
- `id`: Post external ID
- `title`: Post title
- `link`: Post URL
- `date`: Post date
- `source`: Source name
- `tags`: Tags (optional)
- `content_file`: Content file path (optional)

**Example:**
```bash
python manage.py import_posts posts.csv --load-content --content-dir ./content
```

---

### `import_ebooks`

Import ebooks from a CSV file or scan directory.

**Usage:**
```bash
python manage.py import_ebooks [csv_file] [options]
```

**Arguments:**
- `csv_file` (optional): Path to the ebooks CSV file (required if not using `--scan-dir`)

**Options:**
- `--skip-existing`: Skip ebooks that already exist (based on source + external_id)
- `--load-content`: Load content from TXT files (default: True)
- `--txt-dir <dir>`: Path to TXT files directory (default: `ebooks/` relative to project root)
- `--scan-dir`: Scan ebooks/txt directory and import all TXT files
- `--translate-fr`: Translate French content to English (default: True)
- `--no-translate`: Skip translation even for French content

**CSV Format:**
- `title`: Ebook title
- `author`: Author name (optional)
- `source`: Source name
- `language`: Language code
- `date`: Publication date
- `link`: Link (optional)
- `txt_file`: TXT file name

**Examples:**
```bash
# Import from CSV
python manage.py import_ebooks ebooks.csv --load-content

# Scan directory
python manage.py import_ebooks --scan-dir --txt-dir ./ebooks/txt
```

---

## Content Processing

### `extract_blog_content`

Extract content from blog post URLs. Fetches content from URLs for blog posts that don't have content yet.

**Usage:**
```bash
python manage.py extract_blog_content [options]
```

**Options:**
- `--source <name>`: Only extract content for posts from a specific source name
- `--force`: Re-extract content even if it already exists
- `--use-proxy`: Use Webshare proxies for fetching content (helps bypass Cloudflare)
- `--limit <n>`: Limit the number of posts to process
- `--dry-run`: Show what would be extracted without actually extracting

**Example:**
```bash
python manage.py extract_blog_content --source "Example Blog" --use-proxy --limit 10
```

---

### `auto_tag_content`

Automatically tag content using LLM (Ollama or OpenAI).

**Usage:**
```bash
python manage.py auto_tag_content [options]
```

**Options:**
- `--provider <provider>`: LLM provider to use - `ollama` or `openai` (default: `ollama`)
- `--model <model>`: Model name (e.g., `2` for Ollama, `gpt-3.5-turbo` for OpenAI)
- `--limit <n>`: Limit number of content items to process
- `--re-tag`: Re-tag content even if it already has tags (default: skips content with existing tags)
- `--has-content-only`: Only tag content that has text content
- `--source <id>`: Only tag content from specific source ID
- `--dry-run`: Show what would be tagged without actually saving
- `--workers <n>`: Number of parallel workers (default: 1, recommended: 2-4 for Ollama, 3-5 for OpenAI)
- `--delay <seconds>`: Delay between requests in seconds (default: 0.5 for Ollama, 0.1 for OpenAI)
- `--reverse`: Process content in reverse order (useful for running multiple instances in parallel)

**Example:**
```bash
python manage.py auto_tag_content --provider ollama --workers 3 --limit 100
```

---

### `generate_embeddings`

Generate embeddings for content using OpenAI.

**Usage:**
```bash
python manage.py generate_embeddings [options]
```

**Options:**
- `--limit <n>`: Limit number of content items to process
- `--skip-embedded`: Skip content that already has embeddings (chunks exist)
- `--has-content-only`: Only process content that has text content
- `--source <id>`: Only process content from specific source ID
- `--chunk-size <n>`: Maximum characters per chunk (default: 8000)
- `--overlap <n>`: Overlap between chunks in characters (default: 200)
- `--workers <n>`: Number of parallel workers (default: 1, recommended: 2-3 for OpenAI)
- `--dry-run`: Show what would be processed without actually saving

**Note:** Only processes content that has both text content and tags.

**Example:**
```bash
python manage.py generate_embeddings --workers 2 --chunk-size 6000
```

---

## Maintenance & Utilities

### `update_posts_content`

Update blog post content from content files. Finds posts by matching filename to external_id, updates content, sets processed=False, clears tags, and deletes chunks.

**Usage:**
```bash
python manage.py update_posts_content [content_dir] [options]
```

**Arguments:**
- `content_dir` (optional): Path to content directory (default: `content`)

**Options:**
- `--dry-run`: Show what would be updated without actually updating (default behavior)
- `--force`: Actually update the posts (overrides `--dry-run`)
- `--source <name>`: Only update posts from a specific source name

**Example:**
```bash
python manage.py update_posts_content ./content --force --source "Example Blog"
```

---

### `cleanup_posts`

Find and delete blog posts that exist in the database but not in the CSV file.

**Usage:**
```bash
python manage.py cleanup_posts <csv_file> [options]
```

**Arguments:**
- `csv_file` (required): Path to the posts CSV file

**Options:**
- `--dry-run`: Show what would be deleted without actually deleting (default behavior)
- `--force`: Actually delete the posts (required to perform deletion)
- `--source <name>`: Only check posts from a specific source name

**Example:**
```bash
python manage.py cleanup_posts posts.csv --force
```

---

### `update_has_content`

Update `has_content` field for all existing Content records based on whether content text exists.

**Usage:**
```bash
python manage.py update_has_content [options]
```

**Options:**
- `--dry-run`: Show what would be updated without actually updating

**Example:**
```bash
python manage.py update_has_content
```

---

## Notes

### Proxy Configuration

Several commands support proxy usage via Webshare. To use proxies, ensure you have the following environment variables set (or in your `.env` file):

- `WEBSHARE_API_TOKEN`: Webshare API token (preferred)
- OR `WEBSHARE_PROXY_USERNAME` and `WEBSHARE_PROXY_PASSWORD`: Proxy credentials

### File Paths

Most import commands accept both absolute and relative paths. Relative paths are resolved in this order:
1. Current working directory
2. Project root directory
3. Common data directories (for videos/channels)

### CSV Format

All CSV import commands expect UTF-8 encoded files with headers. Column names are case-insensitive and whitespace is trimmed.

### Parallel Processing

Commands that support `--workers` option can process multiple items in parallel for faster execution. Recommended worker counts:
- **Ollama**: 2-4 workers
- **OpenAI**: 3-5 workers

Be mindful of API rate limits when using parallel processing.

---

## Command Summary

| Command | Purpose | Key Features |
|---------|---------|--------------|
| `test_sitemap_parser` | Test sitemap parsing | Proxy support, multiple fetch strategies |
| `test_transcript` | Test transcript extraction | Proxy support, creates test content |
| `test_video_filter` | Test video filtering | Keyword + AI filtering, transcript analysis |
| `import_channels` | Import YouTube channels | CSV import, skip existing |
| `import_videos` | Import videos | CSV import, transcript loading |
| `import_blogs` | Import blog sources | CSV import, metadata support |
| `import_posts` | Import blog posts | CSV import, content file loading |
| `import_ebooks` | Import ebooks | CSV/directory scan, translation, part files |
| `extract_blog_content` | Extract blog content | Proxy support, force re-extraction |
| `auto_tag_content` | Auto-tag content | LLM tagging, parallel processing |
| `generate_embeddings` | Generate embeddings | OpenAI embeddings, chunking |
| `update_posts_content` | Update post content | File-based updates, cleanup |
| `cleanup_posts` | Cleanup orphaned posts | CSV comparison, safe deletion |
| `update_has_content` | Update content flags | Batch update, dry-run support |

