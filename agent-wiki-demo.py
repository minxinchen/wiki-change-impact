#!/usr/bin/env python3
"""Run the synthetic agent knowledge-base maintenance example without an LLM."""
import argparse
import json
from pathlib import Path
import shutil
import wiki_impact as w


def run(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(__file__).parent / "examples/agent-wiki", out / "case")
    config = w.load_config(out / "case/config.json")
    before = w.snapshot(config)
    w.validate_snapshot(before)
    page = out / "case/vault/wiki/tool-selection.md"
    page.write_text(page.read_text().replace("tool-policy-v1.md", "tool-policy-v2.md"))
    report = w.compare(before, w.snapshot(config))
    expected = {"assistant:wiki/tool-selection.md", "assistant:INDEX.md"}
    actual = {item["page"] for item in report["impacted_pages"]}
    if actual != expected or report["status"] != "needs_review":
        raise ValueError("Unexpected impact result")
    w.write_new(out / "report.json", json.dumps(report, ensure_ascii=False, indent=2), config)
    w.write_new(out / "report.html", w.render(report), config)
    return {"example": "synthetic; no LLM", "status": report["status"],
            "pages_to_review": sorted(actual), "report": str(out / "report.html")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.out), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
