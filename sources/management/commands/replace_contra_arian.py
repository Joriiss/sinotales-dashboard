from django.core.management.base import BaseCommand
from django.db import transaction
from sources.models import BlogPost
import re


class Command(BaseCommand):
    help = 'Replace "contra-arian" with "contrarian" in blog post content'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show which posts would be updated without actually updating them',
        )
        parser.add_argument(
            '--post-id',
            type=int,
            help='Update only a specific post by ID',
        )
        parser.add_argument(
            '--case-sensitive',
            action='store_true',
            help='Perform case-sensitive replacement (default: case-insensitive)',
        )
    
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        post_id = options.get('post_id')
        case_sensitive = options['case_sensitive']
        
        # Find blog posts with "contra-arian" in content
        if post_id:
            blog_posts = BlogPost.objects.filter(id=post_id)
        else:
            blog_posts = BlogPost.objects.all()
        
        posts_to_update = []
        
        self.stdout.write('Scanning blog posts for "contra-arian"...')
        
        # Build search pattern
        if case_sensitive:
            search_pattern = r'contra-arian'
            replace_text = 'contrarian'
        else:
            search_pattern = r'contra-arian'
            replace_text = 'contrarian'
        
        for post in blog_posts:
            # Search for "contra-arian" (case-insensitive by default)
            if case_sensitive:
                if 'contra-arian' in post.content:
                    posts_to_update.append(post)
            else:
                if re.search(r'contra-arian', post.content, re.IGNORECASE):
                    posts_to_update.append(post)
        
        if not posts_to_update:
            self.stdout.write(self.style.SUCCESS('No posts found with "contra-arian" in content.'))
            return
        
        self.stdout.write(
            self.style.WARNING(
                f'\nFound {len(posts_to_update)} post(s) with "contra-arian" in content.'
            )
        )
        
        # Show what will be changed
        for post in posts_to_update:
            # Count occurrences
            if case_sensitive:
                count = post.content.count('contra-arian')
            else:
                count = len(re.findall(r'contra-arian', post.content, re.IGNORECASE))
            
            self.stdout.write(
                f'  Post #{post.id}: "{post.title}" '
                f'({count} occurrence(s))'
            )
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes will be made.'))
            return
        
        # Confirm before proceeding
        confirm = input(f'\nReplace "contra-arian" with "contrarian" in {len(posts_to_update)} post(s)? (yes/no): ')
        if confirm.lower() != 'yes':
            self.stdout.write('Cancelled.')
            return
        
        # Update each post
        success_count = 0
        error_count = 0
        total_replacements = 0
        
        for post in posts_to_update:
            self.stdout.write(f'\nUpdating Post #{post.id}: "{post.title}"...')
            
            try:
                with transaction.atomic():
                    # Count occurrences before replacement
                    if case_sensitive:
                        before_count = post.content.count('contra-arian')
                        new_content = post.content.replace('contra-arian', 'contrarian')
                    else:
                        # Case-insensitive replacement
                        before_count = len(re.findall(r'contra-arian', post.content, re.IGNORECASE))
                        new_content = re.sub(
                            r'contra-arian',
                            'contrarian',
                            post.content,
                            flags=re.IGNORECASE
                        )
                    
                    if new_content != post.content:
                        post.content = new_content
                        post.save(update_fields=['content'])
                        
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  ✓ Replaced {before_count} occurrence(s)'
                            )
                        )
                        total_replacements += before_count
                        success_count += 1
                    else:
                        self.stdout.write(
                            self.style.WARNING('  No changes needed.')
                        )
                    
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ✗ Error updating Post #{post.id}: {str(e)}')
                )
                error_count += 1
                import traceback
                self.stdout.write(traceback.format_exc())
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Successfully updated: {success_count}'))
        self.stdout.write(self.style.SUCCESS(f'Total replacements: {total_replacements}'))
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {error_count}'))
        self.stdout.write('='*60)

