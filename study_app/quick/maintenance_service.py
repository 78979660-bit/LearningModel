"""Preserve the existing opt-in weekly collector on the serialized worker pool."""
from __future__ import annotations

from pathlib import Path


class StartupMaintenanceService:
    def __init__(self, db_path, initialize=None, *, due=None, collect=None):
        self.db_path = Path(db_path)
        self._initialize, self._due, self._collect = initialize, due, collect

    def run(self) -> dict:
        from study_app.data.database import get_setting

        if self._initialize is not None:
            self._initialize()
        if get_setting("weekly_practice_collection_enabled", False, db_path=self.db_path) is not True:
            return {"status": "disabled", "collected": False}
        from study_app.data.weekly_practice_collector import collection_is_due, collect_weekly_sources

        if not (self._due or collection_is_due)(self.db_path):
            return {"status": "not-due", "collected": False}
        (self._collect or collect_weekly_sources)(self.db_path)
        return {"status": "complete", "collected": True}
