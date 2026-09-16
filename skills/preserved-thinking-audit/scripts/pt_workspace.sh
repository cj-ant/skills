#!/usr/bin/env bash
# Create the scratch repo every harness works in: small Python project, one real bug, one failing test,
# an AGENTS.md, and enough files to make multi-file reads and greps worthwhile. Never point a harness at a real project.
#   pt_workspace.sh <dir>
set -euo pipefail
dir="${1:?usage: pt_workspace.sh <dir>}"
mkdir -p "$dir/inventory" "$dir/tests" "$dir/docs"
cd "$dir"
cat > AGENTS.md <<'MD'
# Inventory service

Small stock-keeping library. Run tests with `python3 -m unittest discover -s tests -v`.
Keep functions pure. Do not add dependencies. Prices are integer cents.
MD
cat > inventory/__init__.py <<'PY'
from .stock import Stock, Item
from .pricing import order_total, bulk_discount
PY
cat > inventory/stock.py <<'PY'
from dataclasses import dataclass, field


@dataclass
class Item:
    sku: str
    name: str
    price_cents: int
    quantity: int = 0


@dataclass
class Stock:
    items: dict[str, Item] = field(default_factory=dict)

    def add(self, item: Item) -> None:
        # TODO: merge quantities when the sku already exists
        self.items[item.sku] = item

    def remove(self, sku: str, quantity: int) -> None:
        item = self.items[sku]
        if quantity > item.quantity:
            raise ValueError(f"only {item.quantity} of {sku} in stock")
        item.quantity -= quantity

    def low_stock(self, threshold: int = 5) -> list[Item]:
        # BUG: should include items exactly at the threshold
        return sorted((i for i in self.items.values() if i.quantity < threshold), key=lambda i: i.sku)
PY
cat > inventory/pricing.py <<'PY'
from .stock import Item


def bulk_discount(quantity: int) -> float:
    """10% off at 10 units, 20% off at 50 units."""
    if quantity >= 10:
        return 0.10
    if quantity >= 50:  # BUG: unreachable, tiers are in the wrong order
        return 0.20
    return 0.0


def order_total(lines: list[tuple[Item, int]]) -> int:
    """Total in cents after per-line bulk discounts, rounded half up."""
    total = 0
    for item, quantity in lines:
        gross = item.price_cents * quantity
        # TODO: rounding should be half-up, int() truncates
        total += int(gross * (1 - bulk_discount(quantity)))
    return total
PY
cat > inventory/report.py <<'PY'
from .stock import Stock


def stock_report(stock: Stock) -> str:
    # TODO: right-align the quantity column
    lines = [f"{i.sku:<8} {i.name:<20} {i.quantity}" for i in sorted(stock.items.values(), key=lambda i: i.sku)]
    return "\n".join(lines)
PY
cat > tests/test_pricing.py <<'PY'
import unittest

from inventory import Item, bulk_discount, order_total


class PricingTest(unittest.TestCase):
    def test_discount_tiers(self):
        self.assertEqual(bulk_discount(9), 0.0)
        self.assertEqual(bulk_discount(10), 0.10)
        self.assertEqual(bulk_discount(50), 0.20)

    def test_order_total(self):
        widget = Item("W-1", "widget", 199)
        self.assertEqual(order_total([(widget, 50)]), 7960)
        self.assertEqual(order_total([(widget, 3)]), 597)


if __name__ == "__main__":
    unittest.main()
PY
cat > tests/test_stock.py <<'PY'
import unittest

from inventory import Item, Stock


class StockTest(unittest.TestCase):
    def test_low_stock_includes_threshold(self):
        stock = Stock()
        stock.add(Item("A-1", "anvil", 5000, quantity=5))
        stock.add(Item("B-2", "bolt", 10, quantity=40))
        self.assertEqual([i.sku for i in stock.low_stock(5)], ["A-1"])


if __name__ == "__main__":
    unittest.main()
PY
cat > docs/NOTES.md <<'MD'
# Notes
- TODO: document the discount tiers
- Reports are plain text on purpose.
MD
git init -q . 2>/dev/null || true
git symbolic-ref HEAD refs/heads/main 2>/dev/null || true   # same branch name everywhere, whatever init.defaultBranch says
git add -A >/dev/null 2>&1 && git -c user.name=pt -c user.email=pt@example.invalid commit -qm "scratch inventory project" >/dev/null 2>&1 || true
echo "$dir"
