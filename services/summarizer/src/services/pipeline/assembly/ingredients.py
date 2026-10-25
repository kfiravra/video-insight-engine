"""Ingredient checklist rows (output-quality, S06 iteration): amount + name, honestly.

A checklist row reads "1 cup whole milk": the amount, its unit, the name. An
amount that is no quantity — "liberally", "to taste", "a little layer" — is
how the speaker uses the ingredient, not how much: the row is the name and the
amount leads its note ("salt" · "liberally, used on the meat …"). An amount
that only says it is unknown ("amount not stated") is no amount at all (R10).
"""

from __future__ import annotations

import re
from typing import Any

# A display amount that only says the amount is unknown ("amount not stated").
_UNSTATED_AMOUNT = re.compile(
    r"^(?:amount\s+)?(?:not\s+(?:stated|given|specified|mentioned|shown)"
    r"|unspecified|unknown|n/?a)\.?$",
    re.IGNORECASE,
)
# A quantity: a digit or a vulgar fraction, a number or fraction word, or a
# measure ("a pinch", "a can", "two cloves").
_QUANTITY = re.compile(
    r"\d|[½⅓⅔¼¾⅛⅜⅝⅞]"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|half|"
    r"quarter|third|couple|few|several|pinch|dash|handful|splash|sprig|clove|can|stick|"
    r"slice|bunch|head|cup|tablespoons?|teaspoons?|tbsp|tsp|grams?|kg|ml|liters?|"
    r"litres?|ounces?|oz|pounds?|lbs?)\b",
    re.IGNORECASE,
)
# An amount that is only a number: whole, decimal, a fraction or a vulgar one, a
# mixed number ("1 1/2", "1½") or a range ("3-4", "2 to 3") — it needs its unit.
_NUMBER = r"(?:\d+(?:[.,]\d+)?(?:/\d+)?|\d*[½⅓⅔¼¾⅛⅜⅝⅞])"
_BARE_NUMBER = re.compile(
    rf"^{_NUMBER}(?:(?:\s*[-–—]\s*|\s+(?:to|or)\s+|\s+){_NUMBER})*$", re.IGNORECASE
)


def display_amount(item: dict) -> str:
    """The item's displayAmount, or '' when it only says the amount is unknown."""
    shown = str(item.get("displayAmount", "")).strip()
    return "" if _UNSTATED_AMOUNT.match(shown) else shown


def _with_unit(amount: str, unit: Any) -> str:
    """A bare number gains its unit ("1" + "cup" → "1 cup", "1 1/2" + "cups" →
    "1 1/2 cups", "3-4" + "cloves" → "3-4 cloves"); anything else stays."""
    unit_text = str(unit or "").strip()
    if unit_text and _BARE_NUMBER.match(amount):
        return f"{amount} {unit_text}"
    return amount


def _numeric_amount(item: dict) -> str:
    """ "6 tablespoons" from the numeric amount + unit; '' without a positive amount
    (an unstated amount is None or 0 — never a quantity)."""
    amount = item.get("amount")
    if not amount:
        return ""
    unit = str(item.get("unit") or "").strip()
    return f"{amount} {unit}".strip()


def _note(lead: str, notes: Any) -> str | None:
    rest = str(notes or "").strip()
    if lead and rest:
        return f"{lead}, {rest}"
    return lead or rest or None


def ingredient_row(item: dict) -> dict:
    """One ingredient as a checklist row: ``{label, note}``."""
    name = str(item["name"])
    amount = display_amount(item) or _numeric_amount(item)
    if amount and not _QUANTITY.search(amount):
        return {"label": name, "note": _note(amount, item.get("notes"))}
    amount = _with_unit(amount, item.get("unit")) if amount else ""
    label = f"{amount} {name}" if amount else name
    return {"label": label, "note": item.get("notes")}
