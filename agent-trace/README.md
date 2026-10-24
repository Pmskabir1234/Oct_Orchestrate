# Agent Trace Packaging Documentation
=====================================
File: agent-trace/README.md
Challenge: HackerRank Orchestrate (October 2026)

## Overview & Instructor Requirements
As detailed in `agent_trace_instructions.md`, participants must submit the complete agent trace for all sessions as `agent-trace.zip`.

For Google Antigravity (the harness used for this challenge):
- Transcripts are recorded at:
  `~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl`
- Associated conversation artifacts are stored in:
  `~/.gemini/antigravity-cli/brain/<conversation-id>/`
- The staged trace directory structure:
  ```text
  agent-trace/
  └── antigravity/
      └── <conversation-id>/
          ├── .system_generated/
          │   └── logs/
          │       ├── transcript.jsonl
          │       └── transcript_full.jsonl
          └── (artifacts, scratch scripts)
  ```

## How to Package the Trace
You can run the provided automated packager script:
```powershell
python package_trace.py
```
This script will:
1. Identify the active conversation directory under `%USERPROFILE%\.gemini\antigravity-cli\brain/`.
2. Copy the conversation folder into `agent-trace/antigravity/<conversation-id>/`.
3. Verify that no credentials, tokens, or private secrets are included.
4. Compress the folder into `agent-trace.zip` at the repository root ready for submission.

## Safety & Redaction Checklist
- [x] No API keys, credentials, or `.env` files included.
- [x] Structured logs (`transcript.jsonl`) preserved in original JSONL format.
- [x] Unrelated directories outside this challenge excluded.
