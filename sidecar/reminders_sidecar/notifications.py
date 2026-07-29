"""
Decides what to toast. The Rust side only fires what this returns.

Keeping the policy here means the sleep/wake behaviour is unit-testable without
a Windows box, a real clock, or an actual notification service.

The machine sleeping through due times is handled deliberately rather than
discovered in production:

  * Anything overdue by more than `stale_after_minutes` (default 60) is treated
    as missed while the machine was away. Those never produce individual
    toasts -- they collapse into one summary. Waking up to fourteen separate
    toasts for things due overnight is worse than useless.

  * Freshly-due reminders toast individually, but only up to
    `max_individual` (default 3). Beyond that they also collapse into a
    summary, so a batch of simultaneous due times can't flood the tray.

Every reminder handed back is marked notified in the same call, so a crash
between deciding and firing costs at most one toast rather than causing a loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Cache, from_iso


@dataclass
class Toast:
    title: str
    body: str
    reminder_id: Optional[str] = None
    kind: str = "reminder"  # reminder | summary


@dataclass
class NotificationPlan:
    toasts: list[Toast] = field(default_factory=list)
    notified_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "toasts": [
                {
                    "title": t.title,
                    "body": t.body,
                    "reminder_id": t.reminder_id,
                    "kind": t.kind,
                }
                for t in self.toasts
            ],
            "notified_ids": self.notified_ids,
        }


def _fmt_due(iso: Optional[str]) -> str:
    dt = from_iso(iso)
    if dt is None:
        return ""
    return dt.astimezone().strftime("%a %d %b, %H:%M")


def plan_notifications(
    cache: Cache,
    now: Optional[datetime] = None,
    stale_after_minutes: int = 60,
    max_individual: int = 3,
) -> NotificationPlan:
    """Work out which toasts to fire this tick, and mark them notified."""
    now = now or datetime.now(timezone.utc)
    due = cache.due_unnotified(now)
    plan = NotificationPlan()
    if not due:
        return plan

    stale_cutoff = now - timedelta(minutes=stale_after_minutes)
    missed: list[dict] = []
    fresh: list[dict] = []
    for r in due:
        dt = from_iso(r.get("due_date"))
        if dt is not None and dt < stale_cutoff:
            missed.append(r)
        else:
            fresh.append(r)

    if missed:
        titles = ", ".join(r["title"] for r in missed[:3] if r.get("title"))
        more = len(missed) - 3
        if more > 0:
            titles += f", and {more} more"
        plan.toasts.append(
            Toast(
                title=f"{len(missed)} reminder{'s' if len(missed) != 1 else ''} "
                "were due while you were away",
                body=titles or "Open Reminders to review them.",
                kind="summary",
            )
        )
        plan.notified_ids += [r["id"] for r in missed]

    if fresh:
        if len(fresh) <= max_individual:
            for r in fresh:
                plan.toasts.append(
                    Toast(
                        title=r.get("title") or "Reminder",
                        body=_fmt_due(r.get("due_date")) or "Due now",
                        reminder_id=r["id"],
                    )
                )
        else:
            titles = ", ".join(r["title"] for r in fresh[:3] if r.get("title"))
            plan.toasts.append(
                Toast(
                    title=f"{len(fresh)} reminders are due",
                    body=titles,
                    kind="summary",
                )
            )
        plan.notified_ids += [r["id"] for r in fresh]

    if plan.notified_ids:
        cache.mark_notified(plan.notified_ids)
    return plan
