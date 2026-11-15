"""What a child is told when a parent answers ticked-off chores."""
from services.common.screen_time import chore_answer_tag, chore_answer_words


def test_one_approval_names_the_chore():
    assert chore_answer_words(True, "Trash", ["Trash"]) == ("Chore approved", "“Trash” was done right. Nice work!")


def test_several_approvals_the_same_day_are_one_notice():
    title, body = chore_answer_words(True, "Toys", ["Bed", "Teeth", "Toys"])
    assert title == "3 chores approved today"
    assert body == "“Bed”, “Teeth” and “Toys” were done right. Nice work!"


def test_a_long_day_is_counted_not_listed():
    _, body = chore_answer_words(True, "E", ["A", "B", "C", "D", "E"])
    assert body.startswith("“A”, “B”, “C” and 2 more")


def test_a_redo_is_its_own_notice():
    assert chore_answer_words(False, "Dishes", [])[0] == "Have another go"
    assert chore_answer_tag("kid", True) == "chores-approved:kid"
    assert chore_answer_tag("kid", False) == "screen-time:kid"
