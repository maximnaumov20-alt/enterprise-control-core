#!/usr/bin/env python3
"""Keep dependency setup and licence-audit documentation aligned with committed locks."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> None:
    required_files = (
        "backend/requirements-runtime.lock",
        "backend/requirements-dev.lock",
        "frontend/package-lock.json",
    )
    for relative_path in required_files:
        path = ROOT / relative_path
        assert path.is_file() and path.stat().st_size > 0, (
            f"documented dependency lock is missing or empty: {relative_path}"
        )

    database_docs = (ROOT / "docs/DATABASE.md").read_text(encoding="utf-8")
    dependency_docs = (
        ROOT / "docs/licensing/DEPENDENCY-AUDIT.md"
    ).read_text(encoding="utf-8")

    assert "requirements-dev.lock" in database_docs
    assert "requirements-runtime.lock" in database_docs
    assert "requirements.txt" not in database_docs, (
        "database setup instructions still reference the removed combined requirements file"
    )
    for relative_path in required_files:
        assert relative_path in dependency_docs, (
            f"dependency audit does not document {relative_path}"
        )
    assert "pending bootstrap PR" not in dependency_docs
    assert "not yet enabled on `main`" not in dependency_docs


if __name__ == "__main__":
    main()
