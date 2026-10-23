"""The app's activity beacon: what a person opens inside Jarvis.

One tiny handler: the web bundle posts a light event (a feature page it just
opened, usually) and it lands on that person's activity feed in telemetry.
Recording is best effort -- the beacon must never slow a page load down or
fail one.
"""
from services.execution import activity
from services.execution.schemas import ActivityRequest, ExecutionResult

FEATURE_MAX = 40
ITEM_MAX = 200
EVENT_MAX = 20


async def handle_activity(req: ActivityRequest) -> ExecutionResult:
    feature = (req.feature or "").strip()[:FEATURE_MAX] or "app"
    item = (req.item or "").strip()[:ITEM_MAX]
    event = (req.event or "view").strip()[:EVENT_MAX] or "view"
    detail = req.detail if isinstance(req.detail, dict) else {}
    user = getattr(req.user_context, "user", "") or ""
    if user:
        await activity.record(user, feature, item=item, action=event, duration_s=req.duration_s, detail=detail)
    return ExecutionResult.ok("Noted.", service="activity_feed")
