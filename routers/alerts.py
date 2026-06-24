"""Alert endpoints — outlier alerts, read status, history."""
from fastapi import APIRouter, Query

from creators import get_alerts, mark_alert_read

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def get_creator_alerts(
    unread_only: bool = Query(True),
    limit: int = Query(20),
):
    """Get creator alerts (outlier posts, viral content)."""
    return {"alerts": get_alerts(unread_only=unread_only, limit=limit)}


@router.post("/alerts/{alert_id}/read")
async def read_alert(alert_id: int):
    """Mark an alert as read."""
    mark_alert_read(alert_id)
    return {"status": "read", "alert_id": alert_id}


@router.post("/alerts/outliers/check")
async def check_outlier_alerts(
    threshold: float = Query(3.0, description="Minimum z-score to trigger alert"),
    limit: int = Query(5, description="Max outliers to check"),
    hours: int = Query(24, description="Look back N hours"),
):
    """
    Check for viral outliers and send Telegram alerts.
    Posts with z_score >= threshold trigger a notification.
    Has cooldown to prevent duplicate alerts.
    """
    from alert_system import check_and_alert_outliers, get_alert_config
    config = get_alert_config()
    result = check_and_alert_outliers(threshold=threshold, limit=limit, hours=hours)
    result["telegram_configured"] = config["enabled"]
    return result


@router.get("/alerts/history")
async def alert_history(limit: int = Query(20)):
    """Get history of sent/saved outlier alerts."""
    from alert_system import get_alert_history
    return {"alerts": get_alert_history(limit=limit)}
