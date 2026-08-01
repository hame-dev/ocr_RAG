"""Arabic search normalization + the generated columns and indexes.

TWO TRAPS, both expensive to fix later:

1. The Unicode escapes below use PostgreSQL's `U&'...'` literal syntax. Postgres'
   regex engine does NOT interpret `\\uXXXX` inside an ordinary string literal —
   written that way the function silently becomes a no-op, every Arabic search
   quietly loses recall, and nothing errors.

2. The function name is VERSIONED (`_v1`). Generated columns are not recomputed
   when a function body changes, so altering normalization requires a new
   function, a new column and a table rewrite. Versioning from day one makes
   that an explicit migration instead of silent corruption.
"""
from django.db import migrations

AR_NORMALIZE = r"""
CREATE OR REPLACE FUNCTION ar_normalize_v1(t text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT lower(
    translate(
      -- Strip tashkeel (harakat), the superscript alef, Quranic marks and
      -- tatweel. None carry lexical weight; all destroy recall.
      regexp_replace(t, U&'[\064B-\065F\0670\06D6-\06ED\0640]', '', 'g'),
      -- Fold orthographic variants and both Arabic-Indic digit ranges.
      U&'\0623\0625\0622\0671\0649\0629\0624\0626'
        || U&'\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669'
        || U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9',
      U&'\0627\0627\0627\0627\064A\0647\0648\064A'
        || '0123456789'
        || '0123456789'
    )
  );
$$;
"""

DROP_AR_NORMALIZE = "DROP FUNCTION IF EXISTS ar_normalize_v1(text);"

GENERATED_COLUMNS = """
ALTER TABLE rag_chunk
  ADD COLUMN text_norm text
    GENERATED ALWAYS AS (ar_normalize_v1(text)) STORED;

ALTER TABLE rag_chunk
  ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', ar_normalize_v1(text))) STORED;
"""

DROP_GENERATED_COLUMNS = """
ALTER TABLE rag_chunk DROP COLUMN IF EXISTS tsv;
ALTER TABLE rag_chunk DROP COLUMN IF EXISTS text_norm;
"""

# 'simple' + ar_normalize rather than the 'arabic' snowball config, because:
#  - documents here are bilingual within a single page and a tsvector has one
#    dictionary chain per token type, so 'arabic' mangles the English
#  - orthographic folding fixes the actual recall killers; the stemmer fixes none
#  - the input is OCR output, so trigram fuzzy matching beats stemming
INDEXES = """
CREATE INDEX IF NOT EXISTS chunk_tsv  ON rag_chunk USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunk_trgm ON rag_chunk USING gin (text_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS chunk_meta ON rag_chunk USING gin (meta jsonb_path_ops);
"""

DROP_INDEXES = """
DROP INDEX IF EXISTS chunk_meta;
DROP INDEX IF EXISTS chunk_trgm;
DROP INDEX IF EXISTS chunk_tsv;
"""


class Migration(migrations.Migration):
    dependencies = [("rag", "0001_initial")]

    operations = [
        migrations.RunSQL(AR_NORMALIZE, DROP_AR_NORMALIZE),
        migrations.RunSQL(GENERATED_COLUMNS, DROP_GENERATED_COLUMNS),
        migrations.RunSQL(INDEXES, DROP_INDEXES),
    ]
