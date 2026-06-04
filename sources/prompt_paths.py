"""Resolve prompt template file paths (supports legacy names without extension)."""
import os
from django.conf import settings

METADATA_PROMPT_FILENAMES = (
    'prompt-metadata-generator.md',
    'prompt-metadata-generator',
)


def resolve_metadata_prompt_path() -> str | None:
    """Return the path to the metadata prompt file, or None if not found."""
    base_dir = settings.BASE_DIR
    for filename in METADATA_PROMPT_FILENAMES:
        path = os.path.join(base_dir, filename)
        if os.path.isfile(path):
            return path
    return None
