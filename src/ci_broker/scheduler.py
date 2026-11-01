"""Cost-aware advisory planning without provider names or project-specific assumptions."""
from __future__ import annotations

from .ledger import Ledger, utc_month
from .models import Profile, Project, adapter_for


def plan(project: Project, profile: Profile, providers: dict[str, dict], entries: list[dict], independent: bool = False) -> dict:
    month = utc_month()
    options = []
    for name, spec in sorted(providers.items()):
        adapter = adapter_for(spec)
        eligible, reason = adapter.eligible(spec, profile)
        if (independent or profile.independent) and not spec.get("independent", False):
            eligible, reason = False, "independent-verifier-required"
        quote = adapter.quote(spec, profile)
        used = Ledger.usage(entries, name, month)
        reserved = sum(e["units"] for (p, _), e in Ledger.active(entries, month).items() if p == name)
        quota = spec.get("quota")
        remaining = None if quota is None else max(0, float(quota) - used - reserved)
        protected = float(quota) * project.reserve_fraction if quota is not None else 0
        if eligible and remaining is not None and remaining - quote.worst < protected:
            eligible, reason = False, "insufficient-protected-budget"
        external = bool(spec.get("independent", False))
        if eligible and external and not spec.get("verified", False):
            eligible, reason = False, "unverified-account-budget"
        headroom = 1.0 if remaining is None else (remaining - quote.worst - protected) / float(quota)
        options.append({"provider": name, "eligible": eligible, "reason": reason, "unit": quote.unit,
                        "expected": quote.expected, "worst": quote.worst, "used": used,
                        "reserved": reserved, "remaining": remaining, "headroom_fraction": round(headroom, 6),
                        "independent": external, "verified": bool(spec.get("verified", False))})
    eligible = [item for item in options if item["eligible"]]
    if project.prefer_local and not independent and not profile.independent:
        selected = next((x for x in eligible if x["provider"] == "local"), None)
    else:
        selected = None
    selected = selected or (max(eligible, key=lambda x: (x["headroom_fraction"], x["provider"])) if eligible else None)
    return {"project": project.identifier, "profile": profile.name, "selected": selected["provider"] if selected else None,
            "advisory_only": True, "options": options}
