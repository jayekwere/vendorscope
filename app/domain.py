from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import Field

from app.common import (
    Contract,
    DomainError,
    Identity,
    Ref,
    VersionRef,
    digest,
    fresh,
    get,
    independent,
    put,
    require,
    stamp,
    versioned,
)


class Vendor(Ref):
    name: str = Field(min_length=3, max_length=120)
    owner: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    service: str = Field(min_length=5, max_length=160)
    tier: Literal["critical", "standard"]
    data: Literal["public", "personal", "synthetic-health"]
    regions: list[str] = Field(min_length=1, max_length=8)
    subprocessors: list[str] = Field(max_length=20)


class Assessment(VersionRef):
    encryption: bool | None
    access_reviews: bool | None
    recovery_test: bool | None
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: str = Field(min_length=20, max_length=40)


class Decision(VersionRef):
    assessment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["approve", "accept-risk", "reject"]
    rationale: str = Field(min_length=20, max_length=500)
    compensation: str = Field(max_length=500)
    valid_hours: int = Field(ge=1, le=2160)


class Change(VersionRef):
    subprocessors: list[str] = Field(max_length=20)
    regions: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=10, max_length=500)


class Offboard(VersionRef):
    access_revoked: bool
    data_returned_or_deleted: bool
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Questionnaire(Ref):
    vendor_id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: int = Field(ge=1, le=1_000_000)
    questions: list[Literal["encryption", "access_reviews", "recovery_test"]] = Field(
        min_length=1, max_length=3
    )


class ResponseReview(Ref):
    response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


MODELS: dict[str, type[Contract]] = {
    "register-vendor": Vendor,
    "assess-vendor": Assessment,
    "decide-vendor": Decision,
    "change-subprocessors": Change,
    "offboard-vendor": Offboard,
}

MODELS.update(
    {"draft-responses": Questionnaire, "approve-responses": ResponseReview, "export-responses": Ref}
)


def risk(data: dict[str, Any], tier: str) -> list[dict[str, str]]:
    checks = ["encryption", "access_reviews"] + (["recovery_test"] if tier == "critical" else [])
    return [
        {
            "control": key,
            "state": "missing" if data[key] is None else "failed",
            "reason": "Evidence missing or safeguard not demonstrated",
        }
        for key in checks
        if data[key] is not True
    ]


def dispatch(
    state: dict[str, Any], actor: Identity, action: str, value: Contract, now: str
) -> dict[str, Any]:
    data = value.model_dump()
    if action in {"draft-responses", "approve-responses", "export-responses"}:
        if action == "draft-responses":
            require(actor, "operator")
            vendor = get(state, "vendor", data["vendor_id"])
            versioned(vendor, data["version"])
            assessment = vendor["assessment"]
            if (
                not assessment
                or not fresh(assessment["observed_at"], now, 24 * 90)
                or len(set(data["questions"])) != len(data["questions"])
            ):
                raise DomainError("Current evidence and distinct questions are required")
            response = {
                **data,
                "drafted_by": actor.subject,
                "assessment_sha256": assessment["sha256"],
                "answers": [
                    {
                        "question": question,
                        "answer": "unknown"
                        if assessment[question] is None
                        else "yes"
                        if assessment[question]
                        else "no",
                        "evidence_sha256": assessment["evidence_sha256"],
                    }
                    for question in data["questions"]
                ],
                "status": "draft",
            }
            response["response_sha256"] = digest(response)
            return put(state, "questionnaire", data["id"], response, create=True)
        response = get(state, "questionnaire", data["id"])
        vendor = get(state, "vendor", response["vendor_id"])
        if (
            vendor["version"] != response["version"]
            or not vendor["assessment"]
            or vendor["assessment"]["sha256"] != response["assessment_sha256"]
            or not fresh(vendor["assessment"]["observed_at"], now, 24 * 90)
        ):
            raise DomainError("Questionnaire evidence changed or expired")
        if action == "approve-responses":
            independent(actor, response["drafted_by"])
            if (
                response["status"] != "draft"
                or response["response_sha256"] != data["response_sha256"]
            ):
                raise DomainError("Response changed or already reviewed")
            response.update(status="approved", approved_by=actor.subject)
            return put(state, "questionnaire", data["id"], response)
        if not set(actor.roles) & {"reviewer", "auditor"}:
            raise DomainError("Role not authorized", 403)
        if response["status"] != "approved":
            raise DomainError("Only approved responses may be exported")
        return response
    if action == "register-vendor":
        require(actor, "operator")
        return put(
            state,
            "vendor",
            data["id"],
            {
                **data,
                "version": 1,
                "status": "due-diligence",
                "registered_by": actor.subject,
                "assessment": None,
                "decision": None,
            },
            create=True,
        )
    vendor = get(state, "vendor", data["id"])
    versioned(vendor, data["version"])
    if vendor["status"] == "offboarded":
        raise DomainError("Offboarded vendors cannot be changed")
    if action == "assess-vendor":
        require(actor, "operator")
        if not fresh(data["observed_at"], now, 24 * 90):
            raise DomainError("Assessment evidence must be current", 422)
        assessment = {
            **data,
            "assessed_by": actor.subject,
            "findings": risk(data, vendor["tier"]),
            "vendor_version": vendor["version"],
        }
        assessment["sha256"] = digest(assessment)
        vendor.update(assessment=assessment, decision=None, status="awaiting-decision")
    elif action == "decide-vendor":
        assessment = vendor["assessment"]
        if assessment is None or vendor["status"] != "awaiting-decision":
            raise DomainError("A current assessment is required")
        independent(actor, assessment["assessed_by"])
        if data["assessment_sha256"] != assessment["sha256"] or not fresh(
            assessment["observed_at"], now, 24 * 90
        ):
            raise DomainError("Assessment changed or expired")
        findings = assessment["findings"]
        if data["outcome"] == "approve" and findings:
            raise DomainError("Unresolved or missing controls require explicit risk acceptance")
        if data["outcome"] == "accept-risk" and (
            not findings or len(data["compensation"].strip()) < 20 or data["valid_hours"] > 720
        ):
            raise DomainError(
                "Risk acceptance requires unresolved risks, compensation and at most 720 hours"
            )
        vendor.update(
            decision={
                **data,
                "reviewer": actor.subject,
                "decided_at": now,
                "expires_at": (stamp(now) + timedelta(hours=data["valid_hours"])).isoformat(),
            },
            status={"approve": "approved", "accept-risk": "risk-accepted", "reject": "rejected"}[
                data["outcome"]
            ],
        )
    elif action == "change-subprocessors":
        require(actor, "operator")
        vendor.update(
            subprocessors=data["subprocessors"],
            regions=data["regions"],
            status="reassessment-required",
            assessment=None,
            decision=None,
        )
    else:
        require(actor, "reviewer")
        if not data["access_revoked"] or not data["data_returned_or_deleted"]:
            raise DomainError("Offboarding requires recorded access and data disposition evidence")
        vendor.update(
            status="offboarded", offboarding={**data, "reviewer": actor.subject, "at": now}
        )
    vendor["version"] += 1
    return put(state, "vendor", data["id"], vendor)


def view(state: dict[str, Any], actor: Identity, now: str) -> dict[str, Any]:
    if not set(actor.roles) & {"operator", "reviewer", "auditor"}:
        raise DomainError("Role not authorized", 403)
    vendors = []
    for original in state.get("vendor", {}).values():
        vendor = dict(original)
        if vendor["status"] in {"approved", "risk-accepted"} and stamp(
            vendor["decision"]["expires_at"]
        ) <= stamp(now):
            vendor["status"] = "decision-expired"
        vendors.append(vendor)
    return {
        "vendors": vendors,
        "attention": [v["id"] for v in vendors if v["status"] not in {"approved", "offboarded"}],
        "evidence_trust": "operator_reviewed_documents_not_independent_vendor_verification",
    }
