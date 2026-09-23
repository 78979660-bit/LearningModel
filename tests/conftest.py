"""Global test isolation for per-user application data.

This environment variable must be set while pytest is importing test modules,
before ``study_app.paths`` computes its module-level paths.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


_TEST_DATA_ROOT = tempfile.TemporaryDirectory(prefix="learning-model-pytest-")
_TEST_ROOT = Path(_TEST_DATA_ROOT.name)
_EMPTY_LEGACY_ROOT = _TEST_ROOT / "empty-legacy"
_EMPTY_LEGACY_ROOT.mkdir()

# Always override inherited developer settings. Tests must never inspect or
# mutate a real LearningModel profile or auto-discover checkout user data.
os.environ["LEARNINGMODEL_DATA_ROOT"] = str(_TEST_ROOT / "user-data")
os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(_EMPTY_LEGACY_ROOT)
