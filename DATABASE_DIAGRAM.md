# Database Structure Diagram

## Current Database Schema

```mermaid
erDiagram

    SOURCES {
        BIGSERIAL id PK "Primary Key, Auto-increment"
        STRING name "Source name (e.g., channel name)"
        STRING source_type "youtube|blog|ebook|rss"
        STRING link "URL to source"
        STRING language "en|fr|zh|es|de|other"
        STRING channel_id "YouTube channel ID (nullable)"
        BOOLEAN include_shorts "Include YouTube Shorts (default: false)"
        JSON metadata "Additional metadata as JSON"
        BOOLEAN is_active "Active status (default: true)"
        TIMESTAMP last_collected "Last collection time (nullable)"
        TIMESTAMP created_at "Creation timestamp (auto)"
        TIMESTAMP updated_at "Update timestamp (auto)"
    }

    CONTENTS {
        BIGSERIAL id PK "Primary Key, Auto-increment"
        BIGINT source_id FK "Foreign Key to sources.id"
        STRING external_id "video_id, blog link, etc."
        STRING title "Content title"
        STRING link "URL to content"
        STRING content_type "video|blog_post|ebook"
        DATE date "Publication/upload date"
        TEXT content "Text content (transcript, article, etc.)"
        BOOLEAN has_content "Whether content text is available (auto-set)"
        BOOLEAN processed "Whether embedded/processed (default: false)"
        TIMESTAMP created_at "Creation timestamp (auto)"
        TIMESTAMP updated_at "Update timestamp (auto)"
    }

    SOURCES ||--o{ CONTENTS : "has many"
```

## Field Descriptions

### sources Table

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | Auto-increment |
| name | VARCHAR(255) | NOT NULL | Source name |
| source_type | VARCHAR(20) | NOT NULL, CHECK | Values: youtube, blog, ebook, rss |
| link | VARCHAR(500) | NOT NULL | Valid URL format |
| language | VARCHAR(20) | NOT NULL, CHECK | Values: en, fr, zh, es, de, other |
| channel_id | VARCHAR(100) | NULL | YouTube-specific, optional |
| include_shorts | BOOLEAN | NOT NULL, DEFAULT false | YouTube-specific |
| metadata | JSONB | DEFAULT '{}' | Flexible JSON storage |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | Status flag |
| last_collected | TIMESTAMP | NULL | Track last collection time |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-set on creation |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-update on change |

### contents Table

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | Auto-increment |
| source_id | BIGINT | FOREIGN KEY, NOT NULL | References sources.id |
| external_id | VARCHAR(255) | NOT NULL | video_id, blog slug, etc. |
| title | VARCHAR(500) | NOT NULL | Content title |
| link | VARCHAR(500) | NOT NULL | URL to content |
| content_type | VARCHAR(20) | NOT NULL, CHECK | Values: video, blog_post, ebook |
| date | DATE | NOT NULL | Publication/upload date |
| content | TEXT | NULL | Text content (transcript, article) |
| has_content | BOOLEAN | NOT NULL, DEFAULT false | Whether content text is available (auto-set) |
| processed | BOOLEAN | NOT NULL, DEFAULT false | Embedding/processing status |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-set on creation |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-update on change |

### Indexes

**sources table:**
1. **idx_sources_source_type** - Index on `source_type` column
2. **idx_sources_language** - Index on `language` column
3. **idx_sources_is_active** - Index on `is_active` column
4. **idx_sources_channel_id** - Partial index on `channel_id` (WHERE channel_id IS NOT NULL)
5. **idx_sources_link** - Index on `link` column
6. **idx_sources_metadata** - GIN index on `metadata` JSONB column for efficient JSON queries

**contents table:**
1. **idx_contents_source** - Index on `source_id` (foreign key)
2. **idx_contents_external_id** - Index on `external_id` column
3. **idx_contents_content_type** - Index on `content_type` column
4. **idx_contents_date** - Index on `date` column
5. **idx_contents_has_content** - Index on `has_content` column
6. **idx_contents_processed** - Index on `processed` column

**Unique Constraints:**
- `contents(source_id, external_id)` - Ensures no duplicate content per source

## Entity Relationship

- **sources** (1) → (many) **contents**: One source can have many content items
- Cascade delete: If a source is deleted, all its contents are deleted
