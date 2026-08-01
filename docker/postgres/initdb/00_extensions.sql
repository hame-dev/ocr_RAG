-- Extensions must exist before Django migrations run.
--   vector  : embedding storage + HNSW index
--   pg_trgm : fuzzy fallback for queries containing OCR typos
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- The LangGraph checkpointer keeps its tables out of the app's namespace.
CREATE SCHEMA IF NOT EXISTS langgraph;
