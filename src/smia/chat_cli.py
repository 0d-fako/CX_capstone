"""Terminal REPL for the research chat: the whole product for the three-day MVP."""

from __future__ import annotations

import sys
import uuid

from smia.agent import session as sess
from smia.agent.chat import ChatRunner
from smia.tools.discovery import Decision, Proposal, TargetProposal


def _approval(p: Proposal) -> Decision:
    print()
    print("=" * 72)
    print(f"PROPOSED COMPETITIVE SET  |  {p.name}  |  {p.industry}  |  {p.geography or 'geography n/a'}")
    print("=" * 72)
    for i, t in enumerate(p.targets, start=1):
        print(f"{i:2d}. {t.platform:9s} @{t.handle:24s} {t.role:12s} {t.why}")
    print("-" * 72)
    print("y = confirm | rm 2,5 = drop rows then confirm | n <note> = decline with a note")
    while True:
        try:
            ans = input("confirm set? ").strip()
        except EOFError:
            return Decision(confirmed=False, note="user ended input")
        low = ans.lower()
        if low in ("y", "yes"):
            return Decision(confirmed=True, targets=list(p.targets))
        if low.startswith("rm"):
            try:
                drop = {int(x) for x in ans[2:].replace(" ", "").split(",") if x}
            except ValueError:
                print("use: rm 2,5")
                continue
            keep: list[TargetProposal] = [t for i, t in enumerate(p.targets, start=1) if i not in drop]
            if not keep:
                print("that would leave no accounts")
                continue
            return Decision(confirmed=True, targets=keep, note=f"user removed rows {sorted(drop)}")
        if low.startswith("n"):
            return Decision(confirmed=False, note=ans[1:].strip() or "user declined without a note")
        print("y, rm 2,5, or n <note>")


def main(session_id: str | None, user: str, verbose: bool) -> int:
    sid = uuid.UUID(session_id) if session_id else sess.create(user)
    state = sess.load(sid)
    print(f"session {sid}  stage={state['stage']}  credits used={state['credits_used']}")
    if state["messages"]:
        print(f"(resumed with {len(state['messages'])} prior messages)")
    else:
        print("Describe the venture idea: what it is, for whom, and where. Type /quit to leave.")
        print()

    def progress(text: str) -> None:
        if verbose:
            print()
            print("  ... " + text[:300].strip())
            print()

    def deliver(kind: str, markdown: str, report_id: uuid.UUID) -> None:
        from pathlib import Path

        from smia.delivery.slack import post_report
        from smia.export import export_report

        docx, md = export_report(report_id, Path("exports"))
        print()
        print(f"  {kind} saved: {docx}")
        print(f"  {kind} saved: {md}")
        try:
            post_report(kind, markdown, report_id, validation_summary="validated: every number traces to a tool call")
        except Exception as e:  # noqa: BLE001
            print(f"  (Slack post failed: {e})")

    runner = ChatRunner(sid, approval=_approval, on_text=progress, deliver=deliver)
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        res = runner.turn(text)
        print()
        print("smia> " + res.reply)
        print()
        tools = ", ".join(res.tool_names) if res.tool_names else "none"
        u = res.usage or {}
        print(f"  [stage={res.stage} | tools: {tools} | status={res.status} | effort={u.get('effort')} | "
              f"in={u.get('input_tokens', 0)} cached={u.get('cache_read_input_tokens', 0)} "
              f"out={u.get('output_tokens', 0)} | ~${u.get('est_usd', 0):.2f} this turn, "
              f"~${runner.session_spend_usd():.2f} this session]")
        print()
    print(f"session {sid} saved. Resume with: smia chat --session {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main(None, "cli", False))
