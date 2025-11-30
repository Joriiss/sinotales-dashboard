"""
Management command to generate post ideas based on scheduled settings.
This command should be run daily via cron job at the configured time.
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from sources.models import ScheduledPostIdeaGeneration, PostIdea
from sources.utils import log_activity, is_idea_too_similar_with_embeddings
from sources.embedding_service import EmbeddingService
import requests
import json


class Command(BaseCommand):
    help = 'Generate post ideas based on scheduled settings'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force generation even if scheduled generation is disabled',
        )
    
    def handle(self, *args, **options):
        force = options['force']
        
        # Get scheduled settings
        scheduled_settings = ScheduledPostIdeaGeneration.get_settings()
        
        if not scheduled_settings.enabled and not force:
            self.stdout.write(
                self.style.WARNING('Scheduled generation is disabled. Use --force to generate anyway.')
            )
            return
        
        self.stdout.write(
            self.style.SUCCESS(f'Starting scheduled post idea generation...')
        )
        self.stdout.write(f'  Provider: {scheduled_settings.provider}')
        self.stdout.write(f'  Model: {scheduled_settings.model}')
        self.stdout.write(f'  Number of ideas: {scheduled_settings.num_ideas}')
        
        # Build prompt (no tags or content for scheduled generation)
        context_text = "General blog post ideas about China, Chinese culture, travel, history, and related topics."
        
        prompt = f"""
        Generate {scheduled_settings.num_ideas} high-quality blog post ideas for a China travel blog.

Your ideas must be directly useful for people planning a trip to China.  
Avoid abstract cultural topics unless they clearly help a traveler understand a place, activity, or tradition they can experience on a trip.

Use the following context (optional reference material):
{context_text}

### Requirements
- Each idea must clearly answer a real search intent a traveler might have.
- Ideas should be practical, specific, and actionable: itineraries, travel guides, food recommendations, destination highlights, logistics, tips, or seasonal advice.
- Avoid purely cultural or historical analysis unless it connects directly to a travel experience.
- Each idea must contain:
  1. A compelling and SEO-friendly title (50–80 characters)
  2. A brief description (1–2 sentences) explaining what the post covers and why it helps a traveler.
- Cover a diverse range of regions, themes, and traveler needs.

### Tone Guidance
Prioritize topics that answer searches like:
- "Best things to do in ___"
- "Where to eat in ___"
- "Travel guide"
- "Hidden gems in ___"
- "Is ___ worth visiting?"
- "How to get from ___ to ___"
- "Best time to visit ___"
- "What to eat in ___"
- "7-day itinerary for ___"

### Important
Every idea must be explicitly linked to travel, trip planning, or on-the-ground experience in China.

### Output Format
Respond in JSON only:

{{
  "ideas": [
    {{
      "title": "Post title here",
      "description": "Brief summary explaining the travel value"
    }}
  ]
}}

Generate exactly {scheduled_settings.num_ideas} ideas.
Response:
"""
        
        # Call the appropriate provider
        try:
            response_text = None
            
            if scheduled_settings.provider == 'ollama':
                # Call Ollama
                ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                url = f"{ollama_url}/api/generate"
                
                payload = {
                    "model": scheduled_settings.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.8,
                        "top_p": 0.9,
                    }
                }
                
                response = requests.post(url, json=payload, timeout=120)
                response.raise_for_status()
                result = response.json()
                response_text = result.get('response', '').strip()
                
            elif scheduled_settings.provider == 'openai':
                # Call OpenAI
                api_key = getattr(settings, 'OPENAI_API_KEY', None)
                if not api_key:
                    self.stdout.write(
                        self.style.ERROR('OPENAI_API_KEY is not set in settings.')
                    )
                    return
                
                try:
                    from openai import OpenAI
                except ImportError:
                    self.stdout.write(
                        self.style.ERROR('openai library required. Install with: pip install openai')
                    )
                    return
                
                client = OpenAI(api_key=api_key)
                
                # Check model type for parameter compatibility
                is_gpt5 = 'gpt-5' in scheduled_settings.model.lower()
                is_newer_model = any(keyword in scheduled_settings.model.lower() for keyword in ['gpt-4o', 'gpt-5', 'o1', 'o3'])
                
                # Build request parameters
                request_params = {
                    "model": scheduled_settings.model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant that generates blog post ideas for a China travel blog. Always respond with valid JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                }
                
                # GPT-5 only supports default temperature (1), so don't set it
                if not is_gpt5:
                    request_params["temperature"] = 0.8
                
                # Use appropriate parameter based on model
                if is_newer_model:
                    request_params["max_completion_tokens"] = 2000
                else:
                    request_params["max_tokens"] = 2000
                
                response = client.chat.completions.create(**request_params)
                response_text = response.choices[0].message.content.strip()
                
            elif scheduled_settings.provider == 'gemini':
                # Call Gemini
                api_key = getattr(settings, 'GEMINI_API_KEY', None)
                if not api_key:
                    self.stdout.write(
                        self.style.ERROR('GEMINI_API_KEY is not set in settings.')
                    )
                    return
                
                try:
                    import google.generativeai as genai
                except ImportError:
                    self.stdout.write(
                        self.style.ERROR('google-generativeai library required. Install with: pip install google-generativeai')
                    )
                    return
                
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(scheduled_settings.model)
                response = model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": 0.8,
                        "max_output_tokens": 2000,
                    }
                )
                response_text = response.text.strip()
            
            # Parse JSON response
            if response_text:
                # Try to extract JSON from response
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}')
                
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    json_str = response_text[start_idx:end_idx + 1]
                    try:
                        parsed = json.loads(json_str)
                        ideas = parsed.get('ideas', [])
                        
                        # Initialize embedding service for similarity checking
                        embedding_service = None
                        similarity_threshold = 0.85  # 85% similarity threshold
                        try:
                            embedding_service = EmbeddingService()
                        except (ValueError, ImportError):
                            # Embedding service not available, skip similarity checking
                            pass
                        
                        # Get existing ideas with embeddings for comparison
                        existing_ideas_with_embeddings = list(
                            PostIdea.objects.filter(title_embedding__isnull=False)
                        ) if embedding_service else []
                        
                        # Create PostIdea objects with similarity checking
                        created_count = 0
                        created_ideas = []
                        skipped_similar = 0
                        skipped_titles = []
                        
                        for idea_data in ideas:
                            title = idea_data.get('title', '').strip()
                            description = idea_data.get('description', '').strip()
                            
                            if not title:
                                continue
                            
                            # Check for similarity using embeddings if available
                            too_similar = False
                            if embedding_service and existing_ideas_with_embeddings:
                                too_similar = is_idea_too_similar_with_embeddings(
                                    title, 
                                    existing_ideas_with_embeddings,
                                    embedding_service,
                                    similarity_threshold
                                )
                            
                            if too_similar:
                                skipped_similar += 1
                                skipped_titles.append(title)
                                self.stdout.write(f'  [-] Skipped similar: {title}')
                                continue
                            
                            # Generate embedding for the new idea
                            new_embedding = None
                            if embedding_service:
                                try:
                                    new_embedding = embedding_service.generate_embedding(title)
                                except Exception as e:
                                    self.stdout.write(f'  [!] Warning: Could not generate embedding for "{title}": {str(e)}')
                            
                            # Create the post idea
                            post_idea = PostIdea.objects.create(
                                title=title,
                                description=description,
                                title_embedding=new_embedding
                            )
                            created_count += 1
                            created_ideas.append({'id': post_idea.id, 'title': post_idea.title})
                            self.stdout.write(f'  [+] Created: {title}')
                            
                            # Add to existing list for checking against in the same batch
                            if new_embedding:
                                existing_ideas_with_embeddings.append(post_idea)
                        
                        if created_count > 0 or skipped_similar > 0:
                            # Log activity
                            message = f'{created_count} post idea(s) were generated using scheduled generation'
                            if skipped_similar > 0:
                                message += f' ({skipped_similar} similar ideas skipped)'
                            
                            log_activity(
                                'post_ideas_generated',
                                message,
                                user=None,  # System-generated
                                metadata={
                                    'count': created_count,
                                    'skipped_similar': skipped_similar,
                                    'num_requested': scheduled_settings.num_ideas,
                                    'provider': scheduled_settings.provider,
                                    'model': scheduled_settings.model,
                                    'similarity_check': embedding_service is not None,
                                    'similarity_threshold': similarity_threshold if embedding_service else None,
                                    'scheduled': True,
                                    'created_ideas': created_ideas,
                                    'skipped_titles': skipped_titles if skipped_titles else []
                                }
                            )
                            
                            # Update last_run timestamp
                            scheduled_settings.last_run = timezone.now()
                            scheduled_settings.save()
                            
                            if created_count > 0:
                                self.stdout.write(
                                    self.style.SUCCESS(f'\n[+] Successfully generated {created_count} post idea(s)!')
                                )
                            if skipped_similar > 0:
                                self.stdout.write(
                                    self.style.WARNING(f'[!] {skipped_similar} similar idea(s) were skipped.')
                                )
                        else:
                            self.stdout.write(
                                self.style.WARNING('No valid ideas were generated.')
                            )
                        
                    except json.JSONDecodeError:
                        self.stdout.write(
                            self.style.ERROR(f'Failed to parse {scheduled_settings.provider.upper()} response as JSON.')
                        )
                        self.stdout.write(f'Response: {response_text[:200]}')
                else:
                    self.stdout.write(
                        self.style.ERROR(f'Invalid response format from {scheduled_settings.provider.upper()}.')
                    )
                    self.stdout.write(f'Response: {response_text[:200]}')
            else:
                self.stdout.write(
                    self.style.ERROR(f'No response received from {scheduled_settings.provider.upper()}.')
                )
                
        except requests.exceptions.ConnectionError as e:
            if scheduled_settings.provider == 'ollama':
                ollama_url = getattr(settings, 'OLLAMA_URL', 'http://localhost:11434')
                self.stdout.write(
                    self.style.ERROR(f'Could not connect to Ollama at {ollama_url}. Make sure Ollama is running.')
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f'Connection error: {str(e)}')
                )
        except Exception as e:
            error_str = str(e)
            # Check for API key errors
            if 'api_key' in error_str.lower() or 'authentication' in error_str.lower() or 'unauthorized' in error_str.lower():
                if scheduled_settings.provider == 'openai':
                    self.stdout.write(
                        self.style.ERROR(f'OpenAI API key error: {error_str}')
                    )
                elif scheduled_settings.provider == 'gemini':
                    self.stdout.write(
                        self.style.ERROR(f'Gemini API key error: {error_str}')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f'Authentication error: {error_str}')
                    )
            else:
                self.stdout.write(
                    self.style.ERROR(f'Error generating ideas with {scheduled_settings.provider.upper()}: {error_str}')
                )

