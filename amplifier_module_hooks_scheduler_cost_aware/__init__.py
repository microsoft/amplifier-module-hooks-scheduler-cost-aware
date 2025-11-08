"""
Cost-aware scheduler module for Amplifier.
Observes and can veto/modify tool selections based on cost metrics.
"""

import logging
from typing import Any

from amplifier_core import HookResult
from amplifier_core import ModuleCoordinator

logger = logging.getLogger(__name__)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """
    Mount the cost-aware scheduler module.

    Args:
        coordinator: Module coordinator
        config: Scheduler configuration

    Returns:
        Optional cleanup function
    """
    config = config or {}
    scheduler = CostAwareScheduler(config)
    await scheduler.register(coordinator)
    logger.info("Mounted CostAwareScheduler")
    return


class CostAwareScheduler:
    """
    Cost-aware scheduler that observes tool selections.
    Can veto expensive operations or suggest cheaper alternatives.
    """

    def __init__(self, config: dict[str, Any]):
        """
        Initialize cost-aware scheduler.

        Args:
            config: Scheduler configuration
        """
        self.config = config
        self.max_cost = config.get("max_cost", 100.0)
        self.prefer_cheaper = config.get("prefer_cheaper", True)

        # Cost estimates per tool (example values)
        self.tool_costs = config.get(
            "tool_costs",
            {
                "web_search": 5.0,
                "code_analysis": 10.0,
                "file_write": 1.0,
                "file_read": 0.5,
                "bash": 2.0,
            },
        )

        # Track cumulative cost
        self.session_cost = 0.0

        logger.info(f"CostAwareScheduler initialized with max_cost={self.max_cost}")

    async def register(self, coordinator: ModuleCoordinator):
        """Register hook handlers with the coordinator."""
        hooks = coordinator.get("hooks")
        if not hooks:
            logger.warning("No hook registry found in coordinator")
            return

        # Register for tool:selecting event
        hooks.register(
            "tool:selecting",
            self.on_tool_selecting,
            priority=60,  # Higher priority than heuristic
            name="cost:tool_selecting",
        )

        # Register for tool:selected to track costs
        hooks.register(
            "tool:selected",
            self.on_tool_selected,
            priority=10,
            name="cost:tool_selected",
        )

        # Register for session:end to log total cost
        hooks.register(
            "session:end",
            self.on_session_end,
            priority=10,
            name="cost:session_end",
        )

        logger.debug("Registered cost-aware scheduler handlers")

    async def on_tool_selecting(self, event: str, data: dict[str, Any]) -> HookResult:
        """
        Observe tool selection, veto if too expensive.

        Args:
            event: Event name
            data: Contains tool_name, tool_input, available_tools

        Returns:
            HookResult with action (continue, deny, or modify)
        """
        tool_name = data.get("tool_name")
        available_tools = data.get("available_tools", [])

        if not tool_name:
            return HookResult(action="continue")

        # Get cost for this tool
        tool_cost = self.tool_costs.get(tool_name, 1.0)

        # Check if this would exceed budget
        if self.session_cost + tool_cost > self.max_cost:
            logger.warning(
                f"Tool {tool_name} would exceed cost budget ({self.session_cost} + {tool_cost} > {self.max_cost})"
            )

            # Try to find a cheaper alternative
            if self.prefer_cheaper:
                cheaper_tool = self._find_cheaper_alternative(tool_name, available_tools)
                if cheaper_tool:
                    logger.info(f"Cost scheduler suggesting cheaper tool: {tool_name} → {cheaper_tool}")
                    return HookResult(
                        action="modify",
                        data={"tool_name": cheaper_tool},
                        reason="Cost limit reached, using cheaper alternative",
                    )

            # No alternative, deny the tool
            return HookResult(
                action="deny", reason=f"Would exceed cost budget (current: {self.session_cost}, limit: {self.max_cost})"
            )

        # Within budget, continue
        logger.debug(f"Tool {tool_name} cost {tool_cost} within budget (total: {self.session_cost + tool_cost})")
        return HookResult(action="continue")

    async def on_tool_selected(self, event: str, data: dict[str, Any]) -> HookResult:
        """
        Track cost after tool is selected.

        Args:
            event: Event name
            data: Contains tool_name and source

        Returns:
            HookResult (always continue)
        """
        tool_name = data.get("tool_name")
        tool_cost = self.tool_costs.get(tool_name, 1.0)

        self.session_cost += tool_cost
        logger.debug(f"Added cost {tool_cost} for tool {tool_name}, total: {self.session_cost}")

        return HookResult(action="continue")

    async def on_session_end(self, event: str, data: dict[str, Any]) -> HookResult:
        """
        Log total session cost.

        Args:
            event: Event name
            data: Session end data

        Returns:
            HookResult (always continue)
        """
        logger.info(f"Session ended with total cost: {self.session_cost}")
        self.session_cost = 0.0  # Reset for next session
        return HookResult(action="continue")

    def _find_cheaper_alternative(self, tool: str, available_tools: list[str]) -> str | None:
        """
        Find a cheaper alternative tool if available.

        Args:
            tool: Original tool
            available_tools: List of available tools

        Returns:
            Cheaper tool name or None
        """
        current_cost = self.tool_costs.get(tool, 1.0)

        # Find tools with lower cost
        alternatives = []
        for alt_tool in available_tools:
            alt_cost = self.tool_costs.get(alt_tool, 1.0)
            if alt_cost < current_cost:
                alternatives.append((alt_tool, alt_cost))

        # Return cheapest alternative if found
        if alternatives:
            alternatives.sort(key=lambda x: x[1])
            return alternatives[0][0]

        return None
