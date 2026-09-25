import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0002_conversation_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="reasoning",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="message",
            name="thinking_ms",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ChatAttachment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("image", "Image"), ("pdf", "PDF"), ("text", "Text")], max_length=8)),
                ("filename", models.CharField(max_length=255)),
                ("mime", models.CharField(max_length=128)),
                ("size", models.BigIntegerField(default=0)),
                ("storage_path", models.CharField(max_length=1024)),
                ("extracted_text", models.TextField(blank=True)),
                ("page_images", models.JSONField(blank=True, default=list)),
                ("page_count", models.IntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="chat.conversation")),
                ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="attachments", to="chat.message")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_attachments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
