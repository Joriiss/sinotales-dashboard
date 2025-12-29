from django.core.management.base import BaseCommand
from django.db import transaction
from sources.models import BlogPost
from sources.views.utils import _parse_blog_content_sections
import re


class Command(BaseCommand):
    help = 'Find and fix blog posts with "SGE" in their summary title by removing it'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show which posts would be fixed without actually fixing them',
        )
        parser.add_argument(
            '--post-id',
            type=int,
            help='Fix only a specific post by ID',
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        post_id = options.get('post_id')
        
        # Find blog posts with "SGE" in summary title
        if post_id:
            blog_posts = BlogPost.objects.filter(id=post_id)
        else:
            blog_posts = BlogPost.objects.all()
        
        posts_to_fix = []
        
        self.stdout.write('Scanning blog posts for "SGE" in summary title...')
        
        for post in blog_posts:
            # Parse content to extract summary title
            sections = _parse_blog_content_sections(post.content)
            summary_title = sections.get('summary_title', '').strip()
            
            # Check if summary title contains "SGE" (case-insensitive)
            if 'SGE' in summary_title.upper():
                posts_to_fix.append({
                    'post': post,
                    'current_summary_title': summary_title,
                })
                self.stdout.write(
                    f'  Found: Post #{post.id} - "{post.title}" '
                    f'(Summary: "{summary_title}")'
                )
        
        if not posts_to_fix:
            self.stdout.write(self.style.SUCCESS('No posts found with "SGE" in summary title.'))
            return
        
        self.stdout.write(
            self.style.WARNING(
                f'\nFound {len(posts_to_fix)} post(s) to fix.'
            )
        )
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes will be made.'))
            for item in posts_to_fix:
                post = item['post']
                current_title = item['current_summary_title']
                # Show what the new title would be
                new_title = self._remove_sge_from_title(current_title)
                self.stdout.write(
                    f'  Post #{post.id}: "{current_title}" -> "{new_title}"'
                )
            return
        
        # Confirm before proceeding
        confirm = input(f'\nFix {len(posts_to_fix)} post(s)? (yes/no): ')
        if confirm.lower() != 'yes':
            self.stdout.write('Cancelled.')
            return
        
        # Fix each post
        success_count = 0
        error_count = 0
        
        for item in posts_to_fix:
            post = item['post']
            current_title = item['current_summary_title']
            
            self.stdout.write(f'\nFixing Post #{post.id}: "{post.title}"...')
            self.stdout.write(f'  Current summary title: "{current_title}"')
            
            try:
                # Remove "SGE" from the summary title in the content
                new_content = self._fix_summary_title_in_content(post.content, current_title)
                
                if new_content == post.content:
                    self.stdout.write(
                        self.style.WARNING('  No changes needed (title already fixed or not found in content).')
                    )
                else:
                    # Update the blog post content
                    post.content = new_content
                    post.save(update_fields=['content'])
                    
                    # Verify the fix
                    new_sections = _parse_blog_content_sections(post.content)
                    new_summary_title = new_sections.get('summary_title', '').strip()
                    
                    if 'SGE' in new_summary_title.upper():
                        self.stdout.write(
                            self.style.WARNING(
                                f'  WARNING: New summary title still contains "SGE": "{new_summary_title}"'
                            )
                        )
                    else:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  ✓ Fixed successfully. New summary: "{new_summary_title}"'
                            )
                        )
                    
                    success_count += 1
                    
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Error fixing Post #{post.id}: {str(e)}')
                )
                error_count += 1
                import traceback
                self.stdout.write(traceback.format_exc())
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Successfully fixed: {success_count}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
        self.stdout.write('='*60)
    
    def _remove_sge_from_title(self, title):
        """Remove 'SGE' and related text from the title"""
        # Remove "SGE" (case-insensitive) and common variations
        # Patterns to remove:
        # - "SGE" standalone
        # - "for SGE"
        # - ": SGE"
        # - "SGE:" 
        # - "SGE " (with space after)
        # - " SGE" (with space before)
        
        new_title = title
        
        # Remove "for SGE" (case-insensitive)
        new_title = re.sub(r'\s+for\s+SGE\s*', ' ', new_title, flags=re.IGNORECASE)
        new_title = re.sub(r'\s+For\s+SGE\s*', ' ', new_title, flags=re.IGNORECASE)
        
        # Remove ": SGE" or "SGE:"
        new_title = re.sub(r':\s*SGE\s*', ':', new_title, flags=re.IGNORECASE)
        new_title = re.sub(r'\s*SGE\s*:', ':', new_title, flags=re.IGNORECASE)
        
        # Remove standalone "SGE" (with spaces around)
        new_title = re.sub(r'\s+SGE\s+', ' ', new_title, flags=re.IGNORECASE)
        new_title = re.sub(r'\s+SGE\s*$', '', new_title, flags=re.IGNORECASE)
        new_title = re.sub(r'^\s*SGE\s+', '', new_title, flags=re.IGNORECASE)
        
        # Clean up multiple spaces
        new_title = re.sub(r'\s+', ' ', new_title)
        
        # Clean up leading/trailing spaces and colons
        new_title = new_title.strip()
        new_title = re.sub(r':\s*$', '', new_title)  # Remove trailing colon
        new_title = re.sub(r'^\s*:\s*', '', new_title)  # Remove leading colon
        
        return new_title.strip()
    
    def _fix_summary_title_in_content(self, content, old_title):
        """Fix the summary title in the HTML content"""
        if not content or not old_title:
            return content
        
        # Calculate the new title
        new_title = self._remove_sge_from_title(old_title)
        
        if new_title == old_title:
            # No change needed
            return content
        
        # Find and replace the title in various HTML contexts
        # 1. In H3 tags: <h3>Title</h3> or <h3><strong>Title</strong></h3>
        # 2. In H2 tags: <h2>Title</h2> or <h2><strong>Title</strong></h2>
        
        # Pattern to match H3 with the old title (handles HTML tags inside)
        h3_pattern = rf'<h3[^>]*>(.*?{re.escape(old_title)}.*?)</h3>'
        
        def replace_h3_title(match):
            h3_content = match.group(1)
            # Replace the old title with new title, preserving HTML structure
            new_h3_content = h3_content.replace(old_title, new_title)
            return f'<h3>{new_h3_content}</h3>'
        
        content = re.sub(h3_pattern, replace_h3_title, content, flags=re.IGNORECASE | re.DOTALL)
        
        # Pattern to match H2 with the old title
        h2_pattern = rf'<h2[^>]*>(.*?{re.escape(old_title)}.*?)</h2>'
        
        def replace_h2_title(match):
            h2_content = match.group(1)
            # Replace the old title with new title, preserving HTML structure
            new_h2_content = h2_content.replace(old_title, new_title)
            return f'<h2>{new_h2_content}</h2>'
        
        content = re.sub(h2_pattern, replace_h2_title, content, flags=re.IGNORECASE | re.DOTALL)
        
        return content

