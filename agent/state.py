from operator import add
from typing import Annotated, TypedDict

# The shared state that flows through every node in the LangGraph graph.
# It holds the incident details, whatever each tool has returned so far
# (metrics/logs/traces/deployments/similar_incidents), and the running
# conclusion (findings/root_cause/confidence). The one thing worth calling
# out: messages uses Annotated[list, add] as a reducer, which tells
# LangGraph to append new messages to the list instead of overwriting it -
# that's what lets the llm/tool nodes build up a running conversation
# instead of each one clobbering what came before.


class AgentState(TypedDict, total=False):
    incident_id: str
    tenant: str
    service: str
    alert_name: str
    start_time: str
    end_time: str

    metrics: list
    logs: list
    traces: list
    deployments: list
    similar_incidents: list

    findings: list
    root_cause: str
    confidence: str

    messages: Annotated[list, add]