
from __future__ import annotations

from cabbagecode.commands.handlers.clear import CLEAR_COMMAND
from cabbagecode.commands.handlers.compact import COMPACT_COMMAND
from cabbagecode.commands.handlers.help import HELP_COMMAND
from cabbagecode.commands.handlers.mcp import MCP_COMMAND
from cabbagecode.commands.handlers.memory import MEMORY_COMMAND
from cabbagecode.commands.handlers.plan import PLAN_COMMAND
from cabbagecode.commands.handlers.sandbox import SANDBOX_COMMAND
from cabbagecode.commands.handlers.session import SESSION_COMMAND
from cabbagecode.commands.handlers.skill import SKILL_COMMAND
from cabbagecode.commands.handlers.rewind import REWIND_COMMAND
from cabbagecode.commands.handlers.status import STATUS_COMMAND
from cabbagecode.commands.registry import CommandRegistry


ALL_COMMANDS = [
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    SESSION_COMMAND,
    MCP_COMMAND,
    MEMORY_COMMAND,
    SANDBOX_COMMAND,
    REWIND_COMMAND,
    STATUS_COMMAND,
    SKILL_COMMAND,
]


def register_all_commands(registry: CommandRegistry) -> None:
    for cmd in ALL_COMMANDS:
        registry.register_sync(cmd)

