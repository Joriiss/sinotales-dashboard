"""
WSGI config for china-blog-dashboard project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/wsgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Apply before Django boots so Gemini/HTTP clients prefer IPv4 (see config.prefer_ipv4).
_prefer_ipv4 = os.environ.get('PREFER_IPV4', '1').strip().lower()
if _prefer_ipv4 in ('1', 'true', 'yes', 'on'):
    from config.prefer_ipv4 import prefer_ipv4_dns
    prefer_ipv4_dns()

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()


