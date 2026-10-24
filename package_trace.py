"""
Automated Trace & Submission Packager
====================================
File: package_trace.py
Challenge: HackerRank Orchestrate (October 2026)

Instructor Requirements & Purpose:
----------------------------------
This script fulfills the instructions detailed in:
1. `agent_trace_instructions.md`: Copies structured Antigravity conversation traces
   from ~/.gemini/antigravity-cli/brain/<conversation-id> into agent-trace/antigravity/,
   validates that secrets/credentials are excluded, and packages `agent-trace.zip`.
2. `README.md` (§ Packaging code.zip): Bundles runnable code, evaluation reports,
   and README into `code.zip` without dataset or virtual environment artifacts.

Usage:
------
    python package_trace.py [--trace-only | --code-only | --all]
"""

import argparse
import os
import shutil
import zipfile
from pathlib import Path

# Current active conversation ID recorded for this Antigravity session
CURRENT_CONVERSATION_ID = "9c662ac2-c77e-4456-b829-949f5afa232d"


def package_agent_trace(repo_root: Path):
    """
    Stages Antigravity trace files and generates agent-trace.zip.
    """
    print("[Packager] Staging agent trace according to agent_trace_instructions.md...")
    user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    source_brain = user_profile / ".gemini" / "antigravity-cli" / "brain" / CURRENT_CONVERSATION_ID

    if not source_brain.exists():
        # Fallback search in .gemini/antigravity
        alt_source = user_profile / ".gemini" / "antigravity" / "brain" / CURRENT_CONVERSATION_ID
        if alt_source.exists():
            source_brain = alt_source

    staging_dir = repo_root / "agent-trace" / "antigravity" / CURRENT_CONVERSATION_ID
    staging_dir.parent.mkdir(parents=True, exist_ok=True)

    if source_brain.exists():
        print(f"  Copying trace from: {source_brain}")
        print(f"  Staging target    : {staging_dir}")
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        shutil.copytree(source_brain, staging_dir, ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "*credential*"))
    else:
        print(f"  Warning: Local brain directory not found at {source_brain}. Using staged placeholder.")
        staging_dir.mkdir(parents=True, exist_ok=True)

    # Package into agent-trace.zip
    trace_zip_path = repo_root / "agent-trace.zip"
    print(f"  Compressing staged traces into {trace_zip_path}...")
    agent_trace_root = repo_root / "agent-trace"
    with zipfile.ZipFile(trace_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(agent_trace_root):
            for file in files:
                abs_f = Path(root) / file
                rel_f = abs_f.relative_to(repo_root)
                zf.write(abs_f, rel_f)

    print(f"[Packager] Successfully created: {trace_zip_path} ({trace_zip_path.stat().st_size / 1024:.1f} KB)")


def package_code_zip(repo_root: Path):
    """
    Packages code.zip according to README.md lines 129-147:
    Includes: README.md, code/ (with evaluation reports).
    Excludes: dataset/, venv/, .git/, __pycache__/, nested archives.
    """
    print("[Packager] Packaging code.zip according to README.md specifications...")
    code_zip_path = repo_root / "code.zip"

    # Files and folders to include
    include_paths = ["README.md", "code"]

    with zipfile.ZipFile(code_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Write top-level README.md
        readme_path = repo_root / "README.md"
        if readme_path.exists():
            zf.write(readme_path, "README.md")

        # Write code directory recursively
        code_dir = repo_root / "code"
        for root, dirs, files in os.walk(code_dir):
            for file in files:
                if file.endswith((".pyc", ".DS_Store")) or "__pycache__" in root:
                    continue
                abs_path = Path(root) / file
                rel_path = abs_path.relative_to(repo_root)
                zf.write(abs_path, rel_path)

    print(f"[Packager] Successfully created: {code_zip_path} ({code_zip_path.stat().st_size / 1024:.1f} KB)")


def main():
    parser = argparse.ArgumentParser(description="Package trace and code submissions")
    parser.add_argument("--trace-only", action="store_true", help="Package agent-trace.zip only")
    parser.add_argument("--code-only", action="store_true", help="Package code.zip only")
    parser.add_argument("--all", action="store_true", default=True, help="Package both deliverables")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent

    if args.trace_only:
        package_agent_trace(repo_root)
    elif args.code_only:
        package_code_zip(repo_root)
    else:
        package_agent_trace(repo_root)
        package_code_zip(repo_root)


if __name__ == "__main__":
    main()
