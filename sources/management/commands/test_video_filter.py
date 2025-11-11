"""
Management command to test video filtering for China relevance
"""
from django.core.management.base import BaseCommand, CommandError
from typing import List
from sources.youtube_service import get_channel_videos, is_video_relevant_to_china, get_youtube_api_key


class Command(BaseCommand):
    help = 'Test video filtering for China relevance on a YouTube channel'

    def add_arguments(self, parser):
        parser.add_argument(
            'channel_id',
            type=str,
            help='YouTube channel ID to test'
        )
        parser.add_argument(
            '--max-videos',
            type=int,
            default=20,
            help='Maximum number of videos to test (default: 20)'
        )
        parser.add_argument(
            '--include-shorts',
            action='store_true',
            help='Include YouTube Shorts in the test'
        )

    def handle(self, *args, **options):
        channel_id = options['channel_id']
        max_videos = options['max_videos']
        include_shorts = options['include_shorts']

        # Check API key
        api_key = get_youtube_api_key()
        if not api_key:
            raise CommandError(
                'YouTube API key is required. Set YOUTUBE_API_KEY in settings or environment.'
            )

        self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
        self.stdout.write(self.style.SUCCESS(f'Testing Video Filter for Channel: {channel_id}'))
        self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))

        try:
            # Fetch videos without filtering
            self.stdout.write('Fetching videos from YouTube...')
            videos = get_channel_videos(
                channel_id=channel_id,
                include_shorts=include_shorts,
                filter_china=False,  # Don't filter, we want to test all
                api_key=api_key
            )

            if not videos:
                self.stdout.write(self.style.WARNING('No videos found for this channel.'))
                return

            # Limit to max_videos
            videos = videos[:max_videos]

            self.stdout.write(self.style.SUCCESS(f'Found {len(videos)} video(s) to test\n'))

            # Test each video
            relevant_count = 0
            not_relevant_count = 0

            for i, video in enumerate(videos, 1):
                video_id = video['video_id']
                title = video['title']
                description_full = video.get('description', '')  # Full description for testing
                description_preview = description_full[:200]  # Truncated for display only
                tags = video.get('tags', [])

                # Check relevance using FULL description (same as actual filter)
                is_relevant = is_video_relevant_to_china(title, description_full, tags)

                # Find which keyword matched (using full description)
                matched_keywords = self._find_matched_keywords(title, description_full, tags)

                # Display result
                self.stdout.write(f'\n[{i}/{len(videos)}] {title[:60]}{"..." if len(title) > 60 else ""}')
                self.stdout.write(f'  Video ID: {video_id}')
                self.stdout.write(f'  Link: {video.get("link", "N/A")}')

                if is_relevant:
                    relevant_count += 1
                    self.stdout.write(self.style.SUCCESS(f'  ✓ RELEVANT'))
                    if matched_keywords:
                        self.stdout.write(f'  Matched keywords: {", ".join(matched_keywords)}')
                else:
                    not_relevant_count += 1
                    self.stdout.write(self.style.WARNING(f'  ✗ NOT RELEVANT'))

                if description_preview:
                    self.stdout.write(f'  Description preview: {description_preview}...')
                if tags:
                    self.stdout.write(f'  Tags: {", ".join(tags[:5])}{"..." if len(tags) > 5 else ""}')

            # Summary
            self.stdout.write(self.style.SUCCESS(f'\n{"="*80}'))
            self.stdout.write(self.style.SUCCESS('Summary:'))
            self.stdout.write(f'  Total videos tested: {len(videos)}')
            self.stdout.write(self.style.SUCCESS(f'  Relevant: {relevant_count}'))
            self.stdout.write(self.style.WARNING(f'  Not relevant: {not_relevant_count}'))
            if len(videos) > 0:
                percentage = (relevant_count / len(videos)) * 100
                self.stdout.write(f'  Relevance rate: {percentage:.1f}%')
            self.stdout.write(self.style.SUCCESS(f'{"="*80}\n'))

        except Exception as e:
            raise CommandError(f'Error: {str(e)}')

    def _find_matched_keywords(self, title: str, description: str = '', tags: List[str] = None) -> List[str]:
        """
        Find which China-related keywords matched in the video.
        
        Returns:
            List of matched keywords
        """
        if tags is None:
            tags = []

        # Use the same keywords as in youtube_service.py
        # (duplicated here for testing purposes to show which keywords matched)
        import re
        
        # Single-word keywords that need word boundaries
        single_word_keywords = [
            'china', 'chinese', 'chinois', 'chine',
            'beijing', 'peking', 'pékin',
            'shanghai', 'shanghaï',
            'guangzhou', 'canton',
            'shenzhen',
            'taiwan', 'taipei',
            'tibet', 'tibetan', 'tibetain',
            'xinjiang', 'xingjiang',
            'terracotta',
            'yangtze',
            'confucius', 'confucian',
            'buddhism', 'buddhist',
            'daoism', 'taoism',
            'mandarin', 'putonghua',
            'cantonese',
            'han',
            'ming',
            'qing',
            'tang',
            'song',
            'yuan',
            'mao',
            'ccp',
            'panda',
            'dragon', 'phoenix',
            'kungfu',
            'dumpling', 'wonton',
            'zhongguo', '中国', '中文',
        ]
        
        # Multi-word phrases
        multi_word_keywords = [
            'hong kong', 'hongkong', 'hong-kong',
            'great wall', 'greatwall',
            'forbidden city', 'forbiddencity',
            'terracotta army',
            'yangtze river',
            'yellow river', 'huang he',
            'han chinese',
            'mao zedong',
            'communist party',
            'giant panda',
            'silk road', 'silkroad',
            'kung fu', 'martial arts',
            'dim sum',
            'tea ceremony', 'chinese tea',
            'chinese new year', 'lunar new year',
            'spring festival',
        ]

        search_text = f"{title} {description} {' '.join(tags)}".lower()
        matched = []

        # Check single-word keywords with word boundaries
        for keyword in single_word_keywords:
            pattern = r'\b' + re.escape(keyword.lower()) + r'\b'
            if re.search(pattern, search_text, re.IGNORECASE):
                matched.append(keyword)
        
        # Check multi-word phrases
        for keyword in multi_word_keywords:
            if keyword.lower() in search_text:
                matched.append(keyword)

        return matched

