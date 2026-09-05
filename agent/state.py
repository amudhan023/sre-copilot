from operator import add
from typing import Annotated, TypedDict


class AgentState(TypedDict):
    # Nodes return only their new messages; the reducer appends them.
    messages: Annotated[list, add]
