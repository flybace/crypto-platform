"""Controlled execution use case with no retry or direct network behavior."""

from dataclasses import dataclass

from domain.risk import RiskContext, RiskDecision, RiskLimits
from domain.trading import OrderIntent
from ports.execution import ExecutionGateway, OrderReceipt

from .risk_precheck import SellOnlyRiskPrechecker


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    decision: RiskDecision
    receipt: OrderReceipt | None = None

    @property
    def submitted(self) -> bool:
        return self.receipt is not None


class SellOnlyExecutionService:
    """Submit exactly once after the full server-side pre-check passes."""

    def __init__(self, gateway: ExecutionGateway, prechecker: SellOnlyRiskPrechecker | None = None) -> None:
        self._gateway = gateway
        self._prechecker = prechecker or SellOnlyRiskPrechecker()

    def submit(self, intent: OrderIntent, limits: RiskLimits, context: RiskContext) -> ExecutionAttempt:
        decision = self._prechecker.evaluate(intent, limits, context)
        if not decision.allowed:
            return ExecutionAttempt(decision=decision)
        return ExecutionAttempt(decision=decision, receipt=self._gateway.submit(intent))
