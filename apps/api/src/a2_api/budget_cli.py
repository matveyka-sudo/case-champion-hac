"""Compatibility imports; implementation lives in a2_api.assistant.budget_cli."""

from a2_api.assistant.budget_cli import (
    _execute as _execute,
)
from a2_api.assistant.budget_cli import (
    main as main,
)
from a2_api.assistant.budget_cli import (
    run_command as run_command,
)

if __name__ == "__main__":
    main()
