# Database Structure Diagram

## Current Database Schema

```mermaid
erDiagram
    sources {
        bigserial id PK "Primary Key, Auto-increment"
        varchar_255 name "Source name (e.g., channel name)"
        varchar_20 source_type "youtube|blog|ebook|rss"
        varchar_500 link "URL to source"
        varchar_20 language "en|fr|zh|es|de|other"
        varchar_100 channel_id "YouTube channel ID (nullable)"
        boolean include_shorts "Include YouTube Shorts (default: false)"
        jsonb metadata "Additional metadata as JSON"
        boolean is_active "Active status (default: true)"
        timestamp last_collected "Last collection time (nullable)"
        timestamp created_at "Creation timestamp (auto)"
        timestamp updated_at "Update timestamp (auto)"
    }
    
    contents {
        bigserial id PK "Primary Key, Auto-increment"
        bigint source_id FK "Foreign Key to sources.id"
        varchar_255 external_id "video_id, blog link, etc."
        varchar_500 title "Content title"
        varchar_500 link "URL to content"
        varchar_20 content_type "video|blog_post|ebook"
        date date "Publication/upload date"
        text content "Text content (transcript, article, etc.)"
        boolean processed "Whether embedded/processed (default: false)"
        timestamp created_at "Creation timestamp (auto)"
        timestamp updated_at "Update timestamp (auto)"
    }
    
    sources ||--o{ contents : "has many"
    
    %% Indexes
    sources ||--o{ "idx_sources_source_type" : "has index on"
    sources ||--o{ "idx_sources_language" : "has index on"
    sources ||--o{ "idx_sources_is_active" : "has index on"
    sources ||--o{ "idx_sources_channel_id" : "has index on (where not null)"
    sources ||--o{ "idx_sources_link" : "has index on"
    sources ||--o{ "idx_sources_metadata" : "has GIN index on"
    
    contents ||--o{ "idx_contents_source" : "has index on"
    contents ||--o{ "idx_contents_external_id" : "has index on"
    contents ||--o{ "idx_contents_content_type" : "has index on"
    contents ||--o{ "idx_contents_date" : "has index on"
    contents ||--o{ "idx_contents_processed" : "has index on"
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
5. **idx_contents_processed** - Index on `processed` column

**Unique Constraints:**
- `contents(source_id, external_id)` - Ensures no duplicate content per source

## Entity Relationship

- **sources** (1) → (many) **contents**: One source can have many content items
- Cascade delete: If a source is deleted, all its contents are deleted
