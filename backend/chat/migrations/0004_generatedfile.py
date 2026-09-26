import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0003_reasoning_and_attachments"),
    ]

    operations = [
        migrations.CreateModel(
            name="GeneratedFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("image", "Image"), ("document", "Document"), ("spreadsheet", "Spreadsheet"), ("presentation", "Presentation"), ("data", "Data")], max_length=16)),
                ("filename", models.CharField(max_length=255)),
                ("mime", models.CharField(max_length=128)),
                ("size", models.BigIntegerField(default=0)),
                ("storage_path", models.CharField(max_length=1024)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="generated_files", to="chat.conversation")),
                ("message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="files", to="chat.message")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="generated_files", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
