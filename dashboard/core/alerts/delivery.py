"""Where a push channel (Feishu, later others) plugs into alerts.

Every alert is first a stored notification (``core.notify``): that row feeds
the bell in the web app and is the record that the user was told. A push
channel is an optional extra on top of it. To add one::

    from core.alerts import delivery

    def push_to_feishu(item: dict) -> None:
        # item: {seq, kind, level, title, body, data, created_at, read_at}
        payload = delivery.push_payload(item)
        # POST payload to the Feishu bridge's /bridge/notify on loopback,
        # with its bearer token and a network timeout of a few seconds.
        ...

    delivery.register(push_to_feishu)      # e.g. from the bridge's startup code

The deliverer then receives the alert family of notifications only
(``ALERT_KINDS``), on ``core.notify``'s delivery thread, after the row has
committed, in publish order. It must not block for long and may raise: a
failure is logged by ``core.notify`` (name, seq and kind only) and never
affects the alert, its state or the stored notification. Delivery is not
retried; ``push_payload`` carries ``request_id = "alert-notify:<seq>"`` so the
receiving side can drop a repeat if a retry is added later.

Nothing is registered by default: the Feishu bridge
(``dashboard/feishu_workbench/``) has no ``/bridge/notify`` endpoint yet.
"""
from __future__ import annotations

from typing import Callable, Dict

from core import notify

ALERT_KINDS = ('alert', 'alert.digest', 'alert.monitor')

Deliverer = Callable[[dict], None]
_wrappers: Dict[Deliverer, Deliverer] = {}


def is_alert(item: dict) -> bool:
    return isinstance(item, dict) and item.get('kind') in ALERT_KINDS


def push_payload(item: dict) -> dict:
    """``{request_id, title, body, level, kind, link}`` for a chat push of ``item``."""
    data = item.get('data') or {}
    return {'request_id': f'alert-notify:{item["seq"]}', 'title': item['title'], 'body': item['body'],
            'level': item['level'], 'kind': item['kind'], 'link': data.get('link') or '/alerts'}


def register(deliverer: Deliverer) -> Deliverer:
    """Send alert notifications to ``deliverer`` from now on. Returns it (usable as a decorator)."""
    if not callable(deliverer):
        raise TypeError('a deliverer must be callable')
    if deliverer in _wrappers:
        return deliverer

    def deliver_alert(item: dict) -> None:
        if is_alert(item):
            deliverer(item)

    deliver_alert.__name__ = f'alerts:{getattr(deliverer, "__name__", type(deliverer).__name__)}'
    _wrappers[deliverer] = deliver_alert
    notify.register_deliverer(deliver_alert)
    return deliverer


def unregister(deliverer: Deliverer) -> None:
    wrapper = _wrappers.pop(deliverer, None)
    if wrapper is not None:
        notify.unregister_deliverer(wrapper)
