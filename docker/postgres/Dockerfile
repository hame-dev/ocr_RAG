# pgvector/pgvector ships multi-arch images, so this works on arm64.
#
# Deliberately pinned to pg17, NOT pg18. Postgres refuses to start against a
# data directory written by a different major version, so bumping this tag
# would make the existing `pgdata` volume unreadable and needs a dump/restore:
#     docker compose exec db pg_dumpall -U ocrrag > dump.sql
#     docker compose down -v && <bump tag> && docker compose up -d db
#     docker compose exec -T db psql -U ocrrag < dump.sql
# `-trixie` moves the OS base forward (newer openssl/glibc) while keeping the
# server major fixed, so the volume stays readable.
FROM pgvector/pgvector:0.8.6-pg17-trixie

COPY initdb/ /docker-entrypoint-initdb.d/
