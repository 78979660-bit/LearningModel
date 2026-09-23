from __future__ import annotations

import argparse
from datetime import date

from study_app.data.database import add_learning_record


def main() -> None:
    parser = argparse.ArgumentParser(description="Add one learning record to SQLite.")
    parser.add_argument("note", help="Short learning note.")
    parser.add_argument("--subject", required=True, help="Subject name.")
    parser.add_argument("--module", default="", help="Module name.")
    parser.add_argument("--topic", default="", help="Topic name.")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD.")
    parser.add_argument("--activity", default="review", help="Activity type.")
    parser.add_argument("--source", default="outside_class", help="Record source.")
    parser.add_argument("--score", default="", help="Optional score, 0-100.")
    parser.add_argument("--duration", default="", help="Optional duration in minutes.")
    args = parser.parse_args()

    record_id = add_learning_record(
        {
            "date": args.date,
            "subject": args.subject,
            "module": args.module,
            "topic": args.topic,
            "activity": args.activity,
            "source": args.source,
            "score": args.score,
            "duration_minutes": args.duration,
            "note": args.note,
        }
    )
    print(f"Inserted learning_records.id={record_id}")


if __name__ == "__main__":
    main()
