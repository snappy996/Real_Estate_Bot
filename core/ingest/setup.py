"""One-time database setup: apply migrations, then load the seed data.

    python -m core.ingest.setup
"""

from core.db import apply_migrations, transaction
from core.ingest.load import load_seeds


def main():
    print("migrations:", apply_migrations())
    with transaction() as conn:
        print("seeds:", load_seeds(conn))
        row = conn.execute(
            "SELECT count(*) FROM localities WHERE is_covered"
        ).fetchone()
        print(f"covered localities: {row[0]}")
        print("check: 'Kokapeta' resolves to",
              conn.execute("SELECT locality_id FROM resolve_locality('Kokapeta', 'hyd')")
              .fetchone())


if __name__ == "__main__":
    main()
