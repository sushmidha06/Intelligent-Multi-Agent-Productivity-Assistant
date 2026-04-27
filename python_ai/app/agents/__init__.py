"""Proactive agents that run on a schedule, not in response to user chat.

Each agent inspects the user's data, decides whether something needs
attention, and (optionally) pushes a notification. They are independent
processes you can enable/disable individually.
"""

from .base import AgentReport, ProactiveAgent
from .inbox_triage import InboxTriageAgent
from .project_monitor import ProjectMonitorAgent
from .anomaly_detector import AnomalyDetectorAgent
from .recurring_workflows import RecurringWorkflowsAgent

ALL_AGENT_CLASSES = [
    InboxTriageAgent,
    ProjectMonitorAgent,
    AnomalyDetectorAgent,
    RecurringWorkflowsAgent,
]

__all__ = [
    "AgentReport",
    "ProactiveAgent",
    "InboxTriageAgent",
    "ProjectMonitorAgent",
    "AnomalyDetectorAgent",
    "RecurringWorkflowsAgent",
    "ALL_AGENT_CLASSES",
]
