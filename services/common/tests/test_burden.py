from services.common.burden import hard_days, is_burdened


def _c(day, mood=None, energy=None):
    return {"day": day, "mood": mood, "energy": energy}


def test_three_low_mornings_of_the_last_five_is_a_signal():
    rows = [_c("2026-10-20", mood=2), _c("2026-10-21", energy=1), _c("2026-10-22", mood=4), _c("2026-10-23", mood=1), _c("2026-10-24", mood=5)]
    assert hard_days(rows) == 3 and is_burdened(rows)


def test_old_low_days_fall_out_of_the_window():
    rows = [_c(f"2026-10-0{d}", mood=1) for d in range(1, 4)] + [_c(f"2026-10-1{d}", mood=4) for d in range(5)]
    assert not is_burdened(rows)


def test_unanswered_questions_are_not_low():
    assert hard_days([_c("2026-10-20"), _c("2026-10-21", mood=3)]) == 0
