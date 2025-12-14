"""
Management command to find post ideas similar to a given idea.
Takes a post idea (by ID or title text) and finds the most similar existing ideas.
"""
from django.core.management.base import BaseCommand, CommandError
from sources.models import PostIdea
from sources.embedding_service import EmbeddingService
from pgvector.django import CosineDistance


class Command(BaseCommand):
    help = 'Find post ideas similar to a given idea (by ID or title text)'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'idea',
            type=str,
            help='Post idea ID or title text to find similar ideas for',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Maximum number of similar ideas to return (default: 10)',
        )
        parser.add_argument(
            '--min-similarity',
            type=float,
            default=0.0,
            help='Minimum similarity to show (0.0-1.0). Only show ideas with similarity >= this value (default: 0.0)',
        )
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.92,
            help='Similarity threshold for duplicate detection (0.0-1.0). Ideas >= this are considered duplicates (default: 0.92)',
        )
    
    def handle(self, *args, **options):
        idea_input = options['idea']
        limit = options['limit']
        min_similarity = options['min_similarity']
        threshold = options['threshold']
        
        # Validate thresholds
        if not 0.0 <= min_similarity <= 1.0:
            raise CommandError('min-similarity must be between 0.0 and 1.0')
        
        if not 0.0 <= threshold <= 1.0:
            raise CommandError('threshold must be between 0.0 and 1.0')
        
        # Initialize embedding service
        try:
            embedding_service = EmbeddingService()
        except (ValueError, ImportError) as e:
            raise CommandError(f'Failed to initialize embedding service: {str(e)}\nMake sure OPENAI_API_KEY is set in settings.')
        
        # Try to find the idea by ID first, then by title
        target_idea = None
        target_embedding = None
        target_title = None
        
        # Check if input is numeric (ID)
        if idea_input.isdigit():
            try:
                target_idea = PostIdea.objects.get(pk=int(idea_input))
                target_title = target_idea.title
                # Check if embedding exists (can't use boolean check on numpy arrays)
                if target_idea.title_embedding is not None:
                    target_embedding = target_idea.title_embedding
                    self.stdout.write(
                        self.style.SUCCESS(f'Found post idea by ID: {target_idea.id}')
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f'Post idea ID {target_idea.id} found but has no embedding. Generating...')
                    )
                    target_embedding = embedding_service.generate_embedding(target_idea.title)
                    if target_embedding is not None:
                        target_idea.title_embedding = target_embedding
                        target_idea.save(update_fields=['title_embedding'])
                        self.stdout.write(self.style.SUCCESS('Embedding generated and saved.'))
            except PostIdea.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(f'No post idea found with ID {idea_input}. Treating as title text.')
                )
        
        # If not found by ID, treat as title text
        if not target_idea:
            target_title = idea_input
            self.stdout.write(
                self.style.SUCCESS(f'Using provided title text: "{target_title}"')
            )
            # Generate embedding for the title
            target_embedding = embedding_service.generate_embedding(target_title)
            if target_embedding is None:
                raise CommandError('Failed to generate embedding for the provided title.')
        
        if target_embedding is None:
            raise CommandError('Could not get or generate embedding for the target idea.')
        
        # Find similar ideas using vector similarity search
        # CosineDistance: 0 = identical, 2 = opposite
        # similarity = 1.0 - distance
        max_distance = 2.0 - min_similarity  # Convert min_similarity to max_distance
        
        # Build query - exclude the target idea if it exists in DB
        query = PostIdea.objects.filter(
            title_embedding__isnull=False
        )
        
        if target_idea:
            query = query.exclude(id=target_idea.id)
        
        similar_ideas = query.annotate(
            distance=CosineDistance('title_embedding', target_embedding)
        ).filter(
            distance__lte=max_distance
        ).order_by('distance')[:limit]
        
        # Display results
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS(f'Finding ideas similar to: "{target_title}"'))
        if target_idea:
            self.stdout.write(f'Target ID: {target_idea.id}')
        self.stdout.write('=' * 80 + '\n')
        
        if not similar_ideas.exists():
            self.stdout.write(
                self.style.WARNING(f'No similar ideas found (min similarity: {min_similarity:.1%})')
            )
            return
        
        # Display similar ideas
        self.stdout.write(self.style.SUCCESS(f'\nFound {similar_ideas.count()} similar idea(s):\n'))
        
        for idx, idea in enumerate(similar_ideas, 1):
            distance = float(idea.distance)
            similarity = max(0.0, min(1.0, 1.0 - distance))  # Convert distance to similarity
            
            # Determine status based on threshold
            if similarity >= threshold:
                status = 'DUPLICATE'
                style = self.style.ERROR
            elif similarity >= 0.85:
                status = 'VERY SIMILAR'
                style = self.style.WARNING
            elif similarity >= 0.7:
                status = 'SIMILAR'
                style = self.style.WARNING
            else:
                status = 'SOMEWHAT SIMILAR'
                style = self.style.SUCCESS
            
            self.stdout.write(style(f'\n[{idx}] {status} (Similarity: {similarity:.2%}, Distance: {distance:.4f})'))
            self.stdout.write(f'  ID: {idea.id}')
            self.stdout.write(f'  Title: {idea.title}')
            
            if idea.description:
                desc_preview = idea.description[:100] + '...' if len(idea.description) > 100 else idea.description
                self.stdout.write(f'  Description: {desc_preview}')
            
            if idea.primary_keyword:
                self.stdout.write(f'  Keyword: {idea.primary_keyword}')
            
            self.stdout.write(f'  Created: {idea.created_at.strftime("%Y-%m-%d %H:%M")}')
        
        # Summary
        duplicates = sum(1 for idea in similar_ideas if (1.0 - float(idea.distance)) >= threshold)
        very_similar = sum(1 for idea in similar_ideas if 0.85 <= (1.0 - float(idea.distance)) < threshold)
        
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('Summary:'))
        self.stdout.write(f'  Total similar ideas found: {similar_ideas.count()}')
        if duplicates > 0:
            self.stdout.write(
                self.style.ERROR(f'  Duplicates (>= {threshold:.1%}): {duplicates}')
            )
        if very_similar > 0:
            self.stdout.write(
                self.style.WARNING(f'  Very similar (85%-{threshold:.1%}): {very_similar}')
            )
        self.stdout.write('=' * 80 + '\n')

