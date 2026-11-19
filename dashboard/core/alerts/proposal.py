"""The ``alert_rule`` proposal kind: how the agent asks for a rule.

The agent's tool calls ``proposals.propose("alert_rule", payload, source)``.
Nothing is created until a person confirms the proposal through
``POST /api/proposals/{id}/confirm``; ``apply`` then creates the rule with the
proposal's source (``agent:chat``) inside the confirmation's transaction, so
it happens exactly once.
"""
from __future__ import annotations

from core import proposals

from . import messages, rules

KIND = 'alert_rule'


def validate(payload: dict) -> dict:
    rule = rules.validate(payload)
    # Refuse now what ``create`` would refuse on confirmation.
    same = [item['id'] for item in rules.list_rules(ticker=rule['ticker'])
            if (item['kind'], item['threshold'], item['account']) == (rule['kind'], rule['threshold'], rule['account'])
            and not rules.reconciled_duplicate(item)]
    if same:
        raise rules.RuleConflict(f'已有相同的提醒规则（#{same[0]}），无需再添加。')
    return rule


def apply(payload: dict, proposal: dict) -> dict:
    rule = rules.create(payload, source=proposal['source'])
    return {'rule_id': rule['id']}


def register() -> None:
    proposals.register_kind(KIND, validate, apply, messages.describe_proposal)
