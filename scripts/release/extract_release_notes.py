"""Read the project version and extract its CHANGELOG release notes."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


class ReleaseNotesError(ValueError):
    """Raised when the release metadata cannot be extracted safely."""


_FENCE_START_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def read_project_version(pyproject_path: Path) -> str:
    """Return the static project version from a pyproject.toml file."""
    project_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = project_data.get("project")
    if not isinstance(project, dict):
        raise ReleaseNotesError("pyproject.toml 缺少 [project] 配置节")

    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseNotesError("pyproject.toml 的 [project].version 必须是非空字符串")
    return version


def _is_release_heading(line: str, version: str) -> bool:
    """Return whether a line is the requested Keep a Changelog heading."""
    heading_pattern = re.compile(
        rf"^## \[{re.escape(version)}\](?:[ \t]+-[^\r\n]*)?[ \t]*$"
    )
    return heading_pattern.fullmatch(line) is not None


def _update_fence_state(line: str, fence: tuple[str, int] | None) -> tuple[str, int] | None:
    """Track Markdown fenced code blocks while scanning headings."""
    fence_match = _FENCE_START_RE.match(line)
    if fence is None:
        if fence_match is None:
            return None
        marker = fence_match.group(1)
        return marker[0], len(marker)

    if fence_match is None:
        return fence

    marker = fence_match.group(1)
    if marker[0] != fence[0] or len(marker) < fence[1]:
        return fence
    if line[fence_match.end() :].strip():
        return fence
    return None


def extract_release_notes(changelog_path: Path, version: str) -> str:
    """Extract one version section without cutting nested headings or code blocks."""
    lines = changelog_path.read_text(encoding="utf-8").splitlines(keepends=True)

    target_index: int | None = None
    fence: tuple[str, int] | None = None
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        if fence is not None:
            fence = _update_fence_state(line, fence)
            continue
        if _is_release_heading(line, version):
            target_index = index
            break
        fence = _update_fence_state(line, fence)

    expected_heading = f"## [{version}]"
    if target_index is None:
        raise ReleaseNotesError(
            f"CHANGELOG.md 缺少版本 {version} 的发布节；期望的节标题字面量：{expected_heading}"
        )

    notes_end = len(lines)
    fence = None
    for index in range(target_index + 1, len(lines)):
        line = lines[index].rstrip("\r\n")
        if fence is not None:
            fence = _update_fence_state(line, fence)
            continue
        if line.startswith("## "):
            notes_end = index
            break
        fence = _update_fence_state(line, fence)

    return "".join(lines[target_index + 1 : notes_end]).strip()


def _write_text(value: str, output_path: Path | None) -> None:
    """Write a CLI value to a file or stdout with one trailing newline."""
    value_with_newline = value if value.endswith("\n") else f"{value}\n"
    if output_path is None:
        sys.stdout.write(value_with_newline)
    else:
        output_path.write_text(value_with_newline, encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="path to pyproject.toml (default: pyproject.toml)",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="path to CHANGELOG.md (default: CHANGELOG.md)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--version",
        dest="print_version",
        action="store_true",
        help="print the project version instead of release notes",
    )
    mode.add_argument(
        "--output",
        type=Path,
        help="write release notes to this file instead of stdout",
    )
    return parser.parse_args()


def main() -> int:
    """Run the release metadata extraction command."""
    args = _parse_args()
    try:
        version = read_project_version(args.pyproject)
        if args.print_version:
            _write_text(version, None)
        else:
            notes = extract_release_notes(args.changelog, version)
            _write_text(notes, args.output)
    except (OSError, tomllib.TOMLDecodeError, ReleaseNotesError) as error:
        print(f"发布元数据提取失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
