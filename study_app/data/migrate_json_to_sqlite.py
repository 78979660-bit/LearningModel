from __future__ import annotations

from study_app.data.database import DEFAULT_DB_PATH, get_counts, import_current_json_files


def main() -> None:
    path = import_current_json_files()
    counts = get_counts(path)
    print(f"SQLite database: {path}")
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
