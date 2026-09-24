import sqlite3

import pytest

from study_app.data.database import initialize_database
from study_app.data.subject_repository import (
    SubjectCatalogRepository,
    install_subject_lifecycle_schema,
)


def test_public_catalog_transaction_commits_rolls_back_and_closes(tmp_path):
    path = tmp_path / "catalog.sqlite"
    initialize_database(path)
    install_subject_lifecycle_schema(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE transaction_probe (value INTEGER NOT NULL)")

    repository = SubjectCatalogRepository(path)
    with repository.transaction(readonly=False) as connection:
        connection.execute("INSERT INTO transaction_probe(value) VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")

    with pytest.raises(RuntimeError, match="cancel"):
        with repository.transaction(readonly=False) as connection:
            connection.execute("INSERT INTO transaction_probe(value) VALUES (2)")
            raise RuntimeError("cancel")

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM transaction_probe").fetchall() == [(1,)]


def test_read_transaction_keeps_one_snapshot_across_queries(tmp_path):
    path = tmp_path / "catalog.sqlite"
    initialize_database(path)
    install_subject_lifecycle_schema(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE transaction_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO transaction_probe(value) VALUES (1)")

    repository = SubjectCatalogRepository(path)
    with repository.transaction(snapshot=True) as reader:
        assert [row[0] for row in reader.execute("SELECT value FROM transaction_probe")] == [1]
        with sqlite3.connect(path, timeout=1) as writer:
            writer.execute("INSERT INTO transaction_probe(value) VALUES (2)")
        assert [row[0] for row in reader.execute("SELECT value FROM transaction_probe")] == [1]
    with repository.transaction(snapshot=True) as reader:
        assert [row[0] for row in reader.execute("SELECT value FROM transaction_probe")] == [1, 2]


def test_immediate_catalog_transaction_requires_explicit_commit(tmp_path):
    path = tmp_path / "catalog.sqlite"
    initialize_database(path)
    install_subject_lifecycle_schema(path)
    repository = SubjectCatalogRepository(path)

    connection = repository.begin_immediate()
    try:
        assert connection.in_transaction
        connection.rollback()
    finally:
        connection.close()
