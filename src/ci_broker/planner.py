"""Provider-independent task planning, no dispatch or network."""
from __future__ import annotations
from datetime import datetime, timezone
from .graph import order, estimate
from .ledger import active, usage
from .providers import resolve
from .model import BrokerError

def plan(project: dict, accounts: dict, profile: str, events: list[dict] | None = None,
         *, independent: bool = False, now: datetime | None = None) -> dict:
    if profile not in project["profiles"]:
        raise BrokerError(f"Unknown profile {profile}")
    tasks = project["tasks"]
    names = order(tasks, project["profiles"][profile])
    now = now or datetime.now(timezone.utc)
    period = now.strftime("%Y-%m")
    events = events or []
    entries = []
    for name, spec in accounts.items():
        adapter = resolve(spec)
        all_quotes = []
        reasons = []
        if independent and not spec.get("independent", False):
            reasons.append("not-independent")
        for task_id in names:
            task = tasks[task_id]
            eligible, why = adapter.eligible(spec, task)
            if not eligible:
                reasons.append(f"{task_id}:{why}")
            all_quotes.append(adapter.quote(spec, task))
        total_expected = sum(q.expected_units for q in all_quotes)
        total_worst = sum(q.worst_units for q in all_quotes)
        quota = spec.get("quota")
        already_used = usage(events, period, name)
        held = sum(e["units"] for (p, _), e in active(events, period).items() if p == name)
        if quota is not None:
            fraction = float(spec.get("reserve_fraction", 0.1))
            if not 0 <= fraction < 1:
                raise BrokerError("Invalid reserve_fraction")
            remaining = float(quota) * (1 - fraction) - already_used - held
            if remaining < total_worst:
                reasons.append("insufficient-worst-case-headroom")
            capacity = max(remaining, 0) / float(quota)
        else:
            remaining = None
            capacity = 1.0
        entries.append({
            "provider": name, "eligible": not reasons, "reason": reasons,
            "expected_units": round(total_expected, 5),
            "worst_units": round(total_worst, 5),
            "unit": all_quotes[0].unit if all_quotes else spec.get("unit", "units"),
            "quota_remaining_protected": remaining,
            "verified": spec.get("verified", False),
            "requires_approval": spec.get("adapter") != "local",
            "score": round(capacity, 5),
        })
    available = [entry for entry in entries if entry["eligible"]]
    local = [entry for entry in available if accounts[entry["provider"]].get("preferred", False)
             and accounts[entry["provider"]].get("adapter") == "local"]
    selected = (
        sorted(local, key=lambda x: x["provider"])[0] if local and not independent
        else max(available, key=lambda x: (x["score"], x["provider"])) if available else None
    )
    return {
        "schema": 1, "project": project["project_id"], "profile": profile,
        "selected": selected["provider"] if selected else None,
        "tasks": names, "estimate": estimate(tasks, names),
        "advisory_only": True, "candidates": entries,
    }
