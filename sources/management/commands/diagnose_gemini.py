"""
Diagnose Gemini API connectivity from this host (IPv4 / proxy / location blocks).
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from sources.gemini_client import diagnose_egress


class Command(BaseCommand):
    help = 'Probe Gemini API egress (IPv4 forcing, proxy, location errors)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            default='gemini-2.0-flash',
            help='Model to use for a tiny generateContent probe (default: gemini-2.0-flash)',
        )

    def handle(self, *args, **options):
        if not getattr(settings, 'GEMINI_API_KEY', None):
            self.stderr.write(self.style.ERROR('GEMINI_API_KEY is not set'))
            return

        result = diagnose_egress(model=options['model'])
        for key, value in result.items():
            self.stdout.write(f'{key}: {value}')

        if result.get('generate_ok'):
            self.stdout.write(self.style.SUCCESS('Gemini generateContent OK'))
        else:
            self.stderr.write(self.style.ERROR('Gemini generateContent FAILED'))
            self.stderr.write(
                'If error mentions location and proxy_configured is False, '
                'set GEMINI_HTTP_PROXY in .env to an HTTP(S) proxy in a supported region, '
                'then restart Apache.'
            )
