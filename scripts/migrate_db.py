#!/usr/bin/env python3
"""Copy every row from the old Postgres database into the new one.

Purpose: move off Render's free Postgres (which expires 30 days after
creation, then gets deleted) onto an always-free database such as Neon.
This avoids needing `pg_dump` / `psql` installed locally.

Usage:
    uv run --extra postgres python scripts/migrate_db.py <SOURCE_URL> <TARGET_URL> [--force]

Where to get the URLs:
    SOURCE — Render dashboard -> due-board-db -> Connect -> *External* Database URL
    TARGET — Neon dashboard -> Connection string (use the pooled `-pooler` host)

The target schema is created from the app's own models, so it always matches
what the app expects — even if the source database is older. Only columns
present on both sides are copied. Re-run with --force to overwrite a target
that already contains rows.
"""

from __future__ import annotations

import sys

from sqlalchemy import MetaData, create_engine, inspect, text

from web.config import normalize_database_url
from web.db import Base


def fk_safe_order(insp) -> list[str]:
    """Table names sorted so each table comes after the tables it references."""
    names = insp.get_table_names()
    deps: dict[str, set[str]] = {}
    for name in names:
        refs: set[str] = set()
        for fk in insp.get_foreign_keys(name):
            ref = fk.get("referred_table")
            if ref and ref != name and ref in names:
                refs.add(ref)
        deps[name] = refs

    ordered: list[str] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(n for n, refs in remaining.items() if not (refs - set(ordered)))
        if not ready:
            # Circular foreign keys — fall back to a stable arbitrary order.
            ordered.extend(sorted(remaining))
            break
        for name in ready:
            ordered.append(name)
            remaining.pop(name)
    return ordered


def reset_sequence(conn, table: str, column: str) -> None:
    """Postgres sequences don't move when ids are inserted explicitly, so the
    next INSERT would collide. Push the sequence past the copied max id."""
    conn.execute(
        text(
            'SELECT setval(seq, GREATEST(COALESCE((SELECT MAX("{col}") FROM "{tbl}"), 1), 1)) '
            "FROM (SELECT pg_get_serial_sequence(:tbl_name, :col_name)::regclass AS seq) AS s "
            "WHERE seq IS NOT NULL".format(col=column, tbl=table)
        ),
        {"tbl_name": table, "col_name": column},
    )


def main() -> int:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if len(positional) != 2:
        print(__doc__)
        return 2

    src_url, dst_url = (normalize_database_url(a) for a in positional)
    # pool_pre_ping: managed Postgres (Neon especially) closes idle connections,
    # and a recycled-then-dead connection shows up as "SSL connection has been
    # closed unexpectedly" in the middle of the copy.
    src = create_engine(src_url, pool_pre_ping=True)
    dst = create_engine(dst_url, pool_pre_ping=True)

    # Build the target from the app's current models, not from the source's
    # possibly-outdated schema.
    Base.metadata.create_all(bind=dst)

    src_insp = inspect(src)
    dst_insp = inspect(dst)
    order = [t for t in fk_safe_order(src_insp) if dst_insp.has_table(t)]
    # Reflect the shared columns up front: doing it mid-transaction opens extra
    # connections and burns time while the write transaction sits idle.
    src_columns = {t: {c["name"] for c in src_insp.get_columns(t)} for t in order}
    dst_columns = {t: {c["name"] for c in dst_insp.get_columns(t)} for t in order}

    with src.connect() as sconn, dst.begin() as dconn:
        existing = {
            t: dconn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar_one()
            for t in order
        }
        if not force and any(existing.values()):
            print("Target already contains rows:")
            for table, count in existing.items():
                if count:
                    print(f"  {table}: {count}")
            print("\nRe-run with --force to overwrite.")
            return 1

        # Delete children before parents so FK constraints stay satisfied.
        for table in reversed(order):
            dconn.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))

        total = 0
        for table in order:
            cols = sorted(src_columns[table] & dst_columns[table])
            if not cols:
                print(f"  {table}: no shared columns, skipped")
                continue

            quoted = ", ".join(f'"{c}"' for c in cols)
            rows = sconn.execute(text(f'SELECT {quoted} FROM "{table}"')).mappings().all()
            if not rows:
                print(f"  {table}: 0 rows")
                continue

            placeholders = ", ".join(f":{c}" for c in cols)
            dconn.execute(
                text(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'),
                [dict(row) for row in rows],
            )
            total += len(rows)
            print(f"  {table}: {len(rows)} rows")

        for table in order:
            pk_cols = dst_insp.get_pk_constraint(table).get("constrained_columns") or []
            if len(pk_cols) == 1:
                reset_sequence(dconn, table, pk_cols[0])

        print(f"\nDone — {total} row(s) copied into the target database.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
