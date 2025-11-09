# Database Structure Diagram

## Current Database Schema

```mermaid
erDiagram

    SOURCES {
        BIGSERIAL id PK "Primary Key, Auto-increment"
        STRING name "Source name (e.g., channel name)"
        STRING source_type "youtube|blog|ebook|rss"
        STRING link "URL to source (nullable)"
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
        STRING link "URL to content (nullable)"
        STRING content_type "video|blog_post|ebook"
        DATE date "Publication/upload date"
        TEXT content "Text content (transcript, article, etc.)"
        BOOLEAN has_content "Whether content text is available (auto-set)"
        BOOLEAN processed "Whether embedded/processed (default: false)"
        TIMESTAMP created_at "Creation timestamp (auto)"
        TIMESTAMP updated_at "Update timestamp (auto)"
    }

    TAGS {
        BIGSERIAL id PK "Primary Key, Auto-increment"
        STRING name "Tag name (unique)"
        STRING slug "URL-friendly tag name (unique)"
        TEXT description "Optional description"
        TIMESTAMP created_at "Creation timestamp (auto)"
    }

    CONTENT_CHUNKS {
        BIGSERIAL id PK "Primary Key, Auto-increment"
        BIGINT content_id FK "Foreign Key to contents.id"
        INTEGER chunk_index "Order of chunk within content (0-based)"
        TEXT text "Text content of chunk"
        VECTOR embedding "Vector embedding (1536 dimensions, nullable)"
        TIMESTAMP created_at "Creation timestamp (auto)"
    }

    SOURCES ||--o{ CONTENTS : "has many"
    CONTENTS ||--o{ CONTENT_CHUNKS : "has many"
    CONTENTS }o--o{ TAGS : "tagged with"
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
| link | VARCHAR(500) | NULL | URL to content (optional) |
| content_type | VARCHAR(20) | NOT NULL, CHECK | Values: video, blog_post, ebook |
| date | DATE | NOT NULL | Publication/upload date |
| content | TEXT | NULL | Text content (transcript, article) |
| has_content | BOOLEAN | NOT NULL, DEFAULT false | Whether content text is available (auto-set) |
| processed | BOOLEAN | NOT NULL, DEFAULT false | Embedding/processing status |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-set on creation |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-update on change |

### tags Table

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | Auto-increment |
| name | VARCHAR(100) | UNIQUE, NOT NULL | Tag name |
| slug | VARCHAR(100) | UNIQUE, NOT NULL | URL-friendly version |
| description | TEXT | NULL | Optional description |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-set on creation |

### content_chunks Table

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| id | BIGSERIAL | PRIMARY KEY | Auto-increment |
| content_id | BIGINT | FOREIGN KEY, NOT NULL | References contents.id |
| chunk_index | INTEGER | NOT NULL | Order of chunk (0-based) |
| text | TEXT | NOT NULL | Text content of chunk |
| embedding | VECTOR(1536) | NULL | Vector embedding for semantic search |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | Auto-set on creation |

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

**tags table:**
1. **idx_tags_name** - Index on `name` column
2. **idx_tags_slug** - Index on `slug` column

**content_chunks table:**
1. **idx_content_chunks_content_index** - Index on `(content_id, chunk_index)`
2. **idx_content_chunks_embedding** - HNSW vector index on `embedding` column (for similarity search)

**Unique Constraints:**
- `contents(source_id, external_id)` - Ensures no duplicate content per source
- `tags(name)` - Ensures unique tag names
- `tags(slug)` - Ensures unique tag slugs
- `content_chunks(content_id, chunk_index)` - Ensures unique chunk indices per content

## Entity Relationships

- **sources** (1) → (many) **contents**: One source can have many content items
- **contents** (1) → (many) **content_chunks**: One content item can have many chunks
- **contents** (many) ↔ (many) **tags**: Content can have multiple tags, tags can be applied to multiple content
- Cascade delete: 
  - If a source is deleted, all its contents are deleted
  - If content is deleted, all its chunks are deleted

## Vector Search

The `content_chunks` table uses PostgreSQL's `pgvector` extension for semantic search:
- **Embedding Model**: OpenAI `text-embedding-3-small` (1536 dimensions)
- **Index Type**: HNSW (Hierarchical Navigable Small World) for fast similarity search
- **Embedding Content**: Each chunk includes title, content text, and tags for comprehensive semantic representation
