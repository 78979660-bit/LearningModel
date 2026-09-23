from __future__ import annotations

import argparse
import json
from pathlib import Path

from study_app.data.practice_repository import import_practice_problem


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one or more practice problems into the local practice bank.")
    parser.add_argument("--json", dest="json_path", help="Path to a JSON file containing one problem or {'problems': [...]}.")
    parser.add_argument("--template-id")
    parser.add_argument("--title")
    parser.add_argument("--statement")
    parser.add_argument("--difficulty", type=float, default=60)
    parser.add_argument("--subject")
    parser.add_argument("--topic")
    parser.add_argument("--tags", default="")
    parser.add_argument("--source-title", default="Codex 手动导入")
    parser.add_argument("--source-url", default="")
    args = parser.parse_args()

    if args.json_path:
        payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
        problems = payload.get("problems") if isinstance(payload, dict) else None
        if problems is None:
            problems = [payload]
    else:
        if not args.title or not args.statement:
            raise SystemExit("--title and --statement are required when --json is not used")
        tags = [item.strip() for item in args.tags.replace("、", ",").split(",") if item.strip()]
        problems = [
            {
                "template_id": args.template_id,
                "title": args.title,
                "statement": args.statement,
                "difficulty_score": args.difficulty,
                "subject": args.subject,
                "topic": args.topic,
                "tags": tags,
                "source": {
                    "type": "codex",
                    "title": args.source_title,
                    "url": args.source_url,
                },
            }
        ]

    ids = [import_practice_problem(problem) for problem in problems]
    print(json.dumps({"imported_ids": ids}, ensure_ascii=False))


if __name__ == "__main__":
    main()
