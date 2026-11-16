"""Finding model.

Deliberately not line-keyed: every finding carries the node span it came from.
Line-keyed diagnostics are what force byte-grid tools to guess at structure, and
they make continuation lines indistinguishable from real nesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    message: str
    line: int
    col: int
    end_line: int
    end_col: int
    evidence: str

    def to_dict(self) -> dict:
        out = {
            "rule": self.rule,
            "message": self.message,
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
            "evidence": self.evidence,
        }
        return out

    def overlaps(self, start: int, end: int) -> bool:
        """True if this finding's span intersects the 1-indexed range [start, end]."""
        return self.line <= end and self.end_line >= start


@dataclass
class Report:
    path: str
    lang: str
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "lang": self.lang,
            "findings": [f.to_dict() for f in self.findings],
        }
