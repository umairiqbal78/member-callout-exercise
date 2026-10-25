#!/usr/bin/env python3
"""
scripts/export_chat.py — Export conversation transcript into readable Markdown and JSON formats.
"""

import json
import os
import re
import shutil
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = "/Users/admin/.gemini/antigravity-ide/brain/74fe6051-f878-474e-888f-f115b290b710/.system_generated/logs/transcript_full.jsonl"
OUTPUT_MD = os.path.join(REPO_ROOT, "CHAT_HISTORY.md")
OUTPUT_JSON = os.path.join(REPO_ROOT, "chat_history.json")
OUTPUT_RAW = os.path.join(REPO_ROOT, "chat_transcript_full.jsonl")

# Copy raw transcript
if os.path.exists(LOG_FILE):
    shutil.copyfile(LOG_FILE, OUTPUT_RAW)

turns = []
current_turn = None

with open(LOG_FILE, "r", encoding="utf-8") as f:
    for line in f:
        item = json.loads(line)
        t = item.get("type")
        s = item.get("source")
        content = item.get("content", "")
        step = item.get("step_index", 0)

        if t == "USER_INPUT":
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "turn_index": len(turns) + 1,
                "user_raw": content,
                "user_request": "",
                "user_actions": [],
                "assistant_responses": [],
            }
            m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
            if m:
                current_turn["user_request"] = m.group(1).strip()
            else:
                current_turn["user_request"] = content.strip()

            actions = re.findall(
                r"The USER performed the following action:\s*(.*?)(?=(?:The USER performed the following action:|<USER_REQUEST>|$))",
                content,
                re.DOTALL,
            )
            current_turn["user_actions"] = [a.strip() for a in actions if a.strip()]
        elif current_turn is not None:
            if t == "PLANNER_RESPONSE" and content:
                current_turn["assistant_responses"].append({
                    "step": step,
                    "content": content,
                })

if current_turn:
    turns.append(current_turn)

turn_titles = [
    "Session 1: Initial Specification — Foundation and Rule 1",
    "Session 1: Continuation & Completion",
    "DESIGN.md: Announcements Table Additions",
    "DESIGN.md: Concise Revisions to Given Tables",
    "Session 2: Send Path and Rule 2 (Specification)",
    "Session 2: Scope Alignment & Planning",
    "Session 2: Implementation, Scale Test (22,400 members) & Hammer Verification",
    "Session 2 Follow-Up: Funnel Rule, simulate_activity & Invariant Verification",
    "Chat History Export Request",
]

md_lines = []
md_lines.append("# CrewLink — Complete Conversation History")
md_lines.append("")
md_lines.append("> **Project**: `member-callout-exercise` (CrewLink)")
md_lines.append("> **Conversation ID**: `74fe6051-f878-474e-888f-f115b290b710`")
md_lines.append(f"> **Exported Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
md_lines.append(f"> **Total Conversation Turns**: {len(turns)}")
md_lines.append("")
md_lines.append("---")
md_lines.append("")
md_lines.append("## Table of Contents")
md_lines.append("")

for i, turn in enumerate(turns):
    title = turn_titles[i] if i < len(turn_titles) else f"Turn {i+1}"
    anchor = f"turn-{i+1}-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    md_lines.append(f"- [Turn {i+1}: {title}](#{anchor})")

md_lines.append("")
md_lines.append("---")
md_lines.append("")

json_export = []

for i, turn in enumerate(turns):
    title = turn_titles[i] if i < len(turn_titles) else f"Turn {i+1}"
    anchor = f"turn-{i+1}-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")

    md_lines.append(f'## <a id="{anchor}"></a>Turn {i+1}: {title}')
    md_lines.append("")

    # User actions
    if turn["user_actions"]:
        md_lines.append("### User Actions Before Request")
        md_lines.append("")
        for act in turn["user_actions"]:
            md_lines.append("```text")
            md_lines.append(act)
            md_lines.append("```")
            md_lines.append("")

    # User prompt
    md_lines.append("### User Request")
    md_lines.append("")
    md_lines.append("```markdown")
    md_lines.append(turn["user_request"])
    md_lines.append("```")
    md_lines.append("")

    # Assistant responses
    md_lines.append("### Assistant Response")
    md_lines.append("")

    resps = turn["assistant_responses"]
    if not resps:
        md_lines.append("*Current active turn — export generated at this point.*")
        md_lines.append("")
    else:
        # Pick the most substantial final response
        longest = max(resps, key=lambda r: len(r["content"]))
        if len(longest["content"]) > 500:
            main_resp = longest
            progress_resps = [r for r in resps if r != main_resp and len(r["content"]) > 30]
        else:
            main_resp = resps[-1]
            progress_resps = resps[:-1]

        if progress_resps and len(progress_resps) <= 6:
            md_lines.append("<details>")
            md_lines.append(f"<summary>Click to view intermediate status updates ({len(progress_resps)})</summary>")
            md_lines.append("")
            for pr in progress_resps:
                md_lines.append(f"- **Step {pr['step']}:** {pr['content']}")
            md_lines.append("")
            md_lines.append("</details>")
            md_lines.append("")

        md_lines.append(main_resp["content"])
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("")

    json_export.append({
        "turn": i + 1,
        "title": title,
        "user_request": turn["user_request"],
        "user_actions": turn["user_actions"],
        "assistant_responses": [r["content"] for r in turn["assistant_responses"]],
    })

with open(OUTPUT_MD, "w", encoding="utf-8") as f:
    f.write("\n".join(md_lines))

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(json_export, f, indent=2, ensure_ascii=False)

print(f"Export completed successfully:")
print(f"  Markdown:   {OUTPUT_MD} ({os.path.getsize(OUTPUT_MD)} bytes)")
print(f"  JSON:       {OUTPUT_JSON} ({os.path.getsize(OUTPUT_JSON)} bytes)")
print(f"  Raw JSONL:  {OUTPUT_RAW} ({os.path.getsize(OUTPUT_RAW)} bytes)")
