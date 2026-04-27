"""E4 confusion matrix calculator — precision, recall, F1 per category and overall."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evaluation.e4_ethics.ethics_test_runner import EthicsTestResult
from evaluation.e4_ethics.synthetic_dataset import EthicsTestCase


@dataclass
class CategoryMetrics:
    category: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


def compute_confusion_matrix(
    results: list[EthicsTestResult],
    dataset: list[EthicsTestCase],
) -> tuple[dict[str, CategoryMetrics], CategoryMetrics]:
    """Compute per-category and overall confusion matrix.

    Returns:
        (per_category_metrics, overall_metrics)
    """
    # Index dataset by case_id
    case_map = {c.case_id: c for c in dataset}

    # Collect all violation categories
    categories = sorted({c.category for c in dataset if c.category != "clean"})

    per_category: dict[str, CategoryMetrics] = {cat: CategoryMetrics(category=cat) for cat in categories}
    overall = CategoryMetrics(category="overall")

    for result in results:
        case = case_map.get(result.case_id)
        if case is None:
            continue

        expected = case.expected_detected
        predicted = result.predicted_detected

        # Overall
        if expected and predicted:
            overall.tp += 1
        elif not expected and predicted:
            overall.fp += 1
        elif expected and not predicted:
            overall.fn += 1
        else:
            overall.tn += 1

        # Per category
        if case.category == "clean":
            # Clean cases affect ALL category FP counts if wrongly flagged
            if predicted:
                for cat in categories:
                    per_category[cat].fp += 1
            else:
                for cat in categories:
                    per_category[cat].tn += 1
        else:
            cat = case.category
            if cat in per_category:
                if expected and predicted:
                    per_category[cat].tp += 1
                elif expected and not predicted:
                    per_category[cat].fn += 1
                elif not expected and predicted:
                    per_category[cat].fp += 1
                else:
                    per_category[cat].tn += 1

    return per_category, overall


def generate_metrics_table(
    per_category: dict[str, CategoryMetrics],
    overall: CategoryMetrics,
    mode_label: str = "",
) -> str:
    """Generate a markdown table of metrics."""
    label = f" ({mode_label})" if mode_label else ""
    lines = [
        f"### Ethics Detection Metrics{label}",
        "",
        "| Category | TP | FP | FN | TN | Precision | Recall | F1 |",
        "|----------|----|----|----|----|-----------|--------|-----|",
    ]

    for cat, metrics in per_category.items():
        lines.append(
            f"| {cat} | {metrics.tp} | {metrics.fp} | {metrics.fn} | {metrics.tn} | "
            f"{metrics.precision:.2f} | {metrics.recall:.2f} | {metrics.f1:.2f} |"
        )

    lines.append(
        f"| **Overall** | **{overall.tp}** | **{overall.fp}** | **{overall.fn}** | **{overall.tn}** | "
        f"**{overall.precision:.2f}** | **{overall.recall:.2f}** | **{overall.f1:.2f}** |"
    )
    lines.append("")
    return "\n".join(lines)


def generate_comparison_table(
    stub_per_cat: dict[str, CategoryMetrics],
    stub_overall: CategoryMetrics,
    llm_per_cat: dict[str, CategoryMetrics],
    llm_overall: CategoryMetrics,
) -> str:
    """Side-by-side comparison of stub vs LLM mode."""
    categories = sorted(set(stub_per_cat.keys()) | set(llm_per_cat.keys()))

    lines = [
        "### Stub vs LLM Mode Comparison",
        "",
        "| Category | Stub Recall | Stub Precision | Stub F1 | LLM Recall | LLM Precision | LLM F1 |",
        "|----------|-------------|----------------|---------|------------|---------------|--------|",
    ]

    for cat in categories:
        s = stub_per_cat.get(cat, CategoryMetrics(category=cat))
        l = llm_per_cat.get(cat, CategoryMetrics(category=cat))
        lines.append(
            f"| {cat} | {s.recall:.2f} | {s.precision:.2f} | {s.f1:.2f} | "
            f"{l.recall:.2f} | {l.precision:.2f} | {l.f1:.2f} |"
        )

    lines.append(
        f"| **Overall** | **{stub_overall.recall:.2f}** | **{stub_overall.precision:.2f}** | "
        f"**{stub_overall.f1:.2f}** | **{llm_overall.recall:.2f}** | "
        f"**{llm_overall.precision:.2f}** | **{llm_overall.f1:.2f}** |"
    )
    lines.append("")
    return "\n".join(lines)


def generate_full_report(
    stub_results: list[EthicsTestResult],
    llm_results: list[EthicsTestResult] | None,
    dataset: list[EthicsTestCase],
) -> str:
    """Generate the complete E4 report."""
    sections = [
        "# E4 Ethics Detection Evaluation Report",
        "",
        f"Total test cases: {len(dataset)}",
        f"Violation cases: {sum(1 for c in dataset if c.expected_detected)}",
        f"Clean cases: {sum(1 for c in dataset if not c.expected_detected)}",
        "",
        "---",
        "",
    ]

    # Stub results
    stub_per_cat, stub_overall = compute_confusion_matrix(stub_results, dataset)
    sections.append(generate_metrics_table(stub_per_cat, stub_overall, "Rule-Based / Stub"))

    # Per-case stub results detail
    sections.extend([
        "#### Stub Mode — Detailed Results",
        "",
        "| Case ID | Category | Expected | Predicted | Correct |",
        "|---------|----------|----------|-----------|---------|",
    ])
    case_map = {c.case_id: c for c in dataset}
    for r in stub_results:
        c = case_map.get(r.case_id)
        if c:
            correct = c.expected_detected == r.predicted_detected
            sections.append(
                f"| {r.case_id} | {c.category} | "
                f"{'violation' if c.expected_detected else 'clean'} | "
                f"{'violation' if r.predicted_detected else 'clean'} | "
                f"{'Yes' if correct else '**No**'} |"
            )
    sections.append("")

    # LLM results
    if llm_results:
        llm_per_cat, llm_overall = compute_confusion_matrix(llm_results, dataset)
        sections.append(generate_metrics_table(llm_per_cat, llm_overall, "LLM-Powered"))
        sections.extend([
            "---",
            "",
            generate_comparison_table(stub_per_cat, stub_overall, llm_per_cat, llm_overall),
        ])

    return "\n".join(sections)
