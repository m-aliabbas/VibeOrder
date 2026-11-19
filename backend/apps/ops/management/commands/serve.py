"""Production web server for the branch host (waitress: pure Python, runs on Windows and Linux).

`runserver` is for development only. TLS is terminated by the reverse proxy in deploy/Caddyfile; this listens on
loopback by default so tablets reach it only through the proxy.
"""
import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Serve the branch API, KDS and storefront with waitress (production)."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=os.environ.get("VIBEORDER_LISTEN_HOST", "127.0.0.1"))
        parser.add_argument("--port", type=int, default=int(os.environ.get("VIBEORDER_LISTEN_PORT", "8000")))
        parser.add_argument("--threads", type=int, default=int(os.environ.get("VIBEORDER_THREADS", "8")))

    def handle(self, *args, **opts):
        from waitress import serve

        from vibeorder.wsgi import application

        self.stdout.write(f"serving on http://{opts['host']}:{opts['port']} threads={opts['threads']}")
        self.stdout.flush()
        serve(application, host=opts["host"], port=opts["port"], threads=opts["threads"],
              ident="vibeorder", channel_timeout=60, connection_limit=200,
              trusted_proxy="127.0.0.1", trusted_proxy_headers={"x-forwarded-proto", "x-forwarded-for"},
              clear_untrusted_proxy_headers=True)
