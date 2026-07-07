from execution.models.fill import Fill


class FillValidator:
    def validate(self, fill: Fill) -> list[str]:
        issues: list[str] = []
        if fill.quantity <= 0:
            issues.append("Quantity must be positive")
        if fill.price <= 0:
            issues.append("Price must be positive")
        if not fill.symbol:
            issues.append("Symbol is required")
        if not fill.timestamp:
            issues.append("Timestamp is required")
        if fill.side not in ("buy", "sell"):
            issues.append("Side must be buy or sell")
        return issues

    def is_valid(self, fill: Fill) -> bool:
        return len(self.validate(fill)) == 0
