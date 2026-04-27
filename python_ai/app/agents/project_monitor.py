"""Project Status Monitor Agent.

Computes a derived health score for each active project from objective
signals (commit cadence, time remaining, budget burn) and flags ones that
have *changed* into a worse state than the user's stored `health` field.

This is deliberately rule-based, not LLM-based. The signals are simple,
they're cheap to compute, and reproducibility matters more than nuance —
the user wants to trust this one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import ProactiveAgent


def _to_float(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _to_int(x, default=0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def compute_health(project: dict, *, now: datetime | None = None) -> tuple[int, list[str]]:
    """Returns (health_score 0-100, reasons[]).

    Heuristic — not a model. Reasons explain the score so the agent can
    quote them in the notification."""
    now = now or datetime.now(timezone.utc)
    score = 100
    reasons: list[str] = []

    days_left = _to_int(project.get("daysLeft"), default=999)
    commits = _to_int(project.get("commits"), default=0)
    budget = _to_float(project.get("budget"), default=0.0)
    spent = _to_float(project.get("spent"), default=0.0)
    status = (project.get("status") or "").lower()

    if status in {"completed", "archived", "cancelled"}:
        return 100, ["non-active project — health not evaluated"]

    # Time pressure
    if days_left <= 0:
        score -= 40
        reasons.append("deadline passed")
    elif days_left <= 3:
        score -= 25
        reasons.append(f"only {days_left} days left")
    elif days_left <= 7:
        score -= 10
        reasons.append(f"{days_left} days left")

    # Activity
    if commits == 0 and days_left < 30:
        score -= 25
        reasons.append("no commits recorded")
    elif commits < 3 and days_left < 14:
        score -= 10
        reasons.append(f"low commit count ({commits})")

    # Budget burn
    if budget > 0:
        burn = spent / budget
        if burn >= 1.0:
            score -= 25
            reasons.append(f"over budget ({int(burn * 100)}%)")
        elif burn >= 0.9:
            score -= 15
            reasons.append(f"budget at {int(burn * 100)}%")
        elif burn >= 0.75 and days_left < 14:
            score -= 5
            reasons.append(f"budget at {int(burn * 100)}% with {days_left} days left")

    return max(0, min(100, score)), reasons


class ProjectMonitorAgent(ProactiveAgent):
    name = "project-monitor"

    # Only nudge for projects below this — keeps the noise floor sane.
    NUDGE_THRESHOLD = 60

    def _run(self) -> None:
        projects = self.node.get_collection("projects") or []
        if not projects:
            self.add_finding("no_projects", "No projects to monitor", "User has no projects yet.", severity="info")
            return

        at_risk: list[tuple[dict, int, list[str]]] = []
        for p in projects:
            score, reasons = compute_health(p)
            self.add_finding(
                kind="project_health",
                title=p.get("name", "Project"),
                body=f"Computed health {score}/100" + (f" — {'; '.join(reasons)}" if reasons else ""),
                severity="critical" if score < 40 else ("warn" if score < self.NUDGE_THRESHOLD else "info"),
                project_id=p.get("id"),
                computed_health=score,
                stored_health=p.get("health"),
            )
            if score < self.NUDGE_THRESHOLD:
                at_risk.append((p, score, reasons))

        if not at_risk:
            return

        # Bundle into one notification (per-project notifications would spam).
        at_risk.sort(key=lambda t: t[1])  # worst first
        worst = at_risk[0]
        if len(at_risk) == 1:
            title = f"{worst[0].get('name', 'Project')} is at risk"
        else:
            title = f"{len(at_risk)} projects at risk"
        body = f"{worst[0].get('name', 'Project')}: {'; '.join(worst[2]) or 'health below threshold'}"
        kind = "warn" if worst[1] >= 40 else "error"
        self.notify(title=title, body=body, kind=kind)
