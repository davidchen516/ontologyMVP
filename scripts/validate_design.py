#!/usr/bin/env python3
"""Validate ontologyMVP design assets.

Checks:
- Turtle syntax for all ontology/*.ttl files.
- YAML syntax for ontology/*.yaml and ontology/**/*.yaml.
- Local Markdown and generated Jekyll HTML links in README.md and docs/*.md.
- Required design artifacts are present.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml
from rdflib import Graph

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    "README.md",
    "docs/architecture.md",
    "docs/domain-model.md",
    "docs/data-sources-and-ingestion.md",
    "docs/semantica-integration.md",
    "docs/query-and-api.md",
    "docs/database-schema.sql",
    "docs/neo4j-schema.cypher",
    "docs/quality-and-testing.md",
    "docs/delivery-plan.md",
    "docs/adr/0001-storage-responsibilities.md",
    "docs/adr/0002-claim-centered-model.md",
    "ontology/README.md",
    "ontology/stock-core.ttl",
    "ontology/product-skos.ttl",
    "ontology/shapes.ttl",
    "ontology/rules.yaml",
    "ontology/mappings/tushare.yaml",
}

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def check_required_files(errors: list[str]) -> None:
    for relative_path in sorted(REQUIRED_FILES):
        if not (ROOT / relative_path).is_file():
            errors.append(f"missing required file: {relative_path}")


def validate_turtle(errors: list[str]) -> None:
    for path in sorted((ROOT / "ontology").rglob("*.ttl")):
        try:
            Graph().parse(path, format="turtle")
            print(f"OK turtle: {path.relative_to(ROOT)}")
        except Exception as exc:  # rdflib exposes parser-specific exceptions
            errors.append(f"invalid Turtle {path.relative_to(ROOT)}: {exc}")


def validate_yaml(errors: list[str]) -> None:
    for path in sorted((ROOT / "ontology").rglob("*.yaml")):
        try:
            with path.open("r", encoding="utf-8") as handle:
                yaml.safe_load(handle)
            print(f"OK yaml:   {path.relative_to(ROOT)}")
        except Exception as exc:
            errors.append(f"invalid YAML {path.relative_to(ROOT)}: {exc}")


def is_external_link(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(("http://", "https://", "mailto:", "#"))


def validate_markdown_links(errors: list[str]) -> None:
    markdown_files = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for markdown_path in markdown_files:
        text = markdown_path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split("#", maxsplit=1)[0]
            if not target or is_external_link(raw_target.strip()):
                continue
            target = unquote(target)
            resolved = (markdown_path.parent / target).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                errors.append(
                    f"link escapes repository: {markdown_path.relative_to(ROOT)} -> {raw_target}"
                )
                continue
            generated_source = (
                resolved.with_suffix(".md") if resolved.suffix == ".html" else None
            )
            if not resolved.exists() and not (
                generated_source is not None and generated_source.exists()
            ):
                errors.append(
                    f"broken local link: {markdown_path.relative_to(ROOT)} -> {raw_target}"
                )
        print(f"OK links:  {markdown_path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    check_required_files(errors)
    validate_turtle(errors)
    validate_yaml(errors)
    validate_markdown_links(errors)

    if errors:
        print("\nValidation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("\nAll design assets passed validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
