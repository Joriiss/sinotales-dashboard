"""
Management command to update has_content field for all existing Content records.
Usage: python manage.py update_has_content
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from sources.models import Content


class Command(BaseCommand):
    help = 'Update has_content field for all Content records based on whether content text exists'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually updating',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        # Find all contents that have text but has_content is False
        contents_with_text = Content.objects.filter(
            ~Q(content__isnull=True) & ~Q(content='')
        ).exclude(content__regex=r'^\s*$')
        
        # Count how many need updating
        needs_update = contents_with_text.filter(has_content=False).count()
        needs_unset = Content.objects.filter(has_content=True).filter(
            Q(content__isnull=True) | Q(content='') | Q(content__regex=r'^\s*$')
        ).count()
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
            self.stdout.write(f'Records that would be set to has_content=True: {needs_update}')
            self.stdout.write(f'Records that would be set to has_content=False: {needs_unset}')
            return
        
        # Update has_content=True for records with content
        updated_true = contents_with_text.filter(has_content=False).update(has_content=True)
        
        # Update has_content=False for records without content
        updated_false = Content.objects.filter(has_content=True).filter(
            Q(content__isnull=True) | Q(content='') | Q(content__regex=r'^\s*$')
        ).update(has_content=False)
        
        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 50))
        self.stdout.write(self.style.SUCCESS('Update Summary:'))
        self.stdout.write(self.style.SUCCESS(f'  Set has_content=True: {updated_true}'))
        self.stdout.write(self.style.SUCCESS(f'  Set has_content=False: {updated_false}'))
        
        # Show current stats
        total = Content.objects.count()
        with_content = Content.objects.filter(has_content=True).count()
        without_content = Content.objects.filter(has_content=False).count()
        
        self.stdout.write(self.style.SUCCESS(f'\nCurrent Status:'))
        self.stdout.write(self.style.SUCCESS(f'  Total contents: {total}'))
        self.stdout.write(self.style.SUCCESS(f'  With content (has_content=True): {with_content}'))
        self.stdout.write(self.style.SUCCESS(f'  Without content (has_content=False): {without_content}'))
        self.stdout.write(self.style.SUCCESS('=' * 50))

