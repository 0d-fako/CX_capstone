"""The grounding gate (doc 09 §7). Runs between draft and review, fails loudly, never edits.

Full mode (reports):
  1. every [T#] ref resolves to a tool call in this run
  2. every numeric token in [D]/[I] sections is backed by a cited ref whose output contains it
  3. no URL absent from tool results
  4. no reader-directed imperatives that did not come from the brief (heuristic)
  5. hooks ([G] section) are labelled AI-generated and pinned to an eligible cell
  6. any cell a tool returned as insufficient does not appear with numbers outside "Collecting"
Light mode (chat turns): rules 1, 2 (paragraph-scoped) and 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from smia.agent.trace import RunContext

REF_RE = re.compile(r"\[(T\d+)\](?:\[(T\d+)\])*")
REF_ALL_RE = re.compile(r"\[(T\d+)\]")
REF_BARE_RE = re.compile(r"(?<![\w\[])(T\d+)(?![\w\]])")
NUM_RE = re.compile(r"(?<![\w/#-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(%|x|×)?(?![\w/])")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
SECTION_RE = re.compile(r"^#{1,6}\s+.*?\[(D|I|G|D/I)\]\s*$", re.MULTILINE)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")

IMPERATIVE_STARTS = (
    "ignore ", "disregard ", "you must now ", "from now on ", "click ", "visit ", "send ",
    "enter your ", "reply with ", "forward this ", "share this link", "install ",
)

# numbers that are structure, not claims
TRIVIAL = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "0", "12"}


@dataclass
class Finding:
    rule: str
    detail: str
    location: str = ""


@dataclass
class ValidationReport:
    ok: bool
    mode: str
    findings: list[Finding] = field(default_factory=list)
    refs_cited: list[str] = field(default_factory=list)
    numbers_checked: int = 0
    numbers_unbacked: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "mode": self.mode,
            "findings": [f.__dict__ for f in self.findings],
            "refs_cited": self.refs_cited,
            "numbers_checked": self.numbers_checked, "numbers_unbacked": self.numbers_unbacked,
        }

    def summary(self) -> str:
        if self.ok:
            return f"validation passed ({self.numbers_checked} numbers checked, {len(self.refs_cited)} refs)"
        by_rule: dict[str, int] = {}
        for f in self.findings:
            by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
        return "validation FAILED: " + ", ".join(f"{k} x{v}" for k, v in by_rule.items())


# --------------------------------------------------------------------------- helpers


def _flatten_numbers(obj: Any, out: set[str]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        out.add(_norm(str(obj)))
        if isinstance(obj, float):
            for nd in (0, 1, 2, 3):
                out.add(_norm(f"{obj:.{nd}f}"))
        if isinstance(obj, int | float) and 0 <= obj <= 1:
            for nd in (0, 1, 2):
                out.add(_norm(f"{obj * 100:.{nd}f}"))  # shares as percentages
    elif isinstance(obj, str):
        for m in NUM_RE.finditer(obj):
            out.add(_norm(m.group(1)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _flatten_numbers(v, out)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _flatten_numbers(v, out)


def _norm(n: str) -> str:
    n = n.replace(",", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n or "0"


def _tool_numbers(ctx: RunContext) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for c in ctx.calls:
        nums: set[str] = set()
        _flatten_numbers(c.output, nums)
        _flatten_numbers(c.input, nums)
        out[c.ref] = nums
    return out


def _tool_urls(ctx: RunContext) -> set[str]:
    urls: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, str):
            urls.update(URL_RE.findall(o))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list | tuple):
            for v in o:
                walk(v)

    for c in ctx.calls:
        walk(c.output)
    return urls


def _sections(draft: str) -> list[tuple[str, str, str]]:
    """(marker, heading, body) for each [D]/[I]/[G] section; prelude gets marker 'I'."""
    matches = list(SECTION_RE.finditer(draft))
    if not matches:
        return [("I", "(whole)", draft)]
    out = []
    if matches[0].start() > 0:
        out.append(("I", "(prelude)", draft[: matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(draft)
        marker = m.group(1).split("/")[0]
        out.append((marker, m.group(0).strip(), draft[m.end() : end]))
    return out


def _strip_noise(text: str) -> str:
    text = DATE_RE.sub(" ", text)
    text = TIME_RE.sub(" ", text)
    text = re.sub(r"\b(post|posts|id|ids|n)\s*=?\s*#?\d+\b", " ", text, flags=re.IGNORECASE)  # post ids, n=
    text = re.sub(r"\b\d+\s*-?\s*(d|day|days|wk|week|weeks|s|sec|min|h|hour|hours)\b", " ", text)  # windows/durations
    text = re.sub(r"\bT\d+\b", " ", text)
    text = re.sub(r"^\s*\d+\.\s", " ", text, flags=re.MULTILINE)  # list numbering
    text = re.sub(r"\|[-:| ]+\|", " ", text)  # table rules
    return text


def _check_numbers(body: str, tool_nums: dict[str, set[str]], all_nums: set[str], location: str,
                   findings: list[Finding], scope: str = "sentence") -> tuple[int, int]:
    """Every number in a sentence (or paragraph) must appear in the output of a ref cited in
    that same unit. A number backed by a ref cited elsewhere in the same section is a warning,
    not a failure. Returns (checked, unbacked)."""
    checked = unbacked = 0
    section_refs = set(REF_ALL_RE.findall(body)) | set(REF_BARE_RE.findall(body))
    section_backing: set[str] = set()
    for r in section_refs:
        section_backing |= tool_nums.get(r, set())
    if scope == "paragraph":
        units = re.split(r"\n\s*\n", body)
    else:
        units = re.split(r"(?<=[.;:!?])\s+|\n", body)
    for unit in units:
        if not unit.strip():
            continue
        refs = set(REF_ALL_RE.findall(unit)) | set(REF_BARE_RE.findall(unit))
        cleaned = _strip_noise(REF_ALL_RE.sub(" ", unit))
        nums = [_norm(m.group(1)) for m in NUM_RE.finditer(cleaned)]
        nums = [n for n in nums if n not in TRIVIAL]
        if not nums:
            continue
        backing: set[str] = set()
        for r in refs:
            backing |= tool_nums.get(r, set())
        for n in nums:
            checked += 1
            if n in backing:
                continue
            if n in section_backing:
                findings.append(Finding("uncited_in_sentence", f"{n!r} is backed by a ref cited elsewhere in this section, not in this sentence", location))
                continue
            unbacked += 1
            if n in all_nums:
                findings.append(Finding("uncited_number", f"{n!r} matches a tool result but no ref in this section returns it", location))
            else:
                findings.append(Finding("unbacked_number", f"{n!r} not found in any cited ref {sorted(refs) or '(none)'}", location))
    return checked, unbacked


# --------------------------------------------------------------------------- public


def validate(draft: str, ctx: RunContext, *, mode: str = "full") -> ValidationReport:
    findings: list[Finding] = []
    tool_nums = _tool_numbers(ctx)
    all_nums: set[str] = set().union(*tool_nums.values()) if tool_nums else set()
    refs_cited = sorted(set(REF_ALL_RE.findall(draft)) | set(REF_BARE_RE.findall(draft)), key=lambda r: int(r[1:]))

    # 1. refs resolve
    for r in refs_cited:
        if ctx.by_ref(r) is None:
            findings.append(Finding("unknown_ref", f"{r} is not a tool call in this run"))

    # 3. URLs
    known = _tool_urls(ctx)
    for u in set(URL_RE.findall(draft)):
        if u.rstrip(".,") not in known and not any(k.startswith(u.rstrip(".,")) for k in known):
            findings.append(Finding("foreign_url", u))

    checked = unbacked = 0
    if mode == "light":
        c, u = _check_numbers(draft, tool_nums, all_nums, "(chat)", findings, scope="paragraph")
        checked += c
        unbacked += u
    else:
        insufficient_cells = _insufficient_cells(ctx)
        for marker, heading, body in _sections(draft):
            loc = heading
            if marker == "G":
                _check_hooks(body, ctx, loc, findings)
                continue
            if marker in ("D", "I"):
                c, u = _check_numbers(body, tool_nums, all_nums, loc, findings)
                checked += c
                unbacked += u
                if "collecting" not in heading.lower() and "evidence" not in heading.lower():
                    _check_insufficient(body, insufficient_cells, loc, findings)
            # 4. imperatives (all sections)
            for line in body.splitlines():
                low = line.strip().lower().lstrip("-*> ")
                if any(low.startswith(s) for s in IMPERATIVE_STARTS):
                    findings.append(Finding("reader_directed_instruction", line.strip()[:120], loc))

    ok = not any(f.rule in ("unknown_ref", "unbacked_number", "uncited_number", "foreign_url",
                            "reader_directed_instruction", "unpinned_hook", "insufficient_promoted")
                 for f in findings)
    return ValidationReport(ok=ok, mode=mode, findings=findings, refs_cited=refs_cited,
                            numbers_checked=checked, numbers_unbacked=unbacked)


def _insufficient_cells(ctx: RunContext) -> list[tuple[str, str]]:
    """Cells that are insufficient and whose label is never eligible anywhere else in the run.
    'other' is skipped (too common a word), and a cross cell is only listed as a pair."""
    eligible_labels: set[str] = set()
    insufficient: list[tuple[str, str]] = []
    for c in ctx.calls:
        if c.name != "cell_stats" or not isinstance(c.output, dict):
            continue
        for cell in c.output.get("cells", []):
            label = (cell.get("label") or "").lower()
            xlabel = (cell.get("cross_label") or "").lower()
            if cell.get("confidence") in ("directional", "established"):
                eligible_labels.add(label)
                if xlabel:
                    eligible_labels.add(f"{label}/{xlabel}")
            elif cell.get("confidence") == "insufficient":
                insufficient.append((label, xlabel))
    out = []
    for label, xlabel in insufficient:
        if not label or label == "other" or xlabel == "other":
            continue
        if xlabel:
            if f"{label}/{xlabel}" not in eligible_labels:
                out.append((label, xlabel))
        elif label not in eligible_labels:
            out.append((label, ""))
    return out


def _check_insufficient(body: str, cells: list[tuple[str, str]], loc: str, findings: list[Finding]) -> None:
    """An insufficient cell may be named, but not with a median RE attached. A cross cell only
    counts when both labels appear together."""
    low = body.lower()
    for label, xlabel in cells:
        for m in re.finditer(re.escape(label), low):
            window = low[max(0, m.start() - 80): m.end() + 160]
            if xlabel and xlabel not in window:
                continue
            after = low[m.end(): m.end() + 160]
            if ("median re" in after or "re_med" in after) and "insufficient" not in window and "collecting" not in window and "too early" not in window:
                findings.append(Finding("insufficient_promoted", f"cell {label}{'/' + xlabel if xlabel else ''} appears with a metric", loc))
                break


def _eligible_cells(ctx: RunContext) -> set[str]:
    out: set[str] = set()
    for c in ctx.calls:
        if c.name != "cell_stats" or not isinstance(c.output, dict):
            continue
        for cell in c.output.get("cells", []):
            if cell.get("confidence") in ("directional", "established"):
                out.add(cell["label"].lower())
                if cell.get("cross_label"):
                    out.add(cell["cross_label"].lower())
    return out


def _check_hooks(body: str, ctx: RunContext, loc: str, findings: list[Finding]) -> None:
    if not body.strip():
        return
    low = body.lower()
    if "ai-generated" not in low and "ai generated" not in low:
        findings.append(Finding("unpinned_hook", "hooks section lacks the AI-generated label", loc))
    eligible = _eligible_cells(ctx)
    if not eligible:
        if not any(w in low for w in ("no eligible", "omitted", "collecting")):
            findings.append(Finding("unpinned_hook", "no eligible cell exists but hooks were generated", loc))
        return
    if not any(e in low for e in eligible):
        findings.append(Finding("unpinned_hook", f"no hook names an eligible cell ({', '.join(sorted(eligible))})", loc))
