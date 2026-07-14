import pytest

from services.gateway.agent_loop import should_persist_learning


@pytest.mark.parametrize("result", [
    # A build mission that ran out of time and wrote nothing must NOT be
    # counted as a meaningful (successful) result -- it was a false-positive
    # "completed" before this guard existed.
    "Status: Incomplete - interrupted by time limit. The workspace was created, "
    "but all subsequent steps were not completed. Game code not written. "
    "Headless self-test not implemented. Code not committed/pushed to GitHub.",
    "Mission incomplete: repository creation not completed and no files were written.",
    "I hit the time limit before the project was implemented.",
    "The game was not created and the CI workflow was not implemented.",
])
def test_incomplete_summary_is_not_meaningful(result):
    assert should_persist_learning(result) is False


@pytest.mark.parametrize("result", [
    "Created raven-3d-shooter-python, implemented the full game, ran --selftest "
    "which printed GAME_OK, and pushed to GitHub. CI is green.",
    "Successfully built and pushed the project. All tests pass.",
])
def test_genuine_success_is_meaningful(result):
    assert should_persist_learning(result) is True


def test_empty_result_is_not_meaningful():
    assert should_persist_learning("") is False
    assert should_persist_learning("None") is False
