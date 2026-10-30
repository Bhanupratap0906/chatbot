"""Item D: agent prompts include length rule."""
from modules.workflow.conversation_agents import AGENT_ROLES

def test_all_agents_have_length_rule() -> None:
    for role in AGENT_ROLES.values():
        # Instruction embedded either in comment or system_prompt text
        assert "Answer in at most 2 short sentences" in (role.system_prompt or ""), f"{role.name} missing"
