"""Review of proposed changes. Mounted at ``/api/proposals``.

There is deliberately no endpoint that creates a proposal: proposals come from
agent tools and routines in this process, and only a person's request to
``/confirm`` applies one. A busy or unusable store is a 503 (see
``desk_errors``).

``DELETE /{id}`` rejects a pending proposal. ``DELETE /{id}/decision`` deletes a
*rejected* lesson or profile proposal, which is otherwise kept for good so the
assistant does not propose the same text again (``proposals.forget``): the
person takes their "no" back. It is a separate route so that a repeated
reject (a double click on 放弃) can never forget the decision it just made.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.api.desk_errors import desk_call
from core import proposals

router = APIRouter()


def attempt(operation):
    try:
        return desk_call(operation)
    except proposals.NotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except proposals.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except proposals.ApplyFailed as exc:
        raise HTTPException(422 if exc.expected else 500, str(exc)) from None
    except proposals.ProposalError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('')
def listing(status: str = 'pending', kind: Optional[List[str]] = Query(None)):
    """``status``: pending (default), applied, rejected, expired or all. ``kind`` (repeatable) keeps only
    those kinds. Newest first, at most ``proposals.MAX_LIST``; ``pending`` counts every kind."""
    return attempt(lambda: {'items': proposals.list(status, proposals.MAX_LIST if kind else 100, kinds=kind),
                            'pending': proposals.pending_count()})


@router.get('/{proposal_id}')
def proposal(proposal_id: str):
    return attempt(lambda: proposals.get(proposal_id))


@router.post('/{proposal_id}/confirm')
def confirm(proposal_id: str):
    """Apply the proposal. Safe to repeat: a second call returns the stored result."""
    return attempt(lambda: proposals.confirm(proposal_id))


@router.delete('/{proposal_id}')
def reject(proposal_id: str):
    return attempt(lambda: proposals.reject(proposal_id))


@router.delete('/{proposal_id}/decision')
def forget(proposal_id: str):
    """Delete a rejected lesson or profile proposal (409 for anything else); returns it as it was."""
    return attempt(lambda: {'forgotten': True, 'proposal': proposals.forget(proposal_id)})
