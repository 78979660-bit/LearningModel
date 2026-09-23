from __future__ import annotations

import json
from pathlib import Path

from study_app.data.database import DEFAULT_DB_PATH, connect_readonly


def database_snapshot(db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    with connect_readonly(db_path) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            for table in ["subjects", "modules", "topics", "learning_records", "problem_attempts"]
        }
        settings = {
            row["key"]: json.loads(row["value_json"])
            for row in connection.execute("SELECT key, value_json FROM app_settings")
        }
    return {"counts": counts, "settings": settings}
