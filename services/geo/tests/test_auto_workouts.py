"""Tests for GPS-only workout detection."""

from services.geo.auto_workouts import detect_auto_workouts


def trail(mph: float, minutes: int, *, start: float = 1_700_000_000.0, spacing: int = 30, lat: float = 33.1667) -> list[dict]:
    """A straight, constant-speed trail with one fix every ``spacing`` seconds."""
    points = []
    step_m = mph * 0.44704 * spacing
    for index in range(int(minutes * 60 / spacing) + 1):
        points.append({
            "t": start + index * spacing,
            "lat": lat + index * step_m / 111_320.0,
            "lon": -111.5646,
            "spd": mph * 0.44704,
        })
    return points


def test_a_walk_is_detected_from_the_breadcrumbs():
    found = detect_auto_workouts(trail(3.0, 12))
    assert len(found) == 1
    walk = found[0]
    assert walk["activity_type"] == "walking"
    assert walk["duration_seconds"] >= 10 * 60
    assert walk["distance_miles"] >= 0.35
    assert walk["points"] >= 4


def test_a_run_and_a_ride_use_the_right_activity():
    run = detect_auto_workouts(trail(6.0, 10))[0]
    ride = detect_auto_workouts(trail(14.0, 12))[0]
    assert run["activity_type"] == "running"
    assert ride["activity_type"] == "cycling"


def test_a_short_stop_does_not_split_a_walk():
    points = trail(3.0, 6)
    pause_start = points[-1]["t"]
    # A minute standing at a corner, then another six minutes of walking.
    points.extend([
        {"t": pause_start + 30, "lat": points[-1]["lat"], "lon": points[-1]["lon"], "spd": 0},
        {"t": pause_start + 60, "lat": points[-1]["lat"], "lon": points[-1]["lon"], "spd": 0},
    ])
    points.extend(trail(3.0, 6, start=pause_start + 90, lat=points[-1]["lat"]))
    found = detect_auto_workouts(points)
    assert len(found) == 1
    assert found[0]["duration_seconds"] > 12 * 60


def test_a_long_gap_between_walks_is_two_workouts():
    first = trail(3.0, 12)
    second = trail(3.0, 12, start=first[-1]["t"] + 20 * 60)
    found = detect_auto_workouts(first + second)
    assert [w["activity_type"] for w in found] == ["walking", "walking"]
    assert found[0]["end_time"] < found[1]["start_time"]


def test_a_drive_ends_the_walk_window():
    walk = trail(3.0, 12)
    drive_start = walk[-1]["t"] + 30
    drive = trail(45.0, 3, start=drive_start)
    after = trail(3.0, 12, start=drive[-1]["t"] + 30)
    found = detect_auto_workouts(walk + drive + after)
    assert len(found) == 2
    assert all(w["activity_type"] == "walking" for w in found)


def test_an_existing_workout_suppresses_the_same_walk():
    walk = trail(3.0, 12)
    found = detect_auto_workouts(
        walk,
        existing_workouts=[{"start_time": walk[0]["t"], "end_time": walk[-1]["t"]}],
    )
    assert found == []


def test_a_trip_suppresses_a_candidate():
    ride = trail(14.0, 12)
    found = detect_auto_workouts(
        ride,
        recent_trips=[{"start_time": ride[0]["t"] + 60, "end_time": ride[-1]["t"] - 60}],
    )
    assert found == []


def test_a_dismissed_start_stays_dismissed():
    walk = trail(3.0, 12)
    found = detect_auto_workouts(walk, dismissed_starts=[walk[0]["t"] + 60])
    assert found == []


def test_too_short_or_too_slow_is_not_a_workout():
    assert detect_auto_workouts(trail(3.0, 4)) == []
    assert detect_auto_workouts(trail(0.4, 30)) == []
