# PostgreSQL Table Structure for Sources

## Table: `sources`

This table stores information about content sources (YouTube channels, blogs, ebooks, etc.)

### SQL CREATE TABLE Statement

```sql
CREATE TABLE sources (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    source_type VARCHAR(20) NOT NULL DEFAULT 'youtube',
    link VARCHAR(500) NOT NULL,
    language VARCHAR(20) NOT NULL DEFAULT 'en',
    channel_id VARCHAR(100) NULL,
    include_shorts BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_collected TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT sources_source_type_check CHECK (
        source_type IN ('youtube', 'blog', 'ebook', 'rss')
    ),
    CONSTRAINT sources_language_check CHECK (
        language IN ('en', 'fr', 'zh', 'es', 'de', 'other')
    )
);

-- Indexes for better query performance
CREATE INDEX idx_sources_source_type ON sources(source_type);
CREATE INDEX idx_sources_language ON sources(language);
CREATE INDEX idx_sources_is_active ON sources(is_active);
CREATE INDEX idx_sources_channel_id ON sources(channel_id) WHERE channel_id IS NOT NULL;
CREATE INDEX idx_sources_link ON sources(link);

-- Optional: Index on JSONB metadata for advanced queries
CREATE INDEX idx_sources_metadata ON sources USING gin(metadata);
```

### Column Descriptions

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL | Primary key, auto-incrementing |
| `name` | VARCHAR(255) | Name of the source (e.g., channel name) |
| `source_type` | VARCHAR(20) | Type: 'youtube', 'blog', 'ebook', 'rss' |
| `link` | VARCHAR(500) | URL to the source |
| `language` | VARCHAR(20) | Language code: 'en', 'fr', 'zh', 'es', 'de', 'other' |
| `channel_id` | VARCHAR(100) | YouTube channel ID (nullable, for YouTube sources) |
| `include_shorts` | BOOLEAN | Whether to include YouTube Shorts (YouTube sources only) |
| `metadata` | JSONB | Additional metadata as JSON object |
| `is_active` | BOOLEAN | Whether source is currently active |
| `last_collected` | TIMESTAMP | Last time content was collected (nullable) |
| `created_at` | TIMESTAMP | Record creation timestamp |
| `updated_at` | TIMESTAMP | Record last update timestamp |

### Example Data

```sql
INSERT INTO sources (name, source_type, link, language, channel_id, include_shorts, is_active)
VALUES 
    ('Little Chinese Everywhere', 'youtube', 'https://www.youtube.com/@littlechineseeverywhere/', 'en', 'UC1UNB6Gy11umcbEj_hqIwhw', FALSE, TRUE),
    ('Chinese Cooking Demystified', 'youtube', 'https://www.youtube.com/@ChineseCookingDemystified', 'en', 'UC54SLBnD5k5U3Q6N__UjbAw', FALSE, TRUE),
    ('Chine Chilla', 'youtube', 'https://www.youtube.com/@chinechilla/', 'fr', 'UCxnCAZ7Ykp-d7_T2K7LgrdA', TRUE, TRUE);
```

### Notes

- The `channel_id` field is optional but recommended for YouTube sources
- The `metadata` JSONB field can store additional information like:
  - Description
  - Tags
  - Custom fields specific to source type
  - Collection statistics
- The `include_shorts` field only applies to YouTube sources
- Use the indexes for efficient querying by source type, language, and active status

