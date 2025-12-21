"""
Management command to generate FAQ titles for blog posts that have FAQs but no FAQ title
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from sources.models import BlogPost
from sources.rag_service import RAGService
from sources.utils import log_activity
import re
import time
from tqdm import tqdm


class Command(BaseCommand):
    help = 'Generate FAQ titles for blog posts that have FAQs but no FAQ title'
    
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
            help='Model name (default: provider-specific default)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of blog posts to process',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be generated without actually saving',
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=0.5,
            help='Delay between requests in seconds (default: 0.5)',
        )
    
    def handle(self, *args, **options):
        provider = options['provider']
        model = options['model']
        limit = options['limit']
        dry_run = options['dry_run']
        delay = options['delay']
        
        # Get default model if not provided
        if not model:
            if provider == 'ollama':
                try:
                    from sources.models import Settings
                    app_settings = Settings.get_settings()
                    model = app_settings.default_tagging_model
                except Exception:
                    model = 'gpt-oss:20b-cloud'
            elif provider == 'openai':
                model = 'gpt-4o-mini'
            elif provider == 'gemini':
                model = 'gemini-3-pro-preview'
        
        # Find blog posts with FAQs but no FAQ title
        queryset = BlogPost.objects.filter(
            Q(faq__isnull=False) & ~Q(faq=[]) & (Q(faq_title__isnull=True) | Q(faq_title=''))
        )
        
        if limit:
            queryset = queryset[:limit]
        
        total = queryset.count()
        
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No blog posts found that need FAQ titles.'))
            return
        
        self.stdout.write(f'\nFound {total} blog post(s) that need FAQ titles')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        self.stdout.write(f'Using provider: {provider}, model: {model}\n')
        
        # Initialize RAG service
        try:
            rag_service = RAGService()
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to initialize RAG service: {str(e)}')
            )
            return
        
        # Create FAQ title generation prompt (concise to reduce token usage)
        faq_title_prompt_template = """Generate a compelling FAQ section title (3-8 words) for a China travel blog post.

Blog Post Title: {title}
FAQ Questions: {faqs}

Examples: "Frequently Asked Questions", "Common Questions About [Topic]", "Your Questions Answered"

Provide ONLY the title, no formatting or labels:"""

        # Process blog posts
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        start_time = time.time()
        
        with tqdm(total=total, desc="Generating FAQ titles", unit="post", ncols=120) as pbar:
            for blog_post in queryset:
                try:
                    # Format existing FAQs (only questions, no answers to reduce token usage)
                    faqs_text = ""
                    if blog_post.faq and isinstance(blog_post.faq, list):
                        questions = []
                        for faq_item in blog_post.faq[:4]:
                            if isinstance(faq_item, dict) and 'question' in faq_item:
                                questions.append(faq_item['question'])
                        faqs_text = "; ".join(questions) if questions else "No FAQs provided"
                    
                    # Build prompt (simplified - no content preview to reduce tokens)
                    prompt = faq_title_prompt_template.format(
                        title=blog_post.title,
                        faqs=faqs_text
                    )
                    
                    # Generate FAQ title
                    if provider == 'ollama':
                        generated_text = rag_service._call_ollama(prompt, model, max_tokens=100)
                    elif provider == 'openai':
                        generated_text = rag_service._call_openai(prompt, model, max_tokens=100)
                    elif provider == 'gemini':
                        # Gemini models (especially gemini-3-pro) use "thoughts" tokens, so we need more headroom
                        # Use 2000 tokens to ensure we have enough space for thoughts + response
                        generated_text = rag_service._call_gemini(prompt, model, max_tokens=2000)
                    else:
                        raise ValueError(f"Unknown provider: {provider}")
                    
                    # Extract FAQ title - clean up the response
                    faq_title = generated_text.strip()
                    # Remove markdown formatting if present
                    faq_title = re.sub(r'\*\*|\*|\[|\]|`|#', '', faq_title).strip()
                    # Remove common prefixes like "FAQ Section Title:" or "Title:"
                    faq_title = re.sub(r'^(FAQ\s*Section\s*Title|Title|FAQ\s*Title):\s*', '', faq_title, flags=re.IGNORECASE).strip()
                    # Remove newlines and extra spaces
                    faq_title = ' '.join(faq_title.split())
                    # Limit length
                    if len(faq_title) > 200:
                        faq_title = faq_title[:197] + '...'
                    
                    if faq_title and len(faq_title) > 0:
                        if not dry_run:
                            blog_post.faq_title = faq_title
                            blog_post.save(update_fields=['faq_title'])
                            
                            # Log activity
                            log_activity(
                                'blog_post_updated',
                                f'FAQ title generated for blog post "{blog_post.title}"',
                                metadata={
                                    'blog_post_id': blog_post.id,
                                    'faq_title': faq_title,
                                    'provider': provider,
                                    'model': model
                                }
                            )
                        
                        success_count += 1
                        pbar.set_postfix({
                            "status": "generated",
                            "title": faq_title[:30] if len(faq_title) > 30 else faq_title,
                            "post": blog_post.title[:20]
                        })
                    else:
                        skipped_count += 1
                        pbar.set_postfix({
                            "status": "skipped",
                            "reason": "empty title",
                            "post": blog_post.title[:20]
                        })
                    
                except Exception as e:
                    error_count += 1
                    error_msg = str(e)[:50]
                    pbar.set_postfix({
                        "status": f"error: {error_msg}",
                        "post": blog_post.title[:20] if hasattr(blog_post, 'title') else 'unknown'
                    })
                    self.stdout.write(
                        self.style.ERROR(f'\nError processing "{blog_post.title}": {str(e)}')
                    )
                
                pbar.update(1)
                
                # Delay between requests
                if delay > 0:
                    time.sleep(delay)
        
        # Summary
        elapsed = time.time() - start_time
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write('='*60)
        self.stdout.write(f'Total processed: {total}')
        self.stdout.write(f'Successfully generated: {success_count}')
        self.stdout.write(f'Skipped: {skipped_count}')
        self.stdout.write(f'Errors: {error_count}')
        self.stdout.write(f'Time elapsed: {elapsed/60:.1f} minutes')
        if success_count > 0:
            self.stdout.write(f'Average time per post: {elapsed/success_count:.1f} seconds')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were saved'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✓ FAQ title generation complete!'))

