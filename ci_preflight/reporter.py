from typing import List
from ci_preflight.models import Prediction


def render(predictions: List[Prediction]) -> str:
    lines = []
    lines.append("=" * 52)
    lines.append("  CI PREFLIGHT RISK REPORT")
    lines.append("=" * 52)

    if not predictions:
        lines.append("")
        lines.append("  ✓  No risks detected.")
        lines.append("     Pipeline looks clean from here.")
        lines.append("")
        lines.append("=" * 52)
        return "\n".join(lines)

    lines.append("")
    lines.append(f"  {len(predictions)} predicted failure(s) found.")
    lines.append("")

    for i, p in enumerate(predictions, start=1):
        lines.append(f"  [{i}] {p.severity()}  —  {p.failure_type.replace('_', ' ').upper()}")
        lines.append(f"      Contract   : {p.violated_contract}")
        lines.append(f"      Stage      : {p.impact_stage}")
        lines.append(f"      Confidence : {int(p.confidence * 100)}%")
        lines.append("")
        lines.append("      Signals detected:")
        for s in p.signals:
            lines.append(f"        •  {s.description}")
        lines.append("")
        lines.append("      Recommendation:")
        lines.append(f"        {p.recommendation}")
        lines.append("")
        lines.append("  " + "-" * 48)
        lines.append("")

    lines.append("=" * 52)
    return "\n".join(lines)


def print_report(predictions: List[Prediction]) -> None:
    print(render(predictions))
