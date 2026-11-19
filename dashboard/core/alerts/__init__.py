"""Price alerts: stop-loss, target-price, daily-move and position-weight reminders.

``rules``     the rules and their firing state (``desk.sqlite3``)
``engine``    the pure, edge-triggered decision for one rule and one quote
``monitor``   the background task that checks rules during trading sessions
``events``    the log of fired alerts
``view``      rules with their current price and distance to fire
``delivery``  where a push channel such as Feishu registers
``proposal``  the ``alert_rule`` proposal kind used by the agent

An alert only notifies. Nothing in this package places, prepares or sends an
order.

Importing the package registers the ``alert_rule`` proposal kind, so a
confirmation through ``/api/proposals`` can apply it in any process that has
loaded the alerts API or the agent tools.
"""
from . import proposal

proposal.register()
