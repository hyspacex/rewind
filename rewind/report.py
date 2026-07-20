"""Self-contained HTML report rendering."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _file_list(files: list[str], empty: str = "None") -> str:
    if not files:
        return f'<p class="muted">{_e(empty)}</p>'
    return "<ul class=\"files\">" + "".join(f"<li>{_e(path)}</li>" for path in files) + "</ul>"


def _signal_card(signal: dict[str, Any]) -> str:
    status = str(signal["status"]).lower()
    return (
        f'<article class="signal {status}">'
        f'<div class="signal-top"><h3>{_e(signal["label"])}</h3>'
        f'<span class="badge {status}">{_e(signal["status"])}</span></div>'
        f'<p>{_e(signal["summary"])}</p></article>'
    )


def render_report(receipt: dict[str, Any], output: Path) -> Path:
    recovery = receipt.get("recommended_recovery")
    recovery_command = (
        f"rewind recover {recovery['checkpoint_id']} --branch rewind/recover-{receipt['task_id']}"
        if recovery
        else "No recovery branch is available until a check passes."
    )
    timeline = "".join(
        (
            '<li class="timeline-item">'
            f'<span class="seq">{event["sequence"]:02d}</span>'
            '<div>'
            f'<strong>{_e(event["type"].replace("_", " "))}</strong>'
            f'<p>{_e(event["recorded_at"])} · {_e(event["actor"])}</p>'
            f'<code>{_e(event["content_id"][:16])}</code>'
            "</div></li>"
        )
        for event in receipt["timeline"]
    )
    check_rows = "".join(
        (
            "<tr>"
            f'<td><span class="badge {"pass" if check["passed"] else "fail"}">'
            f'{"PASS" if check["passed"] else "FAIL"}</span></td>'
            f"<td><code>{_e(' '.join(check['argv']))}</code></td>"
            f"<td><code>{_e(check['checkpoint_id'])}</code></td>"
            f"<td>{_e(check['duration_ms'])} ms</td>"
            f"<td><code>{_e(check['evidence_sha256'][:14])}</code></td>"
            "</tr>"
        )
        for check in receipt["checks"]
    ) or '<tr><td colspan="5" class="muted">No recorded checks.</td></tr>'
    audit_issues = _file_list(
        [
            f"{issue.get('level', 'L1')} {issue['code']}: {issue['message']}"
            for issue in receipt["audit"]["issues"]
        ],
        "No integrity issues detected.",
    )
    tests = (
        f"{receipt['test_count']} tests passed"
        if receipt.get("test_count") is not None
        else f"{receipt['passing_check_count']} passing checks"
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rewind task receipt · {_e(receipt['task_id'])}</title>
<style>
:root {{ color-scheme: dark; --bg:#0d0f11; --panel:#15181b; --panel2:#1b1f23; --line:#30363c;
--text:#f0f2f4; --muted:#9aa3ab; --green:#62c58b; --amber:#e5ae55; --red:#ee6b65; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ width:min(1180px,calc(100% - 48px)); margin:0 auto; padding:42px 0 72px; }}
header {{ display:grid; grid-template-columns:1fr auto; gap:28px; align-items:end; padding-bottom:30px; border-bottom:1px solid var(--line); }}
.brand {{ color:var(--muted); letter-spacing:.16em; font:700 13px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace; }}
h1 {{ font-size:clamp(30px,4vw,55px); line-height:1.05; margin:14px 0 12px; max-width:880px; }}
.meta,.muted {{ color:var(--muted); }}
.status {{ border:1px solid var(--line); background:var(--panel); border-left:5px solid var(--green); padding:18px 22px; min-width:245px; }}
.status.warn {{ border-left-color:var(--amber); }} .status.fail {{ border-left-color:var(--red); }}
.status strong {{ display:block; font-size:20px; }} .status span {{ color:var(--muted); }}
.signals {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:24px 0; }}
.signal {{ min-height:150px; padding:19px; background:var(--panel); border:1px solid var(--line); border-top:3px solid var(--green); }}
.signal.warn {{ border-top-color:var(--amber); }} .signal.fail,.signal.none {{ border-top-color:var(--red); }}
.signal-top {{ display:flex; justify-content:space-between; gap:12px; align-items:center; }}
.signal h3 {{ margin:0; font-size:15px; }} .signal p {{ margin:22px 0 0; color:#c7cdd2; }}
.badge {{ padding:3px 8px; border:1px solid currentColor; border-radius:999px; font:700 11px ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--green); }}
.badge.warn {{ color:var(--amber); }} .badge.fail,.badge.none {{ color:var(--red); }}
.section {{ margin-top:18px; padding:26px; background:var(--panel); border:1px solid var(--line); }}
.section h2 {{ margin:0 0 20px; font-size:22px; }} .section h3 {{ font-size:15px; color:#c6cdd3; }}
.stats {{ display:flex; flex-wrap:wrap; gap:12px 28px; color:var(--muted); }}
.stats strong {{ color:var(--text); }}
.file-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:24px; }}
.files {{ list-style:none; margin:0; padding:0; }} .files li {{ padding:7px 0; border-bottom:1px solid #262b30; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; overflow-wrap:anywhere; }}
.recovery {{ border-left:5px solid var(--green); }} code,.command {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
.command {{ display:block; background:#0b0d0f; border:1px solid var(--line); padding:15px; overflow-wrap:anywhere; color:#dfe5e9; }}
table {{ width:100%; border-collapse:collapse; }} th,td {{ text-align:left; padding:11px; border-bottom:1px solid var(--line); font-size:14px; }}
th {{ color:var(--muted); font-weight:600; }} .timeline {{ list-style:none; padding:0; margin:0; }}
.timeline-item {{ display:grid; grid-template-columns:42px 1fr; gap:14px; padding:13px 0; border-bottom:1px solid var(--line); }}
.timeline-item p {{ margin:3px 0; color:var(--muted); font-size:13px; }} .timeline-item code {{ color:#aeb7bf; font-size:12px; }}
.seq {{ color:var(--muted); font:700 13px ui-monospace,SFMono-Regular,Menlo,monospace; }}
details summary {{ cursor:pointer; font-weight:700; }} details[open] summary {{ margin-bottom:20px; }}
footer {{ margin-top:30px; color:var(--muted); font-size:13px; }}
@media (max-width:900px) {{ header {{ grid-template-columns:1fr; }} .signals,.file-grid {{ grid-template-columns:1fr 1fr; }} }}
@media (max-width:600px) {{ main {{ width:min(100% - 28px,1180px); }} .signals,.file-grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body><main>
<header>
<div><div class="brand">REWIND / TASK RECEIPT</div><h1>{_e(receipt['intent'])}</h1>
<div class="meta">{_e(receipt['task_id'])} · {_e(receipt['elapsed_display'])} · final tree <code>{_e(receipt['final_checkpoint']['tree'][:12])}</code></div></div>
<div class="status {_e(receipt['outcome_key'])}"><strong>{_e(receipt['outcome'])}</strong><span>{_e(receipt['review_item_count'])} items to review</span></div>
</header>
<section class="signals">{''.join(_signal_card(signal) for signal in receipt['signals'])}</section>
<section class="section"><div class="stats"><span><strong>{len(receipt['changed_files'])}</strong> files changed</span>
<span><strong>{_e(tests)}</strong></span><span>Safe state: <strong>{_e(recovery['checkpoint_id'] if recovery else 'none')}</strong></span>
<span>Final state: <strong>{_e(receipt['final_checkpoint']['checkpoint_id'])}</strong></span></div></section>
<section class="section"><h2>Changed files</h2><div class="file-grid">
<div><h3>Within declared scope</h3>{_file_list(receipt['within_scope'])}</div>
<div><h3>Outside declared scope</h3>{_file_list(receipt['outside_scope'])}</div>
<div><h3>Changed after last passing evidence</h3>{_file_list(receipt['changed_after_passing_evidence'])}</div>
</div></section>
<section class="section"><h2>Intent → action → evidence</h2>
<p class="muted">Declared intent: {_e(receipt['intent'])}</p>
<table><thead><tr><th>Result</th><th>Recorded command</th><th>Bound state</th><th>Duration</th><th>Evidence</th></tr></thead>
<tbody>{check_rows}</tbody></table></section>
<section class="section recovery"><h2>Safe recovery</h2>
<p>Rewind only creates this branch. It will not switch, reset, clean, stash, or modify your current work.</p>
<code class="command">{_e(recovery_command)}</code></section>
<section class="section"><h2>Timeline</h2><ol class="timeline">{timeline}</ol></section>
<details class="section"><summary>Signed audit details</summary>
<p>Signed log: <span class="badge {'pass' if receipt['audit']['log_valid'] else 'fail'}">{'VERIFIED' if receipt['audit']['log_valid'] else 'FAILED'}</span></p>
<p class="muted">{_e(receipt['audit']['threat_model'])}</p>
<p>Events verified: {_e(receipt['audit']['event_count'])} · Recorder key: <code>{_e(receipt['audit']['key_id'])}</code></p>
{audit_issues}
<p class="muted">One local recorder key signs actor assertions. This is not multi-party non-repudiation. A stolen key can rewrite history unless a content ID is externally checkpointed.</p>
</details>
<footer>Generated locally by Rewind. One self-contained HTML file; no network requests or external assets.</footer>
</main></body></html>"""
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output

