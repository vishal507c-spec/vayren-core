"""Edit efficiency, repair tax and first-pass quality (§13, §14, §17).

All functions are pure over explicitly passed inputs. Nothing is inferred
from hidden state: a metric whose inputs were not recorded is None
(UNMEASURED).
"""

from __future__ import annotations


def repair_tax(record: dict) -> dict:
    """REPAIR_TAX for one journal record (§14).

    ``repair_tax_s`` is the FIRST_VALIDATION → FINAL_GREEN window when the
    task needed repairs, else 0.0. The window is an upper bound on repair
    cost (it includes the final green validation itself) and is labeled so.
    """
    segments = record.get("segments") or {}
    window = segments.get("validation_window_s")
    total = record.get("total_s")
    repairs = record.get("repairs")
    if repairs is None:
        return {"repair_tax_s": None, "repair_tax_ratio": None, "basis": "UNMEASURED"}
    if repairs == 0:
        # Zero repairs means zero repair cost by count — no clock needed.
        return {"repair_tax_s": 0.0, "repair_tax_ratio": 0.0, "basis": "measured"}
    if window is None:
        return {"repair_tax_s": None, "repair_tax_ratio": None, "basis": "UNMEASURED"}
    if not isinstance(window, (int, float)):
        return {"repair_tax_s": None, "repair_tax_ratio": None, "basis": "UNMEASURED"}
    ratio = None
    if isinstance(total, (int, float)) and float(total) > 0:
        ratio = round(float(window) / float(total), 3)
    return {
        "repair_tax_s": round(float(window), 3),
        "repair_tax_ratio": ratio,
        "basis": "measured (window is an upper bound: includes final green run)",
    }


def first_pass(record: dict) -> bool | None:
    """FIRST_PASS_RATE input for one record (§17).

    True when the task reached final green without implementation retry
    caused by agent error (repairs == 0 and failures == 0). None when the
    retry evidence was not recorded.
    """
    repairs = record.get("repairs")
    failures = record.get("failures")
    if repairs is None or failures is None:
        return None
    return bool(repairs == 0 and failures == 0)


def edit_efficiency(
    *,
    total_edits: int | None = None,
    failed_edits: int | None = None,
    repeated_edits: int | None = None,
    reverted_edits: int | None = None,
    unnecessary_edits: int | None = None,
) -> dict:
    """Edit repetition pattern metrics (§13).

    Goal: ONE CORRECT EDIT. Rates are None when the numerator or the total
    was not counted — never defaulted to zero.
    """
    outcome: dict = {
        "total_edits": total_edits,
        "failed_edits": failed_edits,
        "repeated_edits": repeated_edits,
        "reverted_edits": reverted_edits,
        "unnecessary_edits": unnecessary_edits,
    }
    if total_edits is None or total_edits <= 0:
        outcome.update(
            {
                "failed_rate": None,
                "repeated_rate": None,
                "rework_rate": None,
            }
        )
        return outcome
    outcome["failed_rate"] = (
        round(failed_edits / total_edits, 3) if failed_edits is not None else None
    )
    outcome["repeated_rate"] = (
        round(repeated_edits / total_edits, 3) if repeated_edits is not None else None
    )
    rework_parts = [v for v in (failed_edits, repeated_edits, reverted_edits) if v is not None]
    outcome["rework_rate"] = round(sum(rework_parts) / total_edits, 3) if rework_parts else None
    return outcome
