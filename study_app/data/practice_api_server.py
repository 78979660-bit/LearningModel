from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from study_app.data.practice_repository import (
    find_practice_problems,
    import_practice_problem,
    list_practice_templates,
    practice_context,
    seed_practice_bank,
)
from study_app.data.weekly_practice_collector import collection_status, collect_weekly_sources
from study_app.data.practice_candidate_processor import process_candidates
from study_app.data.collection_backlog import list_collection_backlog


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class PracticeBankHandler(BaseHTTPRequestHandler):
    server_version = "StudyPracticeBank/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                self.write_json({"status": "ok"})
            elif parsed.path == "/templates":
                self.write_json({"templates": list_practice_templates()})
            elif parsed.path == "/problems":
                self.write_json(
                    {
                        "problems": find_practice_problems(
                            template_id=one(query, "template_id"),
                            subject=one(query, "subject"),
                            topic=one(query, "topic"),
                            difficulty=optional_float(one(query, "difficulty")),
                            limit=optional_int(one(query, "limit"), 8),
                        )
                    }
                )
            elif parsed.path == "/context":
                self.write_json(
                    practice_context(
                        template_id=one(query, "template_id"),
                        subject=one(query, "subject"),
                        topic=one(query, "topic"),
                        difficulty=optional_float(one(query, "difficulty")),
                        limit=optional_int(one(query, "limit"), 4),
                    )
                )
            elif parsed.path == "/collection/status":
                self.write_json(collection_status())
            elif parsed.path == "/collection/candidates":
                from study_app.data.database import connect

                with connect() as connection:
                    rows = connection.execute(
                        """
                        SELECT id, source_name, institution, subject_hint, topic_hint,
                               title, url, document_type, quality_score,
                               estimated_difficulty, status, discovered_at, last_seen_at
                        FROM practice_collection_candidates
                        ORDER BY quality_score DESC, estimated_difficulty DESC, id
                        LIMIT ?
                        """,
                        (optional_int(one(query, "limit"), 50),),
                    ).fetchall()
                self.write_json({"candidates": [dict(row) for row in rows]})
            elif parsed.path == "/collection/backlog":
                self.write_json(
                    {
                        "backlog": list_collection_backlog(
                            status=one(query, "status") or "pending",
                            limit=optional_int(one(query, "limit"), 50),
                        )
                    }
                )
            else:
                self.write_json({"error": "not_found", "path": parsed.path}, status=404)
        except Exception as error:
            self.write_json({"error": str(error)}, status=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/collection/run":
                self.write_json(collect_weekly_sources())
                return
            if parsed.path == "/collection/process":
                payload = self.read_json()
                self.write_json(
                    process_candidates(
                        limit=int(payload.get("limit") or 8),
                        min_quality=int(payload.get("min_quality") or 85),
                    )
                )
                return
            if parsed.path != "/problems/import":
                self.write_json({"error": "not_found", "path": parsed.path}, status=404)
                return
            payload = self.read_json()
            if isinstance(payload, dict) and isinstance(payload.get("problems"), list):
                ids = [import_practice_problem(item) for item in payload["problems"]]
            elif isinstance(payload, dict):
                ids = [import_practice_problem(payload)]
            else:
                raise ValueError("request body must be a JSON object")
            self.write_json({"imported_ids": ids})
        except Exception as error:
            self.write_json({"error": str(error)}, status=400)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        from study_app.data.text_integrity import validate_text_integrity

        validate_text_integrity(payload, context="题库 API 请求")
        return payload

    def write_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    return float(value)


def optional_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    return max(1, min(50, int(value)))


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    seed_practice_bank()
    server = ThreadingHTTPServer((host, port), PracticeBankHandler)
    print(f"Practice bank API listening on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local practice bank API.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
