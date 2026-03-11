from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import PG_DSN


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    if not PG_DSN:
        raise RuntimeError("PG_DSN environment variable is required")

    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("set default_transaction_read_only = on")
        yield conn


@contextmanager
def get_cursor() -> Iterator[psycopg.Cursor]:
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur
