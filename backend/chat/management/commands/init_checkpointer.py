"""Create the LangGraph checkpointer tables. Idempotent; run once at startup."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the LangGraph Postgres checkpointer tables"

    def handle(self, *args, **options):
        from chat.checkpointer import setup_sync

        try:
            setup_sync()
            self.stdout.write(self.style.SUCCESS("checkpointer tables ready"))
        except Exception as exc:
            # Chat degrades to stateless rather than blocking the whole boot.
            self.stderr.write(f"checkpointer setup failed ({exc}); chat memory disabled")
