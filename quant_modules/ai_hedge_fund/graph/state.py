import json
import operator
from collections.abc import Sequence
from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


def merge_dicts(a: dict[str, any], b: dict[str, any]) -> dict[str, any]:
    return {**a, **b}


# Define agent state
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    data: Annotated[dict[str, any], merge_dicts]
    metadata: Annotated[dict[str, any], merge_dicts]


def show_agent_reasoning(output: Any, agent_name: str) -> None:

    def convert_to_serializable(obj: Any) -> Any:
        if hasattr(obj, "to_dict"):  # Handle Pandas Series/DataFrame
            return obj.to_dict()
        if hasattr(obj, "__dict__"):  # Handle custom objects
            return obj.__dict__
        if isinstance(obj, (int, float, bool, str)):
            return obj
        if isinstance(obj, (list, tuple)):
            return [convert_to_serializable(item) for item in obj]
        if isinstance(obj, dict):
            return {key: convert_to_serializable(value) for key, value in obj.items()}
        return str(obj)  # Fallback to string representation

    if isinstance(output, (dict, list)):
        # Convert the output to JSON-serializable format
        convert_to_serializable(output)
    else:
        try:
            # Parse the string as JSON and pretty print it
            json.loads(output)
        except json.JSONDecodeError:
            # Fallback to original string if not valid JSON
            pass
