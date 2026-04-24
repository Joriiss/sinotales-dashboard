"""
Management command to add meta title and/or meta description to blog posts
that are missing one or both.
"""
import os
import re
import time
from django.conf import settings as django_settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from sources.models import BlogPost, Settings
from sources.rag_service import RAGService
from tqdm import tqdm


def _extract_meta_from_response(generated_metadata, blog_post, text_content):
    """
    Extract meta_title and meta_description from LLM response.
    Returns (meta_title, meta_description); uses fallbacks for missing values.
    Only overwrites values that are currently empty on blog_post.
    """
    need_title = not (blog_post.meta_title and blog_post.meta_title.strip())
    need_desc = not (blog_post.meta_description and blog_post.meta_description.strip())
    meta_title = blog_post.meta_title or ''
    meta_description = blog_post.meta_description or ''

    if need_title:
        patterns = [
            r'\*\*Meta Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
            r'Meta [Tt]itle\s*:?\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
            r'Title [Tt]ag\s*:?\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
            r'Meta Title:\s*(.+?)(?:\n|$)',
            r'Title:\s*(.+?)(?=\n\*\*|\n\n|\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE)
            if match:
                meta_title = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                if meta_title:
                    meta_title = meta_title[:60] if len(meta_title) > 60 else meta_title
                    break
        if not (meta_title and meta_title.strip()):
            meta_title = (blog_post.title or '')[:60]

    if need_desc:
        patterns = [
            r'\*\*Meta Description:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Meta [Dd]escription\s*:?\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Meta Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
            r'Description:\s*(.+?)(?=\n\*\*|\n\n|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
            if match:
                meta_description = re.sub(r'\*\*|\*|\[|\]|`', '', match.group(1).strip())
                meta_description = ' '.join(meta_description.split())
                if meta_description:
                    meta_description = meta_description[:160] if len(meta_description) > 160 else meta_description
                    break
        if not (meta_description and meta_description.strip()):
            fallback_desc = (text_content or '').strip()[:160]
            meta_description = fallback_desc or (blog_post.title or '')[:160]

    return meta_title, meta_description


class Command(BaseCommand):
    help = 'Add meta title and/or meta description to blog posts that are missing one or both'

    def add_arguments(self, parser):
        parser.add_argument(
            '--provider',
            type=str,
            default='gemini',
            choices=['ollama', 'openai', 'gemini'],
            help='AI provider to use (default: gemini)',
        )
        parser.add_argument(
            '--model',
            type=str,
            default=None,
            help='Model name (default: provider-specific)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Max number of posts to process',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be set without saving',
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=0.5,
            help='Delay between API calls in seconds (default: 0.5)',
        )

    def handle(self, *args, **options):
        provider = options['provider']
        model = options['model']
        limit = options['limit']
        dry_run = options['dry_run']
        delay = options['delay']

        if not model:
            if provider == 'ollama':
                try:
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'

        # Posts that have content and are missing meta_title and/or meta_description
        queryset = BlogPost.objects.filter(
            Q(content__isnull=False) & ~Q(content='')
        ).filter(
            Q(meta_title__isnull=True) | Q(meta_title='') |
            Q(meta_description__isnull=True) | Q(meta_description='')
        ).order_by('id')

        if limit:
            queryset = queryset[:limit]

        total = queryset.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No blog posts found missing meta title or description.'))
            return

        self.stdout.write(f'Found {total} post(s) missing meta title and/or description.')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be saved'))
        self.stdout.write(f'Provider: {provider}, model: {model}\n')

        prompt_path = os.path.join(django_settings.BASE_DIR, 'prompt-metadata-generator')
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR('Prompt file not found: prompt-metadata-generator'))
            return

        try:
            rag_service = RAGService()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'RAG service init failed: {e}'))
            return

        success = 0
        errors = 0
        start = time.time()

        with tqdm(total=total, desc='Filling meta', unit='post', ncols=120) as pbar:
            for blog_post in queryset:
                try:
                    text_content = re.sub(r'<[^>]+>', ' ', blog_post.content)
                    text_content = ' '.join(text_content.split())
                    prompt = prompt_template.replace('[PASTE YOUR GENERATED HTML CONTENT HERE]', text_content)

                    if provider == 'ollama':
                        generated = rag_service._call_ollama(prompt, model, max_tokens=2000)
                    elif provider == 'openai':
                        generated = rag_service._call_openai(prompt, model, max_tokens=2000)
                    else:
                        generated = rag_service._call_gemini(prompt, model, max_tokens=4000)

                    new_title, new_desc = _extract_meta_from_response(generated, blog_post, text_content)

                    update_fields = []
                    need_title = not (blog_post.meta_title and blog_post.meta_title.strip())
                    need_desc = not (blog_post.meta_description and blog_post.meta_description.strip())
                    if need_title:
                        blog_post.meta_title = new_title
                        update_fields.append('meta_title')
                    if need_desc:
                        blog_post.meta_description = new_desc
                        update_fields.append('meta_description')
                    if dry_run:
                        parts = []
                        if need_title:
                            parts.append(f'meta_title: {new_title[:45]}...')
                        if need_desc:
                            parts.append(f'meta_description: {new_desc[:50]}...')
                        self.stdout.write(f'  [{blog_post.pk}] {blog_post.title[:48]}... -> {", ".join(parts)}')
                    elif update_fields:
                        blog_post.save(update_fields=update_fields)

                    success += 1
                except Exception as e:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f'  Post {blog_post.pk} ({blog_post.title[:40]}...): {e}'))
                pbar.update(1)
                if delay and not dry_run:
                    time.sleep(delay)

        elapsed = time.time() - start
        self.stdout.write(
            self.style.SUCCESS(f'\nDone. Updated: {success}, errors: {errors}, time: {elapsed:.1f}s')
        )
