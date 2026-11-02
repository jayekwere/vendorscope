from __future__ import annotations

from typing import Any

import pytest

from app.common import DEMO_IDENTITIES, DomainError, Identity
from app.domain import Assessment, Change, Decision, Offboard, Vendor, dispatch, view

NOW = "2026-11-01T12:00:00Z"
OP = DEMO_IDENTITIES["demo-operator"]
REVIEW = DEMO_IDENTITIES["demo-reviewer"]


def seed(tier: str = "critical") -> dict[str, Any]:
    state: dict[str, Any] = {}
    dispatch(
        state,
        OP,
        "register-vendor",
        Vendor.model_validate(
            dict(
                id="vendor",
                name="Synthetic Vendor",
                owner="platform",
                service="Synthetic record service",
                tier=tier,
                data="personal",
                regions=["CA"],
                subprocessors=[],
            )
        ),
        NOW,
    )
    return state


def test_assessment_missing_stale_self_review_and_risk_decision() -> None:
    state = seed()
    assess = Assessment(
        id="vendor",
        version=1,
        encryption=None,
        access_reviews=True,
        recovery_test=False,
        evidence_sha256="a" * 64,
        observed_at=NOW,
    )
    result = dispatch(state, OP, "assess-vendor", assess, NOW)
    assert [r["state"] for r in result["assessment"]["findings"]] == ["missing", "failed"]
    data = dict(
        id="vendor",
        version=2,
        assessment_sha256=result["assessment"]["sha256"],
        outcome="accept-risk",
        rationale="A documented bounded assessment decision.",
        compensation="Independently verify backups every day.",
        valid_hours=24,
    )
    same = Identity(tenant="alder", subject=OP.subject, roles=["reviewer"])
    with pytest.raises(DomainError):
        dispatch(state, same, "decide-vendor", Decision.model_validate(data), NOW)
    with pytest.raises(DomainError):
        dispatch(
            state,
            REVIEW,
            "decide-vendor",
            Decision.model_validate({**data, "compensation": ""}),
            NOW,
        )
    dispatch(state, REVIEW, "decide-vendor", Decision.model_validate(data), NOW)
    assert view(state, REVIEW, "2026-11-03T12:00:00Z")["vendors"][0]["status"] == "decision-expired"
    with pytest.raises(DomainError):
        dispatch(
            seed(),
            OP,
            "assess-vendor",
            assess.model_copy(update={"observed_at": "2026-01-01T00:00:00Z"}),
            NOW,
        )


def test_offboarding_requires_evidence_and_is_terminal() -> None:
    state = seed("standard")
    with pytest.raises(DomainError):
        dispatch(
            state,
            REVIEW,
            "offboard-vendor",
            Offboard(
                id="vendor",
                version=1,
                access_revoked=False,
                data_returned_or_deleted=True,
                evidence_sha256="a" * 64,
            ),
            NOW,
        )
    result = dispatch(
        state,
        REVIEW,
        "offboard-vendor",
        Offboard(
            id="vendor",
            version=1,
            access_revoked=True,
            data_returned_or_deleted=True,
            evidence_sha256="a" * 64,
        ),
        NOW,
    )
    assert result["status"] == "offboarded"
    with pytest.raises(DomainError):
        dispatch(
            state,
            OP,
            "change-subprocessors",
            Change(
                id="vendor",
                version=2,
                subprocessors=[],
                regions=["CA"],
                reason="Attempt to reopen retired service.",
            ),
            NOW,
        )
    with pytest.raises(DomainError):
        view(state, DEMO_IDENTITIES["demo-clinician"], NOW)


def test_customer_responses_pin_exact_current_evidence() -> None:
    from app.domain import Questionnaire, Ref, ResponseReview

    state = seed()
    dispatch(
        state,
        OP,
        "assess-vendor",
        Assessment(
            id="vendor",
            version=1,
            encryption=True,
            access_reviews=None,
            recovery_test=True,
            evidence_sha256="a" * 64,
            observed_at=NOW,
        ),
        NOW,
    )
    response = dispatch(
        state,
        OP,
        "draft-responses",
        Questionnaire(
            id="response", vendor_id="vendor", version=2, questions=["encryption", "access_reviews"]
        ),
        NOW,
    )
    assert response["answers"][1]["answer"] == "unknown"
    with pytest.raises(DomainError):
        dispatch(state, REVIEW, "export-responses", Ref(id="response"), NOW)
    same = Identity(tenant="alder", subject=OP.subject, roles=["reviewer"])
    approval = ResponseReview(id="response", response_sha256=response["response_sha256"])
    with pytest.raises(DomainError):
        dispatch(state, same, "approve-responses", approval, NOW)
    dispatch(state, REVIEW, "approve-responses", approval, NOW)
    assert (
        dispatch(
            state, DEMO_IDENTITIES["demo-auditor"], "export-responses", Ref(id="response"), NOW
        )["status"]
        == "approved"
    )
    dispatch(
        state,
        OP,
        "change-subprocessors",
        Change(
            id="vendor",
            version=2,
            subprocessors=["new"],
            regions=["CA"],
            reason="A new downstream processing relationship.",
        ),
        NOW,
    )
    with pytest.raises(DomainError):
        dispatch(state, REVIEW, "export-responses", Ref(id="response"), NOW)
