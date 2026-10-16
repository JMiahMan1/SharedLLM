"""The words and arithmetic of geo's ``health`` notices.

Two notices: the daily step goal reached (once per person per day) and a
weekly summary on Sunday evening comparing the week (Monday to Sunday) with
the one before. Geo's main module decides when to send them; this module only
works out what they say.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

#: Local hour on Sunday from which the weekly summary goes out.
WEEKLY_SUMMARY_HOUR = int(os.environ.get("WEEKLY_SUMMARY_HOUR", "19"))
#: Marks a person's day as announced (the goal) or week as summarised.
GOAL_SENT_KEY = "geo:notice:step_goal:{user}:{day}"
WEEK_SENT_KEY = "geo:notice:week:{user}:{week}"


def week_start(day: date) -> date:
    """The Monday of ``day``'s week."""
    return day - timedelta(days=day.weekday())


def week_totals(daily: dict[str, int], today: date) -> tuple[int, int, int]:
    """(this week's steps, last week's steps, days with steps this week),
    weeks running Monday to Sunday."""
    start = week_start(today)
    this_week = last_week = days = 0
    for day_str, steps in daily.items():
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            continue
        if start <= day <= today:
            this_week += steps
            days += int(steps > 0)
        elif start - timedelta(days=7) <= day < start:
            last_week += steps
    return this_week, last_week, days


def goal_text(steps: int, goal: int) -> tuple[str, str]:
    return "Step goal reached", f"{steps:,} steps today, past your goal of {goal:,}."


def weekly_text(this_week: int, last_week: int, days: int) -> tuple[str, str]:
    body = f"{this_week:,} steps this week"
    if days:
        body += f", {this_week // days:,} a day"
    if last_week:
        change = round((this_week - last_week) * 100 / last_week)
        if change > 0:
            body += f". Up {change}% on last week ({last_week:,})."
        elif change < 0:
            body += f". Down {-change}% on last week ({last_week:,})."
        else:
            body += f". The same as last week ({last_week:,})."
    else:
        body += "."
    return "Your week in steps", body
