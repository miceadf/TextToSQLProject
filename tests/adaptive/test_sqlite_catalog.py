from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.adaptive.catalog import (
    CatalogErrorCode,
    SQLiteAdapter,
    SQLiteAffinity,
    SchemaInspectionError,
)


class SQLiteAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "catalog.sqlite3"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE departments (
                    department_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE courses (
                    course_code TEXT PRIMARY KEY,
                    title VARCHAR(200) NOT NULL,
                    credits REAL DEFAULT 3.0,
                    department_id INTEGER,
                    title_length INTEGER GENERATED ALWAYS AS (length(title)) STORED,
                    FOREIGN KEY (department_id)
                        REFERENCES departments(department_id)
                        ON UPDATE CASCADE
                        ON DELETE SET NULL
                );
                INSERT INTO departments VALUES (1, '컴퓨터융합학부');
                INSERT INTO courses(course_code, title, credits, department_id)
                VALUES
                    ('CS101', '데이터베이스시스템과응용', 3.0, 1),
                    ('CS102', '운영체제', 3.0, 1),
                    ('CS103', '컴퓨터네트워크', 3.0, 1);
                CREATE VIEW course_titles AS SELECT title FROM courses;
                """
            )
            connection.commit()
        finally:
            connection.close()

        self.adapter = SQLiteAdapter(
            {"catalog-db": self.database_path},
            sample_limit=2,
            max_sample_length=8,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_inspects_tables_columns_primary_keys_and_foreign_keys(self) -> None:
        catalog = self.adapter.inspect("catalog-db")

        self.assertEqual(catalog.dialect, "sqlite")
        self.assertEqual([table.name for table in catalog.tables], ["courses", "departments"])
        self.assertEqual(len(catalog.schema_fingerprint), 64)

        courses = next(table for table in catalog.tables if table.name == "courses")
        self.assertEqual(courses.primary_key, ["course_code"])
        self.assertEqual([column.name for column in courses.columns], [
            "course_code",
            "title",
            "credits",
            "department_id",
            "title_length",
        ])

        by_name = {column.name: column for column in courses.columns}
        self.assertEqual(by_name["course_code"].affinity, SQLiteAffinity.TEXT)
        self.assertEqual(by_name["credits"].affinity, SQLiteAffinity.REAL)
        self.assertEqual(by_name["credits"].default_sql, "3.0")
        self.assertTrue(by_name["title_length"].generated)
        self.assertEqual(by_name["title"].sample_values, [])

        self.assertEqual(len(courses.foreign_keys), 1)
        foreign_key = courses.foreign_keys[0]
        self.assertEqual(foreign_key.column, "department_id")
        self.assertEqual(foreign_key.referenced_table, "departments")
        self.assertEqual(foreign_key.referenced_column, "department_id")
        self.assertEqual(foreign_key.on_update, "CASCADE")
        self.assertEqual(foreign_key.on_delete, "SET NULL")

    def test_samples_are_opt_in_bounded_and_truncated(self) -> None:
        catalog = self.adapter.inspect("catalog-db", include_samples=True)
        courses = next(table for table in catalog.tables if table.name == "courses")
        title = next(column for column in courses.columns if column.name == "title")
        generated = next(column for column in courses.columns if column.name == "title_length")

        self.assertEqual(len(title.sample_values), 2)
        self.assertEqual(title.sample_values[0], "데이터베이스시스")
        self.assertTrue(all(len(value) <= 8 for value in title.sample_values))
        self.assertEqual(generated.sample_values, [])

    def test_fingerprint_ignores_samples_but_changes_with_schema(self) -> None:
        without_samples = self.adapter.inspect("catalog-db")
        with_samples = self.adapter.inspect("catalog-db", include_samples=True)
        self.assertEqual(
            without_samples.schema_fingerprint,
            with_samples.schema_fingerprint,
        )

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("ALTER TABLE courses ADD COLUMN semester TEXT")
            connection.commit()
        finally:
            connection.close()

        changed = self.adapter.inspect("catalog-db")
        self.assertNotEqual(
            without_samples.schema_fingerprint,
            changed.schema_fingerprint,
        )

    def test_missing_database_is_reported(self) -> None:
        adapter = SQLiteAdapter({"missing": Path(self.temp_dir.name) / "missing.sqlite3"})

        with self.assertRaises(SchemaInspectionError) as raised:
            adapter.inspect("missing")

        self.assertEqual(raised.exception.code, CatalogErrorCode.DATABASE_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
