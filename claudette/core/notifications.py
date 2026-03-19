"""Outgoing webhook notifications."""

from __future__ import annotations

import json
import logging
from urllib.request import Request, urlopen

from claudette.core.config import NotificationsConfig

logger = logging.getLogger("claudette.notify")


def notify(config: NotificationsConfig, event: str, message: str, **extra: object) -> None:
    """Send a notification if the event is enabled and webhook is configured."""
    if not config.webhook_url or event not in config.events:
        return

    payload: dict[str, object] = {"text": message, "event": event, **extra}

    # Detect Slack vs Discord vs generic
    url = config.webhook_url
    if "discord" in url:
        payload = {"content": message}

    try:
        data = json.dumps(payload).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)  # noqa: S310
    except Exception as e:
        logger.warning("Notification failed for %s: %s", event, e)
