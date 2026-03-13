"""Seed content for the mock Google Docs environment."""

from __future__ import annotations


def _style_span(body: str, needle: str, style: dict) -> dict:
    start = body.index(needle) + 1
    end = start + len(needle)
    return {"startIndex": start, "endIndex": end, "textStyle": style}


def _paragraph_entry(lines: list[str], line_index: int, **fields) -> dict:
    start = 1
    for idx, line in enumerate(lines):
        end = start + len(line)
        if idx == line_index:
            entry = {"startIndex": start, "endIndex": end}
            entry.update(fields)
            return entry
        start = end
    raise IndexError(f"Paragraph index out of range: {line_index}")


def _doc_from_lines(title: str, lines: list[str], heading_indices: list[int], bullet_indices: list[int], link_targets: dict[str, str] | None = None) -> dict:
    body = "".join(lines)
    paragraph_ops = []
    for idx in heading_indices:
        paragraph_ops.append(
            _paragraph_entry(
                lines,
                idx,
                namedStyleType="HEADING_1" if idx == 0 else "HEADING_2",
            )
        )
    for idx in bullet_indices:
        paragraph_ops.append(
            _paragraph_entry(
                lines,
                idx,
                bulletPreset="BULLET_DISC_CIRCLE_SQUARE",
                listId=f"seed-list-{idx}",
            )
        )

    text_spans = []
    if link_targets:
        for needle, url in link_targets.items():
            text_spans.append(_style_span(body, needle, {"link": {"url": url}}))
    return {
        "title": title,
        "body_text": body,
        "text_spans": text_spans,
        "paragraph_ops": paragraph_ops,
    }


DEFAULT_DOCUMENTS = [
    _doc_from_lines(
        "Agent Runbooks PRD",
        [
            "Agent Runbooks PRD\n",
            "Overview\n",
            "Ship agentic runbooks for support teams.\n",
            "Goals\n",
            "- Cut triage time by 40%\n",
            "- Launch pilot with 3 design partners\n",
            "Reference\n",
            "Roadmap: https://nexusai.com/roadmap\n",
        ],
        heading_indices=[0, 1, 3, 6],
        bullet_indices=[4, 5],
        link_targets={"https://nexusai.com/roadmap": "https://nexusai.com/roadmap"},
    ),
    _doc_from_lines(
        "2026 Strategy Memo",
        [
            "2026 Strategy Memo\n",
            "North Star Metric\n",
            "Increase weekly active automations by 3x.\n",
            "Operating Principles\n",
            "Focus on deterministic tool outcomes.\n",
        ],
        heading_indices=[0, 1, 3],
        bullet_indices=[],
    ),
    _doc_from_lines(
        "Launch Checklist",
        [
            "Launch Checklist\n",
            "Owners\n",
            "ReplaceMe Launch Owner\n",
            "Tasks\n",
            "Finalize security review\n",
            "Prepare rollback doc\n",
            "Notify design partners\n",
        ],
        heading_indices=[0, 1, 3],
        bullet_indices=[],
    ),
    _doc_from_lines(
        "Incident Review - API Timeout",
        [
            "Incident Review - API Timeout\n",
            "Summary\n",
            "SEV-2 incident impacted document export latency.\n",
            "Findings\n",
            "- Queue depth alert fired late\n",
            "- Retry budget was exhausted\n",
            "Follow-up\n",
            "Document remediation owners by Friday.\n",
        ],
        heading_indices=[0, 1, 3, 6],
        bullet_indices=[4, 5],
    ),
    _doc_from_lines(
        "Customer Discovery Notes",
        [
            "Customer Discovery Notes\n",
            "ACME asked for policy-aware drafting support.\n",
            "Bluebird needs approval workflows.\n",
            "Notes to delete: stale placeholder sentence.\n",
        ],
        heading_indices=[0],
        bullet_indices=[],
    ),
    _doc_from_lines(
        "New Hire Onboarding",
        [
            "New Hire Onboarding\n",
            "Day 1\n",
            "Read the team charter and open the docs workspace.\n",
            "Day 2\n",
            "Shadow an incident review and write notes.\n",
        ],
        heading_indices=[0, 1, 3],
        bullet_indices=[],
    ),
]


LAUNCH_CRUNCH_EXTRA_DOCUMENTS = [
    _doc_from_lines(
        "Launch War Room Notes",
        [
            "Launch War Room Notes\n",
            "Action Items\n",
            "Capture live issues in this doc.\n",
            "TODO_OWNER follow up with support.\n",
        ],
        heading_indices=[0, 1],
        bullet_indices=[],
    ),
    _doc_from_lines(
        "Design Partner FAQ",
        [
            "Design Partner FAQ\n",
            "Q1\n",
            "How will data retention work?\n",
            "Q2\n",
            "Can we export notes as HTML?\n",
        ],
        heading_indices=[0, 1, 3],
        bullet_indices=[],
    ),
    _doc_from_lines(
        "Launch Retro Agenda",
        [
            "Launch Retro Agenda\n",
            "Topics\n",
            "Wins\n",
            "Risks\n",
            "Open questions\n",
        ],
        heading_indices=[0, 1],
        bullet_indices=[],
    ),
]


SCENARIO_DEFINITIONS = {
    "default": {"target_documents": len(DEFAULT_DOCUMENTS)},
    "launch_crunch": {"target_documents": len(DEFAULT_DOCUMENTS) + len(LAUNCH_CRUNCH_EXTRA_DOCUMENTS)},
    "long_context": {"target_documents": 160},
}
