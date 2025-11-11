"""
Management command to test YouTube API connectivity and functionality.
Usage: 
    python manage.py test_youtube_api <channel_id>
    python manage.py test_youtube_api <channel_id> --max-videos 5
"""
from django.core.management.base import BaseCommand, CommandError
from sources.youtube_service import get_channel_videos, get_youtube_api_key


class Command(BaseCommand):
    help = 'Test YouTube API connectivity and fetch videos from a channel'

    def add_arguments(self, parser):
        parser.add_argument(
            'channel_id',
            type=str,
            help='YouTube channel ID to test'
        )
        parser.add_argument(
            '--max-videos',
            type=int,
            default=5,
            help='Maximum number of videos to fetch (default: 5)'
        )
        parser.add_argument(
            '--include-shorts',
            action='store_true',
            help='Include YouTube Shorts (videos under 90 seconds)'
        )

    def handle(self, *args, **options):
        channel_id = options['channel_id']
        max_videos = options['max_videos']
        include_shorts = options['include_shorts']
        
        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS("YouTube API Test"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))
        
        # Step 1: Check API key
        self.stdout.write("Step 1: Checking YouTube API key...")
        api_key = get_youtube_api_key()
        if not api_key:
            raise CommandError(
                "YouTube API key not found. Set YOUTUBE_API_KEY in settings or environment."
            )
        self.stdout.write(self.style.SUCCESS(f"  ✓ API key found: {api_key[:10]}...{api_key[-4:]}"))
        
        # Step 2: Test fetching videos
        self.stdout.write(f"\nStep 2: Fetching videos from channel: {channel_id}")
        self.stdout.write(f"  Max videos: {max_videos}")
        self.stdout.write(f"  Include shorts: {include_shorts}")
        self.stdout.write("")
        
        try:
            videos = get_channel_videos(
                channel_id=channel_id,
                include_shorts=include_shorts,
                fetch_details=False,
                max_videos=max_videos
            )
            
            self.stdout.write(self.style.SUCCESS(f"\n✓ Successfully fetched {len(videos)} video(s)\n"))
            
            # Display results
            if videos:
                self.stdout.write("Videos fetched:")
                for i, video in enumerate(videos, 1):
                    self.stdout.write(f"\n  [{i}] {video['title']}")
                    self.stdout.write(f"      ID: {video['video_id']}")
                    self.stdout.write(f"      Date: {video['upload_date']}")
                    self.stdout.write(f"      Duration: {video['duration']} seconds")
                    self.stdout.write(f"      Link: {video['link']}")
            else:
                self.stdout.write(self.style.WARNING("  No videos found"))
            
            self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
            self.stdout.write(self.style.SUCCESS("Test completed successfully!"))
            self.stdout.write(self.style.SUCCESS(f"{'='*60}\n"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n✗ Error: {str(e)}"))
            self.stdout.write(self.style.ERROR(f"{'='*60}\n"))
            raise CommandError(f"YouTube API test failed: {str(e)}")

