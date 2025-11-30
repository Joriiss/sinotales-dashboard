"""
Management command to find similar post ideas using embedding-based similarity checking.
This uses OpenAI embeddings and vector similarity search for semantic similarity detection.
"""
from django.core.management.base import BaseCommand
from sources.models import PostIdea
from sources.embedding_service import EmbeddingService
from pgvector.django import CosineDistance
from collections import defaultdict
import numpy as np


class Command(BaseCommand):
    help = 'Find similar post ideas using embedding-based similarity checking'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.85,
            help='Similarity threshold (0.0-1.0). Ideas with similarity >= threshold are considered duplicates. Default: 0.85',
        )
        parser.add_argument(
            '--min-similarity',
            type=float,
            default=0.7,
            help='Minimum similarity to report (0.0-1.0). Only report pairs with similarity >= this value. Default: 0.7',
        )
        parser.add_argument(
            '--group',
            action='store_true',
            help='Group similar ideas together for easier review',
        )
        parser.add_argument(
            '--export',
            type=str,
            help='Export results to a text file (provide file path)',
        )
        parser.add_argument(
            '--delete',
            action='store_true',
            help='Actually delete duplicate ideas (keeps the oldest in each group). Use with caution! Without this flag, the command will only preview deletions.',
        )
        parser.add_argument(
            '--generate-embeddings',
            action='store_true',
            help='Generate embeddings for post ideas that don\'t have them yet',
        )
    
    def handle(self, *args, **options):
        threshold = options['threshold']
        min_similarity = options['min_similarity']
        group = options['group']
        export_path = options.get('export')
        delete = options.get('delete', False)
        generate_embeddings = options.get('generate_embeddings', False)
        
        # Validate thresholds
        if not 0.0 <= threshold <= 1.0:
            self.stdout.write(
                self.style.ERROR('Threshold must be between 0.0 and 1.0')
            )
            return
        
        if not 0.0 <= min_similarity <= 1.0:
            self.stdout.write(
                self.style.ERROR('min-similarity must be between 0.0 and 1.0')
            )
            return
        
        # Initialize embedding service
        try:
            embedding_service = EmbeddingService()
        except (ValueError, ImportError) as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to initialize embedding service: {str(e)}')
            )
            self.stdout.write(
                self.style.ERROR('Make sure OPENAI_API_KEY is set in settings.')
            )
            return
        
        # Generate embeddings for ideas that don't have them
        if generate_embeddings:
            self._generate_missing_embeddings(embedding_service)
        
        # Get all post ideas with embeddings
        ideas_with_embeddings = list(
            PostIdea.objects.filter(title_embedding__isnull=False).order_by('id')
        )
        total_ideas = len(ideas_with_embeddings)
        
        if total_ideas < 2:
            self.stdout.write(
                self.style.WARNING('Need at least 2 post ideas with embeddings to compare.')
            )
            if total_ideas == 0:
                self.stdout.write(
                    self.style.WARNING('No ideas have embeddings. Use --generate-embeddings to create them.')
                )
            return
        
        self.stdout.write(
            self.style.SUCCESS(f'\nAnalyzing {total_ideas} post ideas with embeddings...\n')
        )
        self.stdout.write(f'Threshold: {threshold} (ideas >= this are considered duplicates)')
        self.stdout.write(f'Min similarity to report: {min_similarity}\n')
        
        # Compare all pairs using vector similarity
        similar_pairs = []
        all_comparisons = []
        
        for i in range(len(ideas_with_embeddings)):
            idea1 = ideas_with_embeddings[i]
            
            # Use vector search to find similar ideas
            # CosineDistance: 0 = identical, 2 = opposite
            # We convert to similarity: similarity = 1 - distance
            max_distance = 2.0 - threshold  # Convert threshold to distance
            min_distance = 2.0 - min_similarity  # Convert min_similarity to distance
            
            similar_ideas = PostIdea.objects.filter(
                title_embedding__isnull=False,
                id__gt=idea1.id  # Only compare with ideas we haven't checked yet
            ).annotate(
                distance=CosineDistance('title_embedding', idea1.title_embedding)
            ).filter(
                distance__lte=min_distance  # distance <= min_distance means similarity >= min_similarity
            ).order_by('distance')
            
            for idea2 in similar_ideas:
                # Convert distance to similarity (1 - distance, clamped to 0-1)
                distance = float(idea2.distance)
                similarity = max(0.0, min(1.0, 1.0 - distance))
                
                comparison = {
                    'idea1': idea1,
                    'idea2': idea2,
                    'similarity': similarity,
                    'distance': distance,
                    'is_duplicate': similarity >= threshold,
                }
                
                all_comparisons.append(comparison)
                
                if similarity >= min_similarity:
                    if similarity >= threshold:
                        similar_pairs.append(comparison)
        
        # Output results
        output_lines = []
        
        if group and similar_pairs:
            # Group similar ideas together
            self.stdout.write(self.style.WARNING('\n=== SIMILAR IDEAS (Grouped) ===\n'))
            output_lines.append('\n=== SIMILAR IDEAS (Grouped) ===\n')
            
            groups = self._build_duplicate_groups(similar_pairs, threshold)
            
            # Display groups
            for group_idx, group in enumerate(groups, 1):
                self.stdout.write(
                    self.style.WARNING(f'\n--- Group {group_idx} ({len(group)} ideas) ---')
                )
                output_lines.append(f'\n--- Group {group_idx} ({len(group)} ideas) ---\n')
                
                for idea in sorted(group, key=lambda x: x.id):
                    self.stdout.write(f'  ID {idea.id}: {idea.title}')
                    output_lines.append(f'  ID {idea.id}: {idea.title}\n')
                    
                    if idea.description:
                        desc_preview = idea.description[:100] + '...' if len(idea.description) > 100 else idea.description
                        self.stdout.write(f'         {desc_preview}')
                        output_lines.append(f'         {desc_preview}\n')
                    
                    self.stdout.write(f'         Created: {idea.created_at.strftime("%Y-%m-%d %H:%M")}')
                    output_lines.append(f'         Created: {idea.created_at.strftime("%Y-%m-%d %H:%M")}\n')
        
        # Show detailed pairs
        if similar_pairs:
            self.stdout.write(self.style.WARNING('\n=== DETAILED SIMILAR PAIRS ===\n'))
            output_lines.append('\n=== DETAILED SIMILAR PAIRS ===\n')
            
            # Sort by similarity (highest first)
            similar_pairs.sort(key=lambda x: x['similarity'], reverse=True)
            
            for idx, pair in enumerate(similar_pairs, 1):
                idea1 = pair['idea1']
                idea2 = pair['idea2']
                similarity = pair['similarity']
                distance = pair['distance']
                
                status = 'DUPLICATE' if pair['is_duplicate'] else 'SIMILAR'
                style = self.style.ERROR if pair['is_duplicate'] else self.style.WARNING
                
                self.stdout.write(style(f'\n[{idx}] {status} (Similarity: {similarity:.2%}, Distance: {distance:.4f})'))
                output_lines.append(f'\n[{idx}] {status} (Similarity: {similarity:.2%}, Distance: {distance:.4f})\n')
                
                self.stdout.write(f'\n  Idea 1 (ID {idea1.id}): {idea1.title}')
                output_lines.append(f'  Idea 1 (ID {idea1.id}): {idea1.title}\n')
                if idea1.description:
                    desc_preview = idea1.description[:80] + '...' if len(idea1.description) > 80 else idea1.description
                    self.stdout.write(f'    {desc_preview}')
                    output_lines.append(f'    {desc_preview}\n')
                self.stdout.write(f'    Created: {idea1.created_at.strftime("%Y-%m-%d %H:%M")}')
                output_lines.append(f'    Created: {idea1.created_at.strftime("%Y-%m-%d %H:%M")}\n')
                
                self.stdout.write(f'\n  Idea 2 (ID {idea2.id}): {idea2.title}')
                output_lines.append(f'  Idea 2 (ID {idea2.id}): {idea2.title}\n')
                if idea2.description:
                    desc_preview = idea2.description[:80] + '...' if len(idea2.description) > 80 else idea2.description
                    self.stdout.write(f'    {desc_preview}')
                    output_lines.append(f'    {desc_preview}\n')
                self.stdout.write(f'    Created: {idea2.created_at.strftime("%Y-%m-%d %H:%M")}')
                output_lines.append(f'    Created: {idea2.created_at.strftime("%Y-%m-%d %H:%M")}\n')
        else:
            self.stdout.write(
                self.style.SUCCESS(f'\n[+] No similar ideas found (threshold: {threshold})')
            )
            output_lines.append(f'\n[+] No similar ideas found (threshold: {threshold})\n')
        
        # Summary statistics
        duplicates_count = sum(1 for p in similar_pairs if p['is_duplicate'])
        similar_count = len(similar_pairs) - duplicates_count
        
        self.stdout.write(self.style.SUCCESS('\n=== SUMMARY ==='))
        output_lines.append('\n=== SUMMARY ===\n')
        self.stdout.write(f'Total ideas analyzed: {total_ideas}')
        output_lines.append(f'Total ideas analyzed: {total_ideas}\n')
        self.stdout.write(f'Total comparisons: {len(all_comparisons)}')
        output_lines.append(f'Total comparisons: {len(all_comparisons)}\n')
        self.stdout.write(
            self.style.ERROR(f'Duplicate pairs (>= {threshold}): {duplicates_count}')
        )
        output_lines.append(f'Duplicate pairs (>= {threshold}): {duplicates_count}\n')
        if similar_count > 0:
            self.stdout.write(
                self.style.WARNING(f'Similar pairs ({min_similarity}-{threshold}): {similar_count}')
            )
            output_lines.append(f'Similar pairs ({min_similarity}-{threshold}): {similar_count}\n')
        
        # Export to file if requested
        if export_path:
            try:
                with open(export_path, 'w', encoding='utf-8') as f:
                    f.write('Post Ideas Similarity Report (Embedding-Based)\n')
                    f.write('=' * 50 + '\n\n')
                    f.write(f'Threshold: {threshold}\n')
                    f.write(f'Min similarity: {min_similarity}\n')
                    f.write(f'Generated: {self.get_timestamp()}\n\n')
                    f.writelines(output_lines)
                
                self.stdout.write(
                    self.style.SUCCESS(f'\n[+] Results exported to: {export_path}')
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'\n✗ Error exporting to file: {str(e)}')
                )
        
        # Handle deletion
        if duplicates_count > 0:
            if delete:
                self._delete_duplicates(similar_pairs, threshold)
            else:
                # Default: preview deletions (dry run)
                self._preview_deletions(similar_pairs, threshold)
    
    def _generate_missing_embeddings(self, embedding_service):
        """Generate embeddings for post ideas that don't have them"""
        ideas_without_embeddings = PostIdea.objects.filter(
            title_embedding__isnull=True
        )
        
        total = ideas_without_embeddings.count()
        if total == 0:
            self.stdout.write(
                self.style.SUCCESS('All post ideas already have embeddings.')
            )
            return
        
        self.stdout.write(
            self.style.WARNING(f'\nGenerating embeddings for {total} post idea(s)...\n')
        )
        
        generated = 0
        failed = 0
        
        for idea in ideas_without_embeddings:
            try:
                embedding = embedding_service.generate_embedding(idea.title)
                if embedding:
                    idea.title_embedding = embedding
                    idea.save(update_fields=['title_embedding'])
                    generated += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'  [+] Generated embedding for ID {idea.id}: {idea.title[:60]}...')
                    )
                else:
                    failed += 1
                    self.stdout.write(
                        self.style.ERROR(f'  [-] Failed to generate embedding for ID {idea.id}')
                    )
            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f'  [-] Error for ID {idea.id}: {str(e)}')
                )
        
        self.stdout.write(
            self.style.SUCCESS(f'\n[+] Generated {generated} embeddings, {failed} failed')
        )
    
    def _preview_deletions(self, similar_pairs, threshold):
        """Preview what would be deleted without actually deleting"""
        groups = self._build_duplicate_groups(similar_pairs, threshold)
        
        if not groups:
            return
        
        self.stdout.write(self.style.WARNING('\n=== DELETION PREVIEW (DRY RUN) ==='))
        self.stdout.write(self.style.WARNING('The following ideas would be deleted (keeping oldest in each group):\n'))
        
        total_to_delete = 0
        for group_idx, group in enumerate(groups, 1):
            sorted_group = sorted(group, key=lambda x: x.created_at)
            keep = sorted_group[0]
            to_delete = sorted_group[1:]
            
            if to_delete:
                self.stdout.write(
                    self.style.WARNING(f'\n--- Group {group_idx} ({len(group)} ideas) ---')
                )
                self.stdout.write(
                    self.style.SUCCESS(f'  KEEP (oldest): ID {keep.id} - {keep.title}')
                )
                self.stdout.write(
                    self.style.SUCCESS(f'    Created: {keep.created_at.strftime("%Y-%m-%d %H:%M")}')
                )
                
                for idea in to_delete:
                    self.stdout.write(
                        self.style.ERROR(f'  DELETE: ID {idea.id} - {idea.title}')
                    )
                    self.stdout.write(
                        self.style.ERROR(f'    Created: {idea.created_at.strftime("%Y-%m-%d %H:%M")}')
                    )
                    total_to_delete += 1
        
        self.stdout.write(
            self.style.WARNING(f'\n[!] Total ideas that would be deleted: {total_to_delete}')
        )
        self.stdout.write(
            self.style.WARNING('[!] This is a DRY RUN. Use --delete to actually delete these ideas.')
        )
    
    def _delete_duplicates(self, similar_pairs, threshold):
        """Actually delete duplicate ideas, keeping the oldest in each group"""
        from sources.utils import log_activity
        
        groups = self._build_duplicate_groups(similar_pairs, threshold)
        
        if not groups:
            self.stdout.write(
                self.style.WARNING('No duplicate groups found to delete.')
            )
            return
        
        self.stdout.write(self.style.WARNING('\n=== DELETING DUPLICATES ===\n'))
        
        total_deleted = 0
        deleted_ideas = []
        
        for group_idx, group in enumerate(groups, 1):
            sorted_group = sorted(group, key=lambda x: x.created_at)
            keep = sorted_group[0]
            to_delete = sorted_group[1:]
            
            if to_delete:
                self.stdout.write(
                    self.style.WARNING(f'Group {group_idx}: Keeping ID {keep.id} (oldest)')
                )
                
                for idea in to_delete:
                    try:
                        title = idea.title
                        idea_id = idea.id
                        idea.delete()
                        deleted_ideas.append({'id': idea_id, 'title': title})
                        total_deleted += 1
                        self.stdout.write(
                            self.style.SUCCESS(f'  [+] Deleted ID {idea_id}: {title}')
                        )
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f'  [-] Error deleting ID {idea.id}: {str(e)}')
                        )
        
        if total_deleted > 0:
            try:
                log_activity(
                    'post_ideas_deleted',
                    f'{total_deleted} duplicate post idea(s) were deleted (embedding-based)',
                    user=None,
                    metadata={
                        'count': total_deleted,
                        'threshold': threshold,
                        'method': 'embedding',
                        'deleted_ideas': deleted_ideas,
                    }
                )
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f'Note: Could not log activity: {str(e)}')
                )
            
            self.stdout.write(
                self.style.SUCCESS(f'\n[+] Successfully deleted {total_deleted} duplicate idea(s)!')
            )
        else:
            self.stdout.write(
                self.style.WARNING('No ideas were deleted.')
            )
    
    def _build_duplicate_groups(self, similar_pairs, threshold):
        """Build groups of duplicate ideas from similar pairs"""
        duplicate_pairs = [p for p in similar_pairs if p['is_duplicate']]
        
        if not duplicate_pairs:
            return []
        
        groups = []
        idea_to_group = {}
        
        for pair in duplicate_pairs:
            idea1_id = pair['idea1'].id
            idea2_id = pair['idea2'].id
            
            group1_idx = idea_to_group.get(idea1_id)
            group2_idx = idea_to_group.get(idea2_id)
            
            if group1_idx is None and group2_idx is None:
                new_group = [pair['idea1'], pair['idea2']]
                groups.append(new_group)
                group_idx = len(groups) - 1
                idea_to_group[idea1_id] = group_idx
                idea_to_group[idea2_id] = group_idx
            elif group1_idx is not None and group2_idx is None:
                if pair['idea2'] not in groups[group1_idx]:
                    groups[group1_idx].append(pair['idea2'])
                idea_to_group[idea2_id] = group1_idx
            elif group1_idx is None and group2_idx is not None:
                if pair['idea1'] not in groups[group2_idx]:
                    groups[group2_idx].append(pair['idea1'])
                idea_to_group[idea1_id] = group2_idx
            elif group1_idx != group2_idx:
                groups[group1_idx].extend(groups[group2_idx])
                for idea in groups[group2_idx]:
                    idea_to_group[idea.id] = group1_idx
                groups[group2_idx] = None
        
        groups = [list(set(g)) for g in groups if g is not None]
        return groups
    
    def get_timestamp(self):
        """Get current timestamp as string"""
        from django.utils import timezone
        return timezone.now().strftime('%Y-%m-%d %H:%M:%S')

