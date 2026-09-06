from operator import add
from typing import Annotated, TypedDict


class AgentState(TypedDict):
    incident_id: str
    tenant: str
    service: str
    alert_name: str
    start_time: str
    end_time: str
    # Nodes return only their new messages; the reducer appends them.
    messages: Annotated[list, add]
