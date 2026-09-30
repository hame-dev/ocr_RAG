"""Per-chunk metadata columns, the stage-2 cache, document vectors, and a
WEIGHTED tsv: keywords (A) > section context (B) > body (C) > title (D).

The title is deliberately the lowest weight, so one document whose title
matches the query cannot flood the lexical candidate list with every one of its
chunks.

ar_normalize_v1 is STRICT: a NULL argument returns NULL and `||` with NULL
yields NULL, so every branch is wrapped in coalesce(..., '') INSIDE the call.
Without that, one NULL column would silently drop the chunk from lexical search.
The function itself is untouched (see 0002 for why it is versioned).

The AddField operations run first because the generated column references them;
in reverse the old tsv is restored before those columns are dropped.
"""
import django.db.models.deletion
import pgvector.django.vector
from django.db import migrations, models


WEIGHTED_TSV = """
DROP INDEX IF EXISTS chunk_tsv;
ALTER TABLE rag_chunk DROP COLUMN IF EXISTS tsv;
ALTER TABLE rag_chunk ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(keywords_text, ''))), 'A') ||
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(context_text, ''))), 'B') ||
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(text, ''))), 'C') ||
  setweight(to_tsvector('simple', ar_normalize_v1(coalesce(title_text, ''))), 'D')
) STORED;
CREATE INDEX IF NOT EXISTS chunk_tsv ON rag_chunk USING gin (tsv);
"""

# The 0002 definition.
UNWEIGHTED_TSV = """
DROP INDEX IF EXISTS chunk_tsv;
ALTER TABLE rag_chunk DROP COLUMN IF EXISTS tsv;
ALTER TABLE rag_chunk ADD COLUMN tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('simple', ar_normalize_v1(text))) STORED;
CREATE INDEX IF NOT EXISTS chunk_tsv ON rag_chunk USING gin (tsv);
"""


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0003_document_owner'),
        ('rag', '0002_ar_normalize_and_search'),
    ]

    operations = [
        migrations.AddField(
            model_name='chunk',
            name='context_text',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='chunk',
            name='keywords_text',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='chunk',
            name='title_text',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='indexrun',
            name='context_done',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='indexrun',
            name='context_error',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='indexrun',
            name='context_model',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='indexrun',
            name='context_status',
            field=models.CharField(default='none', max_length=16),
        ),
        migrations.AddField(
            model_name='indexrun',
            name='context_windows',
            field=models.IntegerField(default=0),
        ),
        migrations.CreateModel(
            name='ChunkContext',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text_sha256', models.CharField(max_length=64)),
                ('model_id', models.CharField(max_length=128)),
                ('prompt_version', models.CharField(max_length=24)),
                ('summary', models.TextField(blank=True)),
                ('keywords', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'unique_together': {('text_sha256', 'model_id', 'prompt_version')},
            },
        ),
        migrations.CreateModel(
            name='DocumentVector',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('embedding', pgvector.django.vector.VectorField(dimensions=1024)),
                ('text_sha256', models.CharField(max_length=64)),
                ('model', models.CharField(max_length=64)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='vector', to='documents.document')),
            ],
        ),
        migrations.RunSQL(WEIGHTED_TSV, UNWEIGHTED_TSV),
    ]
