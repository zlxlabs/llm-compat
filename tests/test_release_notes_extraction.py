import sys
from pathlib import Path

import pytest

# pytest imports test modules with ``tests/`` as the first path entry; the
# maintenance script is intentionally not part of the installed library.
sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.release.extract_release_notes import (
    ReleaseNotesError,
    extract_release_notes,
    read_project_version,
)  # noqa: E402


def _write_release_files(tmp_path: Path, version: str, changelog: str) -> tuple[Path, Path]:
    pyproject_path = tmp_path / "pyproject.toml"
    changelog_path = tmp_path / "CHANGELOG.md"
    pyproject_path.write_text(
        f'[project]\nname = "example"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    changelog_path.write_text(changelog, encoding="utf-8")
    return pyproject_path, changelog_path


def test_extract_release_notes_preserves_paragraphs_code_and_nested_headings(
    tmp_path: Path,
) -> None:
    pyproject_path, changelog_path = _write_release_files(
        tmp_path,
        "0.8.0",
        """# Changelog

## [0.8.0] - 2026-08-05

第一段落。

### Fixed

- 修复一个问题。

```python
print(\"## 这不是 CHANGELOG 节标题\")
```

第二段落。

## [0.7.0] - 2026-07-01

旧版本内容。
""",
    )

    version = read_project_version(pyproject_path)

    assert extract_release_notes(changelog_path, version) == """第一段落。

### Fixed

- 修复一个问题。

```python
print("## 这不是 CHANGELOG 节标题")
```

第二段落。"""


def test_extract_release_notes_fails_with_explicit_missing_section_error(tmp_path: Path) -> None:
    _, changelog_path = _write_release_files(
        tmp_path,
        "0.8.0",
        """# Changelog

## [0.7.0] - 2026-07-01

旧版本内容。
""",
    )

    with pytest.raises(ReleaseNotesError, match=r"0\.8\.0.*## \[0\.8\.0\]"):
        extract_release_notes(changelog_path, "0.8.0")


def test_extract_release_notes_reads_to_end_when_version_is_last_section(tmp_path: Path) -> None:
    _, changelog_path = _write_release_files(
        tmp_path,
        "0.8.0",
        """# Changelog

## [0.7.0] - 2026-07-01

旧版本内容。

## [0.8.0]

### Changed

- 最后一节没有后继二级标题。
""",
    )

    assert extract_release_notes(changelog_path, "0.8.0") == """### Changed

- 最后一节没有后继二级标题。"""


def test_extract_release_notes_supports_pre_release_versions_as_literal_tags(
    tmp_path: Path,
) -> None:
    pyproject_path, changelog_path = _write_release_files(
        tmp_path,
        "0.9.0rc1",
        """# Changelog

## [0.9.0rc1] - 2026-08-06

预发布版本说明。
""",
    )

    version = read_project_version(pyproject_path)

    assert version == "0.9.0rc1"
    assert extract_release_notes(changelog_path, version) == "预发布版本说明。"
