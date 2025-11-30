"""
Management command to find similar post ideas using text-based similarity checking.
This helps identify duplicate or near-duplicate ideas that should be cleaned up.
"""
from django.core.management.base import BaseCommand
from sources.models import PostIdea
from difflib import SequenceMatcher
from collections import defaultdict


class Command(BaseCommand):
    help = 'Find similar post ideas using text-based similarity checking'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.8,
            help='Similarity threshold (0.0-1.0). Ideas with similarity >= threshold are considered duplicates. Default: 0.8',
        )
        parser.add_argument(
            '--min-similarity',
            type=float,
            default=0.5,
            help='Minimum similarity to report (0.0-1.0). Only report pairs with similarity >= this value. Default: 0.5',
        )
        parser.add_argument(
            '--show-all',
            action='store_true',
            help='Show all comparisons, not just similar ones',
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
    
    def calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate similarity between two texts using SequenceMatcher
        Returns a value between 0 (completely different) and 1 (identical)
        """
        # Normalize texts: lowercase, remove extra spaces
        text1_normalized = ' '.join(text1.lower().split())
        text2_normalized = ' '.join(text2.lower().split())
        return SequenceMatcher(None, text1_normalized, text2_normalized).ratio()
    
    def extract_keywords(self, title: str) -> set:
        """
        Extract meaningful keywords from a title (removes common stop words)
        """
        stop_words = {
            'how', 'to', 'in', 'the', 'a', 'an', 'and', 'or', 'but', 'for', 'by', 
            'with', 'on', 'at', 'from', 'of', 'is', 'are', 'was', 'were', 'be', 
            'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 
            'would', 'should', 'could', 'may', 'might', 'must', 'can', 'this', 
            'that', 'these', 'those', 'not', 'get', 'got', 'take', 'taking'
        }
        # Remove punctuation and split
        words = title.lower().replace(':', '').replace(',', '').replace('-', ' ').replace('(', '').replace(')', '').split()
        return {w for w in words if w not in stop_words and len(w) > 2}
    
    def calculate_keyword_overlap(self, title1: str, title2: str) -> float:
        """
        Calculate similarity based on keyword overlap (Jaccard similarity)
        Returns a value between 0 and 1
        """
        keywords1 = self.extract_keywords(title1)
        keywords2 = self.extract_keywords(title2)
        
        if not keywords1 or not keywords2:
            return 0.0
        
        intersection = keywords1.intersection(keywords2)
        union = keywords1.union(keywords2)
        
        # Jaccard similarity
        return len(intersection) / len(union) if union else 0.0
    
    def calculate_similarity(self, title1: str, title2: str) -> dict:
        """
        Calculate multiple similarity metrics between two titles
        Returns a dict with similarity scores
        """
        text_sim = self.calculate_text_similarity(title1, title2)
        keyword_sim = self.calculate_keyword_overlap(title1, title2)
        max_sim = max(text_sim, keyword_sim)
        
        return {
            'text_similarity': text_sim,
            'keyword_similarity': keyword_sim,
            'max_similarity': max_sim,
        }
    
    def handle(self, *args, **options):
        threshold = options['threshold']
        min_similarity = options['min_similarity']
        show_all = options['show_all']
        group = options['group']
        export_path = options.get('export')
        delete = options.get('delete', False)
        
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
        
        # Get all post ideas
        ideas = list(PostIdea.objects.all().order_by('id'))
        total_ideas = len(ideas)
        
        if total_ideas < 2:
            self.stdout.write(
                self.style.WARNING('Need at least 2 post ideas to compare.')
            )
            return
        
        self.stdout.write(
            self.style.SUCCESS(f'\nAnalyzing {total_ideas} post ideas...\n')
        )
        self.stdout.write(f'Threshold: {threshold} (ideas >= this are considered duplicates)')
        self.stdout.write(f'Min similarity to report: {min_similarity}\n')
        
        # Compare all pairs
        similar_pairs = []
        all_comparisons = []
        
        for i in range(len(ideas)):
            for j in range(i + 1, len(ideas)):
                idea1 = ideas[i]
                idea2 = ideas[j]
                
                similarity = self.calculate_similarity(idea1.title, idea2.title)
                
                comparison = {
                    'idea1': idea1,
                    'idea2': idea2,
                    'similarity': similarity,
                    'is_duplicate': similarity['max_similarity'] >= threshold,
                }
                
                all_comparisons.append(comparison)
                
                if similarity['max_similarity'] >= min_similarity:
                    if similarity['max_similarity'] >= threshold:
                        similar_pairs.append(comparison)
        
        # Output results
        output_lines = []
        
        if group and similar_pairs:
            # Group similar ideas together
            self.stdout.write(self.style.WARNING('\n=== SIMILAR IDEAS (Grouped) ===\n'))
            output_lines.append('\n=== SIMILAR IDEAS (Grouped) ===\n')
            
            # Build groups using union-find approach
            groups = []
            idea_to_group = {}
            
            for pair in similar_pairs:
                idea1_id = pair['idea1'].id
                idea2_id = pair['idea2'].id
                
                # Find groups for both ideas
                group1_idx = idea_to_group.get(idea1_id)
                group2_idx = idea_to_group.get(idea2_id)
                
                if group1_idx is None and group2_idx is None:
                    # Create new group
                    new_group = [pair['idea1'], pair['idea2']]
                    groups.append(new_group)
                    group_idx = len(groups) - 1
                    idea_to_group[idea1_id] = group_idx
                    idea_to_group[idea2_id] = group_idx
                elif group1_idx is not None and group2_idx is None:
                    # Add idea2 to idea1's group
                    groups[group1_idx].append(pair['idea2'])
                    idea_to_group[idea2_id] = group1_idx
                elif group1_idx is None and group2_idx is not None:
                    # Add idea1 to idea2's group
                    groups[group2_idx].append(pair['idea1'])
                    idea_to_group[idea1_id] = group2_idx
                elif group1_idx != group2_idx:
                    # Merge two groups
                    groups[group1_idx].extend(groups[group2_idx])
                    for idea in groups[group2_idx]:
                        idea_to_group[idea.id] = group1_idx
                    groups[group2_idx] = None
            
            # Remove None groups and deduplicate
            groups = [list(set(g)) for g in groups if g is not None]
            
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
            similar_pairs.sort(key=lambda x: x['similarity']['max_similarity'], reverse=True)
            
            for idx, pair in enumerate(similar_pairs, 1):
                idea1 = pair['idea1']
                idea2 = pair['idea2']
                sim = pair['similarity']
                
                status = 'DUPLICATE' if pair['is_duplicate'] else 'SIMILAR'
                style = self.style.ERROR if pair['is_duplicate'] else self.style.WARNING
                
                self.stdout.write(style(f'\n[{idx}] {status} (Max: {sim["max_similarity"]:.2%})'))
                output_lines.append(f'\n[{idx}] {status} (Max: {sim["max_similarity"]:.2%})\n')
                
                self.stdout.write(f'  Text similarity: {sim["text_similarity"]:.2%}')
                output_lines.append(f'  Text similarity: {sim["text_similarity"]:.2%}\n')
                self.stdout.write(f'  Keyword similarity: {sim["keyword_similarity"]:.2%}')
                output_lines.append(f'  Keyword similarity: {sim["keyword_similarity"]:.2%}\n')
                
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
                self.style.SUCCESS(f'\n✓ No similar ideas found (threshold: {threshold})')
            )
            output_lines.append(f'\n✓ No similar ideas found (threshold: {threshold})\n')
        
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
        
        # Handle deletion
        if duplicates_count > 0:
            if delete:
                self._delete_duplicates(similar_pairs, threshold)
            else:
                # Default: preview deletions (dry run)
                self._preview_deletions(similar_pairs, threshold)
        
        # Export to file if requested
        if export_path:
            try:
                with open(export_path, 'w', encoding='utf-8') as f:
                    f.write('Post Ideas Similarity Report\n')
                    f.write('=' * 50 + '\n\n')
                    f.write(f'Threshold: {threshold}\n')
                    f.write(f'Min similarity: {min_similarity}\n')
                    f.write(f'Generated: {self.get_timestamp()}\n\n')
                    f.writelines(output_lines)
                
                self.stdout.write(
                    self.style.SUCCESS(f'\n✓ Results exported to: {export_path}')
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'\n✗ Error exporting to file: {str(e)}')
                )
    
    def _preview_deletions(self, similar_pairs, threshold):
        """Preview what would be deleted without actually deleting"""
        # Build groups of duplicates
        groups = self._build_duplicate_groups(similar_pairs, threshold)
        
        if not groups:
            return
        
        self.stdout.write(self.style.WARNING('\n=== DELETION PREVIEW (DRY RUN) ==='))
        self.stdout.write(self.style.WARNING('The following ideas would be deleted (keeping oldest in each group):\n'))
        
        total_to_delete = 0
        for group_idx, group in enumerate(groups, 1):
            # Sort by creation date (oldest first)
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
        
        # Build groups of duplicates
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
            # Sort by creation date (oldest first)
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
                            self.style.SUCCESS(f'  ✓ Deleted ID {idea_id}: {title}')
                        )
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f'  ✗ Error deleting ID {idea.id}: {str(e)}')
                        )
        
        if total_deleted > 0:
            # Log the deletion activity
            try:
                log_activity(
                    'post_ideas_deleted',
                    f'{total_deleted} duplicate post idea(s) were deleted',
                    user=None,  # System-generated
                    metadata={
                        'count': total_deleted,
                        'threshold': threshold,
                        'deleted_ideas': deleted_ideas,
                    }
                )
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(f'Note: Could not log activity: {str(e)}')
                )
            
            self.stdout.write(
                self.style.SUCCESS(f'\n✓ Successfully deleted {total_deleted} duplicate idea(s)!')
            )
        else:
            self.stdout.write(
                self.style.WARNING('No ideas were deleted.')
            )
    
    def _build_duplicate_groups(self, similar_pairs, threshold):
        """Build groups of duplicate ideas from similar pairs"""
        # Only consider pairs that are actual duplicates (>= threshold)
        duplicate_pairs = [p for p in similar_pairs if p['is_duplicate']]
        
        if not duplicate_pairs:
            return []
        
        # Build groups using union-find approach
        groups = []
        idea_to_group = {}
        
        for pair in duplicate_pairs:
            idea1_id = pair['idea1'].id
            idea2_id = pair['idea2'].id
            
            # Find groups for both ideas
            group1_idx = idea_to_group.get(idea1_id)
            group2_idx = idea_to_group.get(idea2_id)
            
            if group1_idx is None and group2_idx is None:
                # Create new group
                new_group = [pair['idea1'], pair['idea2']]
                groups.append(new_group)
                group_idx = len(groups) - 1
                idea_to_group[idea1_id] = group_idx
                idea_to_group[idea2_id] = group_idx
            elif group1_idx is not None and group2_idx is None:
                # Add idea2 to idea1's group
                if pair['idea2'] not in groups[group1_idx]:
                    groups[group1_idx].append(pair['idea2'])
                idea_to_group[idea2_id] = group1_idx
            elif group1_idx is None and group2_idx is not None:
                # Add idea1 to idea2's group
                if pair['idea1'] not in groups[group2_idx]:
                    groups[group2_idx].append(pair['idea1'])
                idea_to_group[idea1_id] = group2_idx
            elif group1_idx != group2_idx:
                # Merge two groups
                groups[group1_idx].extend(groups[group2_idx])
                for idea in groups[group2_idx]:
                    idea_to_group[idea.id] = group1_idx
                groups[group2_idx] = None
        
        # Remove None groups and deduplicate
        groups = [list(set(g)) for g in groups if g is not None]
        
        return groups
    
    def get_timestamp(self):
        """Get current timestamp as string"""
        from django.utils import timezone
        return timezone.now().strftime('%Y-%m-%d %H:%M:%S')

