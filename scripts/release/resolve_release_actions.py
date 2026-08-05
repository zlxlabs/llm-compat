"""Resolve the actions needed to complete a versioned GitHub release."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

ReleaseAction = str


def resolve_release_actions(
    tag_exists: bool,
    release_exists: bool,
) -> tuple[ReleaseAction, ...]:
    """Return the release actions for the current tag/Release state."""
    if not tag_exists and not release_exists:
        return ("create_tag", "create_release")
    if tag_exists and not release_exists:
        return ("create_release",)
    if tag_exists and release_exists:
        return ("skip",)
    return ("fail",)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _branch_message(version: str, actions: Sequence[ReleaseAction]) -> str:
    action_key = tuple(actions)
    if action_key == ("create_tag", "create_release"):
        return (
            f"版本 {version} 尚无 tag、也无 GitHub Release，执行：提取 notes、创建并推送 tag、"
            "创建 Release。"
        )
    if action_key == ("create_release",):
        return (
            f"版本 {version} 已有 tag 但没有 GitHub Release，执行：提取 notes、跳过打 tag、"
            "创建 Release（补齐半完成状态）。"
        )
    if action_key == ("skip",):
        return f"版本 {version} 已完整发布（已有 tag 和 GitHub Release），跳过。"
    return (
        f"错误：版本 {version} 没有 tag 但已有 GitHub Release，拒绝发布，请人工介入。"
    )


def _write_github_outputs(output_path: Path, actions: Sequence[ReleaseAction]) -> None:
    action_set = set(actions)
    outputs = {
        "actions": ",".join(actions),
        "create_tag": str("create_tag" in action_set).lower(),
        "create_release": str("create_release" in action_set).lower(),
        "skip": str("skip" in action_set).lower(),
        "fail": str("fail" in action_set).lower(),
    }
    with output_path.open("a", encoding="utf-8") as output_file:
        for name, value in outputs.items():
            output_file.write(f"{name}={value}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag-exists", required=True, type=_parse_bool)
    parser.add_argument("--release-exists", required=True, type=_parse_bool)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--summary-file", type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    actions = resolve_release_actions(args.tag_exists, args.release_exists)
    message = _branch_message(args.version, actions)
    state = (
        f"发布状态：tag_exists={str(args.tag_exists).lower()}，"
        f"release_exists={str(args.release_exists).lower()}；决策：{','.join(actions)}。"
    )

    print(message)
    print(state)
    if args.github_output is not None:
        _write_github_outputs(args.github_output, actions)
    if args.summary_file is not None:
        with args.summary_file.open("a", encoding="utf-8") as summary_file:
            summary_file.write(f"{message}\n{state}\n")

    return 1 if actions == ("fail",) else 0


if __name__ == "__main__":
    raise SystemExit(main())
