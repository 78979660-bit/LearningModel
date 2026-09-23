from __future__ import annotations

import argparse

from study_app.data.file_locator import locate_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Find local files from a rough natural-language hint.")
    parser.add_argument("query", help="Example: 坚果云 chap6")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    matches = locate_files(args.query, limit=args.limit)
    if not matches:
        print("No matches.")
        return
    for item in matches:
        print(f"{item.score:>3}  {item.path}  {item.reason}")


if __name__ == "__main__":
    main()
