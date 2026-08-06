"""Postgres connection handling for the data access layer.

This module (plus db/queries.py) is the only place in this project that
imports psycopg or knows a Postgres connection exists -- see CLAUDE.md's
"Postgres data layer" section, "Data access layer (db/)" subsection.

DSN defaults to the local, native Homebrew Postgres setup documented in
CLAUDE.md ("Local setup"): trust-auth, no password, connecting as the
scrutiny_app role (not the owning/migration role) so the Row-Level Security
policies in db/schema.sql apply unconditionally -- see CLAUDE.md's
"Row-Level Security" subsection for why the role matters, not just the
policy. Override with the SCRUTINY_ENGINE_DB_DSN env var for any other
environment (e.g. the docker-compose.yml Postgres, or a future non-local
instance).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Cursor

DEFAULT_DSN = "postgresql://scrutiny_app@localhost/scrutiny_engine"
DSN = os.environ.get("SCRUTINY_ENGINE_DB_DSN", DEFAULT_DSN)


@contextmanager
def client_scoped_connection(client_id: str) -> Iterator[Cursor]:
    """Opens a connection, sets app.current_client_id for this transaction
    only (SET LOCAL, not SET -- so a reused/pooled connection can never leak
    one client's scope into another request), and yields a cursor. Commits
    on clean exit, rolls back and re-raises on any exception, always closes
    the connection.

    client_id is required and must be non-empty: an unscoped connection
    would rely entirely on RLS's "current_setting unset -> NULL -> matches
    nothing" fallback (see CLAUDE.md) to avoid returning cross-client data,
    which is a safety net for a coding mistake elsewhere, not something to
    rely on by skipping scoping here.
    """
    if not client_id:
        raise ValueError("client_id must be a non-empty string")

    conn = psycopg.connect(DSN)
    try:
        with conn.cursor() as cur:
            # SET LOCAL itself doesn't accept a bind parameter for its
            # value (it's a utility statement, not a regular parameterized
            # query) -- set_config() is a normal function call that does,
            # and its third argument (is_local=true) gives the identical
            # transaction-scoped-only behavior SET LOCAL would.
            cur.execute("SELECT set_config('app.current_client_id', %s, true)", (client_id,))
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
