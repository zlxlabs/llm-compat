import sys
from pathlib import Path

import pytest

# pytest may put ``tests/`` ahead of the repository root on ``sys.path``;
# release maintenance scripts are intentionally not installed into the wheel.
sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.release.resolve_release_actions import resolve_release_actions  # noqa: E402


@pytest.mark.parametrize(
    ("tag_exists", "release_exists", "expected_actions"),
    [
        (False, False, ("create_tag", "create_release")),
        (True, False, ("create_release",)),
        (True, True, ("skip",)),
        (False, True, ("fail",)),
    ],
)
def test_resolve_release_actions_covers_all_release_states(
    tag_exists: bool,
    release_exists: bool,
    expected_actions: tuple[str, ...],
) -> None:
    assert resolve_release_actions(tag_exists, release_exists) == expected_actions
