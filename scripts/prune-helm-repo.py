#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    import yaml  # pip install pyyaml
except ImportError:
    print("Missing dependency: pyyaml. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

try:
    from packaging.version import Version, InvalidVersion  # pip install packaging
except ImportError:
    print("Missing dependency: packaging. Install with: pip install packaging", file=sys.stderr)
    sys.exit(2)


def load_protected(protect_file: Path) -> dict[str, set[str]]:
    """
    protect file format (whitespace separated):
      chart-name 1.2.3
      other-chart 0.9.0
    Lines starting with # are comments.
    """
    protected: dict[str, set[str]] = {}
    if not protect_file:
        return protected
    if not protect_file.exists():
        return protected

    for line in protect_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"Invalid protect line (expected: <chart> <version>): {line}")
        chart, ver = parts
        protected.setdefault(chart, set()).add(ver)
    return protected


def semver_key(ver_str: str):
    try:
        return Version(ver_str)
    except InvalidVersion:
        # Fallback: treat unknown versions as very old
        return Version("0.0.0")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune a Helm chart repo directory to keep only last N versions per chart.")
    ap.add_argument("--repo-dir", default=".", help="Path to helm repo directory (contains index.yaml and *.tgz).")
    ap.add_argument("--keep", type=int, default=3, help="How many latest versions to keep per chart.")
    ap.add_argument("--url", required=True, help="Base URL for helm repo index generation.")
    ap.add_argument("--protect-file", default=".helm-keep", help="File listing chart versions to always keep.")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be deleted, but don't delete or reindex.")
    args = ap.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    index_path = repo_dir / "index.yaml"
    if not index_path.exists():
        print(f"index.yaml not found at {index_path}", file=sys.stderr)
        return 2

    data = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    entries = data.get("entries", {})
    if not isinstance(entries, dict):
        print("index.yaml has unexpected structure: entries is not a map", file=sys.stderr)
        return 2

    protected = load_protected(repo_dir / args.protect_file)

    to_delete_files: set[Path] = set()

    for chart_name, versions in entries.items():
        if not isinstance(versions, list):
            continue

        # Sort versions descending by semver
        sorted_versions = sorted(
            versions,
            key=lambda e: semver_key(str(e.get("version", "0.0.0"))),
            reverse=True,
        )

        keep_versions = set()

        # keep latest N
        for e in sorted_versions[: args.keep]:
            v = str(e.get("version"))
            keep_versions.add(v)

        # plus protected
        for pv in protected.get(chart_name, set()):
            keep_versions.add(pv)

        # anything not in keep_versions -> delete its tgz files
        for e in sorted_versions:
            v = str(e.get("version"))
            if v in keep_versions:
                continue
            for u in e.get("urls", []) or []:
                fname = Path(str(u)).name
                if fname.endswith(".tgz"):
                    to_delete_files.add(repo_dir / fname)

    if not to_delete_files:
        print("Nothing to delete.")
    else:
        print("Will delete:")
        for f in sorted(to_delete_files):
            print(f"  - {f.name}")

    if args.dry_run:
        print("Dry-run: not deleting or reindexing.")
        return 0

    # Delete files
    deleted_any = False
    for f in sorted(to_delete_files):
        if f.exists():
            f.unlink()
            deleted_any = True
        else:
            print(f"Warning: file not found (already deleted?): {f.name}", file=sys.stderr)

    # Rebuild index.yaml from remaining tgz files
    # This recalculates digests and ensures index matches the filesystem.
    cmd = ["helm", "repo", "index", str(repo_dir), "--url", args.url]
    print(f"Running: {' '.join(cmd)}")
    subprocess.check_call(cmd)

    if deleted_any:
        print("Prune complete.")
    else:
        print("No files were deleted, but index.yaml was regenerated.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
