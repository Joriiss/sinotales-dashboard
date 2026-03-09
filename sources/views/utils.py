from django.http import JsonResponse
from django.conf import settings
import re

def _validate_api_token(request):
    """Helper function to validate API token from request"""
    api_token = settings.API_TOKEN
    if not api_token:
        return None, JsonResponse({
            'success': False,
            'error': 'API token not configured'
        }, status=500)
    
    # Get token from Authorization header or query parameter
    provided_token = None
    
    # Check Authorization header: "Token <token>" or "Bearer <token>"
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() in ('token', 'bearer'):
            provided_token = parts[1]
    
    # Check query parameter
    if not provided_token:
        provided_token = request.GET.get('token', None)
    
    # Validate token
    if not provided_token or provided_token != api_token:
        return None, JsonResponse({
            'success': False,
            'error': 'Invalid or missing authentication token'
        }, status=401)
    
    return True, None



def _parse_blog_content_sections(content):
    """
    Parse HTML blog post content into sections: intro, summary, main_content, conclusion
    
    Returns a dict with:
    - intro: Content from H1 until "Quick Summary", "Key Takeaways", or "TL;DR" (etc.) heading
    - summary_title: The heading text of the summary section
    - summary_content: The content within the summary section
    - main_content: Content between summary and conclusion
    - conclusion: The last paragraph/section
    """
    import re
    
    if not content:
        return {
            'intro': '',
            'summary_title': '',
            'summary_content': '',
            'main_content': '',
            'conclusion': ''
        }
    
    # Remove H1 if present (we don't need it in sections)
    content = re.sub(r'<h1[^>]*>.*?</h1>', '', content, flags=re.IGNORECASE | re.DOTALL)
    content = content.strip()
    
    # Find the summary section: "Quick Summary", "Key Takeaways", or "TL;DR: ..." (etc.)
    # Look for H2, H3, or div containing H2/H3 with those phrases
    # Pattern matches "Quick Summary" / "Key Takeaways" / "TL;DR" even when followed by colon and more text
    # Use [^\n]* after the phrase so the title stops at newline (avoids capturing rest of post)
    
    summary_match = None
    summary_heading_re = r'(?:Quick\s+Summary|Key\s+Takeaways|TL\.?;DR|TLDR)(?:\s*:[^\n]*)?'
    
    # First try to find div containing H2 or H3 with summary phrase (most common case)
    # Need to handle nested divs properly by finding the opening div and matching closing div
    # Title ends at newline so we don't pull in following content; HTML inside heading (e.g. <strong>) still allowed
    h2_pattern = r'<h2[^>]*>(.*?' + summary_heading_re + r'[^\n]*)</h2>'
    h2_match = re.search(h2_pattern, content, re.IGNORECASE | re.DOTALL)
    heading_match = h2_match
    if not heading_match:
        h3_pattern = r'<h3[^>]*>(.*?' + summary_heading_re + r'[^\n]*)</h3>'
        h3_match = re.search(h3_pattern, content, re.IGNORECASE | re.DOTALL)
        heading_match = h3_match
    
    if heading_match:
        # Find the div that contains this heading (H2 or H3)
        # Look backwards from heading to find the opening <div> tag
        heading_start = heading_match.start()
        # Find the last <div> before this heading
        div_start_match = None
        for match in re.finditer(r'<div[^>]*>', content[:heading_start], re.IGNORECASE):
            div_start_match = match
        
        if div_start_match:
            # Find the matching closing </div> after the H3
            # Count div tags to find the matching closing tag
            div_start_pos = div_start_match.start()
            div_count = 1
            pos = div_start_match.end()
            div_end_pos = -1
            
            while pos < len(content) and div_count > 0:
                next_open = content.find('<div', pos)
                next_close = content.find('</div>', pos)
                
                if next_close == -1:
                    break
                
                if next_open != -1 and next_open < next_close:
                    div_count += 1
                    pos = next_open + 4
                else:
                    div_count -= 1
                    if div_count == 0:
                        div_end_pos = next_close + 6  # Position after </div>
                        break
                    pos = next_close + 6
            
            if div_end_pos > 0:
                summary_title_raw = heading_match.group(1).strip()
                # Remove HTML tags from title first
                summary_title_clean = re.sub(r'<[^>]+>', '', summary_title_raw).strip()
                # Remove emojis from title
                summary_title_clean = re.sub(r'[\U0001F300-\U0001F9FF]|[\U00002600-\U000027BF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F1E0-\U0001F1FF]|[\U00002700-\U000027BF]|[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|[\U00002600-\U000026FF]|[\U00002700-\U000027BF]', '', summary_title_clean).strip()
                # Extract inner content: remove outer div wrapper and heading tag
                # Find the heading closing tag and extract everything after it until the div closing tag
                heading_end = heading_match.end()  # Position after </h2> or </h3>
                # Extract content between heading closing tag and div closing tag
                # div_end_pos is after </div>, so we need to go back 6 chars to get before </div>
                inner_content_start = heading_end
                inner_content_end = div_end_pos - 6  # Position before </div>
                summary_content = content[inner_content_start:inner_content_end].strip()
                # Remove any trailing </div> tags that might have been included
                summary_content = re.sub(r'</div>\s*$', '', summary_content, flags=re.IGNORECASE | re.MULTILINE).strip()
                # Create a match-like object
                class MatchObj:
                    def __init__(self, start, end, title, content):
                        self._start = start
                        self._end = end
                        self._title = title
                        self._content = content
                    def start(self):
                        return self._start
                    def end(self):
                        return self._end
                    def group(self, n):
                        return self._title if n == 1 else self._content
                summary_match = MatchObj(div_start_pos, div_end_pos, summary_title_clean, summary_content)
    
    # If not found in div, try standalone H2/H3 with summary phrases
    # Title capture stops at newline so we don't pull in the rest of the post
    if not summary_match:
        for phrase in (r'Quick\s+Summary', r'Key\s+Takeaways', r'TL\.?;DR', r'TLDR'):
            pattern = r'<h2[^>]*>(.*?' + phrase + r'(?:\s*:[^\n]*)?[^\n]*)</h2>(.*?)(?=<h[2-6]|$)'
            summary_match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if summary_match:
                break
            pattern = r'<h3[^>]*>(.*?' + phrase + r'(?:\s*:[^\n]*)?[^\n]*)</h3>(.*?)(?=<h[2-6]|$)'
            summary_match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if summary_match:
                break
    
    intro = ''
    summary_title = ''
    summary_content = ''
    main_content = ''
    conclusion = ''
    
    if summary_match:
        # Extract intro (everything before the summary section)
        intro_end = summary_match.start()
        intro = content[:intro_end].strip()
        
        # For div-wrapped summaries, ensure intro ends before the opening <div> tag
        # Check if this is a div-wrapped summary (has _content attribute)
        if hasattr(summary_match, '_content'):
            # This is a div-wrapped summary
            # The intro_end is at div_start_pos, which is where <div> begins
            # We need to make sure intro doesn't include the opening <div> tag
            # Find the last <div> tag in the intro and remove everything from that point
            # This handles cases where the div might not be at the absolute end
            div_matches = list(re.finditer(r'<div[^>]*>', intro, re.IGNORECASE))
            if div_matches:
                # Get the last div tag position
                last_div_pos = div_matches[-1].start()
                # Truncate intro at the last div tag
                intro = intro[:last_div_pos].strip()
            else:
                # Fallback: try to remove any div tag at the end
                intro = re.sub(r'\s*<div[^>]*>\s*$', '', intro, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
                intro = intro.strip()
        
        # Extract summary title and content
        summary_title_raw = summary_match.group(1).strip()
        summary_title = re.sub(r'<[^>]+>', '', summary_title_raw).strip()  # Remove HTML tags
        # Remove emojis (common emoji ranges and symbols)
        summary_title = re.sub(r'[\U0001F300-\U0001F9FF]|[\U00002600-\U000027BF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F1E0-\U0001F1FF]|[\U00002700-\U000027BF]|[\U0001F900-\U0001F9FF]|[\U0001FA00-\U0001FA6F]|[\U0001FA70-\U0001FAFF]|[\U00002600-\U000026FF]|[\U00002700-\U000027BF]', '', summary_title).strip()
        summary_content = summary_match.group(2).strip()
        
        # Find the end of summary section (next H2/H3 or end of content)
        summary_end = summary_match.end()
        content_after_summary = content[summary_end:].strip()
        
        # Extract conclusion: look for "Conclusion" heading first
        # Pattern to match H2 or H3 with "Conclusion" (case-insensitive)
        # Match a single heading tag that contains "Conclusion" - must not match across multiple heading tags
        # Use a pattern that matches the opening tag, then content (possibly with nested tags), then closing tag
        # The key is to ensure we don't match across </h2> boundaries
        conclusion_heading_pattern = r'<h[23][^>]*>(?:(?!</h[23]>).)*\bConclusion\b(?:(?!</h[23]>).)*</h[23]>'
        conclusion_heading_match = re.search(conclusion_heading_pattern, content_after_summary, re.IGNORECASE | re.DOTALL)
        
        if conclusion_heading_match:
            # Found conclusion heading - include heading and everything after it
            conclusion_start = conclusion_heading_match.start()
            conclusion = content_after_summary[conclusion_start:].strip()
            # Everything before the conclusion heading is main_content
            main_content = content_after_summary[:conclusion_start].strip()
        else:
            # No conclusion heading found, try to find the last paragraph
            paragraph_matches = list(re.finditer(r'<p[^>]*>.*?</p>', content_after_summary, re.IGNORECASE | re.DOTALL))
            
            if paragraph_matches:
                # Last paragraph is conclusion
                last_para_match = paragraph_matches[-1]
                conclusion = last_para_match.group(0)
                # Everything before the last paragraph is main_content
                main_content = content_after_summary[:last_para_match.start()].strip()
            else:
                # No paragraphs found, try to find last section
                # Look for last block of content (could be wrapped in div, or just text)
                # Split by common block-level tags
                blocks = re.split(r'(<h[2-6][^>]*>.*?</h[2-6]>)', content_after_summary, flags=re.IGNORECASE | re.DOTALL)
                if len(blocks) > 2:
                    # Last block might be conclusion
                    conclusion = blocks[-1].strip()
                    main_content = ''.join(blocks[:-1]).strip()
                else:
                    # Can't split, put everything in main_content
                    main_content = content_after_summary
                    conclusion = ''
    else:
        # No summary section found
        # Try to split content: conclusion is last paragraph, intro is first paragraph(s)
        paragraph_matches = list(re.finditer(r'<p[^>]*>.*?</p>', content, re.IGNORECASE | re.DOTALL))
        
        if paragraph_matches and len(paragraph_matches) > 1:
            # First paragraph as intro
            intro = paragraph_matches[0].group(0)
            # Last paragraph as conclusion
            conclusion = paragraph_matches[-1].group(0)
            # Everything in between is main_content
            main_content = content[paragraph_matches[0].end():paragraph_matches[-1].start()].strip()
        elif paragraph_matches:
            # Only one paragraph - use as intro
            intro = paragraph_matches[0].group(0)
            main_content = ''
            conclusion = ''
        else:
            # No paragraphs found, put everything in main_content
            main_content = content
            intro = ''
            conclusion = ''
    
    return {
        'intro': intro,
        'summary_title': summary_title,
        'summary_content': summary_content,
        'main_content': main_content,
        'conclusion': conclusion
    }



def _format_acf_field(value, label, field_type='text'):
    """
    Format an ACF field with both raw value and _source object
    
    Args:
        value: The raw field value
        label: The field label
        field_type: 'text' or 'wysiwyg'
    
    Returns:
        Dict with _source object containing label, type, and formatted_value
    """
    return {
        'label': label,
        'type': field_type,
        'formatted_value': value or ''
    }



def _format_faq_acf_fields(blog_post):
    """
    Format FAQ data into individual ACF fields (question_1, answer_1, etc.)
    
    Returns a dict with all FAQ-related ACF fields
    """
    import re
    
    faq_fields = {}
    
    # FAQ title
    faq_title = blog_post.faq_title or ''
    faq_fields['faqs_title'] = faq_title
    faq_fields['faqs_title_source'] = {
        'label': 'Title',
        'type': 'text',
        'formatted_value': faq_title
    }
    
    # FAQ questions and answers (up to 4)
    faq_list = blog_post.faq if blog_post.faq and isinstance(blog_post.faq, list) else []
    
    for i in range(1, 5):  # question_1 through question_4
        index = i - 1
        if index < len(faq_list) and isinstance(faq_list[index], dict):
            question = faq_list[index].get('question', '').strip()
            answer = faq_list[index].get('answer', '').strip()
        else:
            question = ''
            answer = ''
        
        # Question field (text)
        faq_fields[f'question_{i}'] = question
        faq_fields[f'question_{i}_source'] = {
            'label': 'Title',
            'type': 'text',
            'formatted_value': question
        }
        
        # Answer field (wysiwyg)
        # Raw answer is just the text
        faq_fields[f'answer_{i}'] = answer
        
        # Formatted answer: wrap in <p> tags if not already HTML
        if answer:
            # Check if answer already contains HTML tags
            if re.search(r'<[^>]+>', answer):
                formatted_answer = answer
            else:
                # Wrap in <p> tags
                formatted_answer = f'<p>{answer}</p>'
        else:
            formatted_answer = ''
        
        faq_fields[f'answer_{i}_source'] = {
            'label': 'Content',
            'type': 'wysiwyg',
            'formatted_value': formatted_answer
        }
    
    return faq_fields
