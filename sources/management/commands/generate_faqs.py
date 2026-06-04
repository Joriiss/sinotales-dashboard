"""
Management command to generate FAQs for blog posts that don't have FAQs yet
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from sources.models import BlogPost
from sources.rag_service import RAGService
from sources.utils import log_activity
import re
import json
import time
from tqdm import tqdm
import os
from datetime import datetime
from django.conf import settings


class Command(BaseCommand):
    help = 'Generate FAQs for blog posts that have content but no FAQs'
    
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
            from sources.llm_models import get_default_model_for_provider
            model = get_default_model_for_provider(provider)
        
        # Find blog posts with content but no FAQs
        queryset = BlogPost.objects.filter(
            Q(content__isnull=False) & ~Q(content='') &
            (Q(faq__isnull=True) | Q(faq=[]) | Q(faq='[]'))
        )
        
        if limit:
            queryset = queryset[:limit]
        
        total = queryset.count()
        
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No blog posts found that need FAQs.'))
            return
        
        self.stdout.write(f'\nFound {total} blog post(s) that need FAQs')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))
        self.stdout.write(f'Using provider: {provider}, model: {model}\n')
        
        from sources.prompt_paths import resolve_metadata_prompt_path

        prompt_file_path = resolve_metadata_prompt_path()
        if not prompt_file_path:
            self.stdout.write(
                self.style.ERROR('Prompt template file not found: prompt-metadata-generator.md')
            )
            return
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            metadata_prompt_template = f.read()
        
        # Initialize RAG service
        try:
            rag_service = RAGService()
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to initialize RAG service: {str(e)}')
            )
            return
        
        # Process blog posts
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        start_time = time.time()
        
        with tqdm(total=total, desc="Generating FAQs", unit="post", ncols=120) as pbar:
            for blog_post in queryset:
                try:
                    # Strip HTML tags from content for metadata generation
                    text_content = re.sub(r'<[^>]+>', ' ', blog_post.content)
                    text_content = ' '.join(text_content.split())
                    
                    if not text_content or len(text_content.strip()) < 100:
                        skipped_count += 1
                        pbar.set_postfix({
                            "status": "skipped",
                            "reason": "insufficient content",
                            "post": blog_post.title[:20]
                        })
                        pbar.update(1)
                        continue
                    
                    # Build metadata prompt with current year
                    current_year = str(datetime.now().year)
                    metadata_prompt = metadata_prompt_template.replace('{current_year}', current_year).replace('[PASTE YOUR GENERATED HTML CONTENT HERE]', text_content)
                    
                    # Generate metadata
                    if provider == 'ollama':
                        generated_metadata = rag_service._call_ollama(metadata_prompt, model, max_tokens=2000)
                    elif provider == 'openai':
                        generated_metadata = rag_service._call_openai(metadata_prompt, model, max_tokens=2000)
                    elif provider == 'gemini':
                        # Gemini models (especially gemini-3-pro) use "thoughts" tokens, so we need more headroom
                        generated_metadata = rag_service._call_gemini(metadata_prompt, model, max_tokens=4000)
                    else:
                        raise ValueError(f"Unknown provider: {provider}")
                    
                    # Extract FAQ Section Title
                    faq_title = None
                    patterns = [
                        r'\*\*FAQ Section Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                        r'FAQ Section Title:\s*(.+?)(?=\n\*\*|\n\n|$)',
                        r'\*\*FAQ Title:\*\*\s*(.+?)(?=\n\*\*|\n\n|$)',
                        r'FAQ Title:\s*(.+?)(?=\n\*\*|\n\n|$)',
                    ]
                    for pattern in patterns:
                        faq_title_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                        if faq_title_match:
                            faq_title = faq_title_match.group(1).strip()
                            faq_title = re.sub(r'\*\*|\*|\[|\]|`', '', faq_title).strip()
                            faq_title = ' '.join(faq_title.split())
                            if faq_title and len(faq_title) <= 200:
                                break
                    
                    # Extract FAQ - try multiple patterns
                    valid_faq = []
                    # Try to find FAQ section - look for FAQ: followed by JSON array (may span multiple lines)
                    patterns = [
                        r'\*\*FAQ:\*\*\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
                        r'FAQ:\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
                        r'\*\*FAQ\*\*\s*(\[.*?\])\s*(?=\n\*\*|\n\n|$)',
                        r'\*\*FAQ:\*\*\s*(\[[\s\S]*?\])',  # More flexible for multi-line
                        r'FAQ:\s*(\[[\s\S]*?\])',  # More flexible for multi-line
                    ]
                    for pattern in patterns:
                        faq_match = re.search(pattern, generated_metadata, re.IGNORECASE | re.DOTALL)
                        if faq_match:
                            faq_text = faq_match.group(1).strip()
                            try:
                                # Try to parse as JSON
                                faq_data = json.loads(faq_text)
                                # Validate it's a list with proper structure
                                if isinstance(faq_data, list) and len(faq_data) > 0:
                                    # Ensure each item has question and answer
                                    for item in faq_data[:4]:  # Limit to 4 items
                                        if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                            valid_faq.append({
                                                'question': str(item['question']).strip(),
                                                'answer': str(item['answer']).strip()
                                            })
                                    # Accept any number of valid FAQ items (1-4)
                                    if len(valid_faq) > 0:
                                        break
                            except (json.JSONDecodeError, ValueError, KeyError) as e:
                                # If JSON parsing fails, try next pattern
                                continue
                    
                    # If FAQ wasn't found with patterns, try to find JSON array anywhere after "FAQ:"
                    if len(valid_faq) == 0:
                        # Find the position of "FAQ:" in the text
                        faq_label_match = re.search(r'FAQ:', generated_metadata, re.IGNORECASE)
                        if faq_label_match:
                            # Get text after FAQ:
                            text_after_faq = generated_metadata[faq_label_match.end():]
                            # Try to find a JSON array - look for opening bracket and try to match closing bracket
                            bracket_start = text_after_faq.find('[')
                            if bracket_start != -1:
                                # Find matching closing bracket by counting brackets
                                bracket_count = 0
                                bracket_end = -1
                                for i in range(bracket_start, len(text_after_faq)):
                                    if text_after_faq[i] == '[':
                                        bracket_count += 1
                                    elif text_after_faq[i] == ']':
                                        bracket_count -= 1
                                        if bracket_count == 0:
                                            bracket_end = i + 1
                                            break
                                
                                if bracket_end > bracket_start:
                                    faq_text = text_after_faq[bracket_start:bracket_end].strip()
                                    try:
                                        faq_data = json.loads(faq_text)
                                        if isinstance(faq_data, list) and len(faq_data) > 0:
                                            for item in faq_data[:4]:
                                                if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                                    valid_faq.append({
                                                        'question': str(item['question']).strip(),
                                                        'answer': str(item['answer']).strip()
                                                    })
                                    except (json.JSONDecodeError, ValueError, KeyError):
                                        pass
                    
                    # Save FAQs if found
                    if len(valid_faq) > 0:
                        # Ensure it's in the correct format
                        valid_faq = []
                        for item in faq_data[:4]:
                            if isinstance(item, dict) and 'question' in item and 'answer' in item:
                                valid_faq.append({
                                    'question': str(item['question']).strip(),
                                    'answer': str(item['answer']).strip()
                                })
                        
                        if not dry_run:
                            blog_post.faq = valid_faq
                            if faq_title:
                                blog_post.faq_title = faq_title
                            blog_post.save(update_fields=['faq', 'faq_title'])
                            
                            # Log activity
                            log_activity(
                                'blog_post_updated',
                                f'FAQs generated for blog post "{blog_post.title}"',
                                metadata={
                                    'blog_post_id': blog_post.id,
                                    'faq_count': len(valid_faq),
                                    'faq_title': faq_title,
                                    'provider': provider,
                                    'model': model
                                }
                            )
                        
                        success_count += 1
                        pbar.set_postfix({
                            "status": "generated",
                            "faqs": len(valid_faq),
                            "post": blog_post.title[:20]
                        })
                    else:
                        skipped_count += 1
                        pbar.set_postfix({
                            "status": "skipped",
                            "reason": "no FAQs found",
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
            self.stdout.write(self.style.SUCCESS('\n✓ FAQ generation complete!'))

