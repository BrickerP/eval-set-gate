#!/usr/bin/env python3
"""eval-set-gate: fail a PR that changes prompts/model config without bumping the versioned eval set.

Commands:
  check --base REF [--head REF]   gate a diff (used by the GitHub Action)
  lock                            (re)write the lockfile from current eval files
  verify                          check the lockfile hash matches the eval files
Stdlib only.
"""
import argparse, fnmatch, hashlib, json, os, subprocess, sys

DEFAULT_WATCH = "prompts/**,**/*.prompt,**/*.prompt.md,**/model*.yaml,**/model*.yml,**/model*.json,**/llm*.yaml"
DEFAULT_EVALS = "evals/**"
DEFAULT_LOCK = "evals.lock.json"


def split_globs(s):
    return [g.strip() for g in (s or "").replace("\n", ",").split(",") if g.strip()]


def match(path, globs):
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
        if g.startswith("**/") and fnmatch.fnmatch(path, g[3:]):
            return True
        if g.endswith("/**") and (path == g[:-3] or path.startswith(g[:-2])):
            return True
    return False


def git(*args, cwd="."):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def changed_files(base, head="HEAD", cwd="."):
    out = git("diff", "--name-only", f"{base}...{head}", cwd=cwd)
    return [l for l in out.splitlines() if l]


def list_files(root="."):
    try:
        return [l for l in git("ls-files", cwd=root).splitlines() if l]
    except Exception:
        res = []
        for d, _, fs in os.walk(root):
            if ".git" in d.split(os.sep):
                continue
            for f in fs:
                res.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
        return res


def eval_hash(eval_globs, lock, root="."):
    h = hashlib.sha256()
    files = sorted(f for f in list_files(root) if match(f, eval_globs) and f != lock)
    for f in files:
        h.update(f.encode() + b"\0")
        with open(os.path.join(root, f), "rb") as fh:
            h.update(hashlib.sha256(fh.read()).hexdigest().encode() + b"\n")
    return h.hexdigest(), files


def read_lock(lock, root="."):
    p = os.path.join(root, lock)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def write_lock(eval_globs, lock, root=".", bump=True):
    digest, files = eval_hash(eval_globs, lock, root)
    old = read_lock(lock, root) or {}
    ver = int(old.get("version", 0)) + (1 if bump and old.get("sha256") != digest else 0)
    data = {"version": max(ver, 1), "sha256": digest, "files": len(files)}
    with open(os.path.join(root, lock), "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return data


def gate(changed, watch, evals, lock, lock_data, current_hash):
    """Pure decision function. Returns (ok, reasons, details)."""
    prompt_changes = [f for f in changed if match(f, watch)]
    eval_changes = [f for f in changed if match(f, evals) and f != lock]
    lock_changed = lock in changed
    reasons = []
    if lock_data is not None and lock_data.get("sha256") != current_hash:
        reasons.append(f"{lock} is stale: eval files changed but lock not regenerated (run `eval-set-gate lock`).")
    if prompt_changes and not lock_changed:
        reasons.append(
            f"{len(prompt_changes)} prompt/model file(s) changed but the eval set version in {lock} was not bumped.")
    if prompt_changes and lock_data is None:
        reasons.append(f"No {lock} found. Run `eval-set-gate lock` and commit it.")
    details = {"prompt_changes": prompt_changes, "eval_changes": eval_changes, "lock_changed": lock_changed}
    return (not reasons), reasons, details


def summary_md(ok, reasons, d, lock_data):
    lines = ["## eval-set-gate: " + ("✅ pass" if ok else "❌ fail"), ""]
    v = lock_data.get("version") if lock_data else "none"
    lines.append(f"Eval set version: **{v}** · lockfile changed: **{d['lock_changed']}**")
    lines.append("")
    for title, key in (("Prompt / model changes", "prompt_changes"), ("Eval set changes", "eval_changes")):
        lines.append(f"**{title}** ({len(d[key])})")
        lines += [f"- `{f}`" for f in d[key][:30]] or ["- none"]
        lines.append("")
    if reasons:
        lines.append("**Why it failed**")
        lines += [f"- {r}" for r in reasons]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eval-set-gate")
    ap.add_argument("cmd", choices=["check", "lock", "verify"])
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--watch", default=os.environ.get("INPUT_WATCH") or DEFAULT_WATCH)
    ap.add_argument("--evals", default=os.environ.get("INPUT_EVALS") or DEFAULT_EVALS)
    ap.add_argument("--lockfile", default=os.environ.get("INPUT_LOCKFILE") or DEFAULT_LOCK)
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    watch, evals = split_globs(a.watch), split_globs(a.evals)
    if a.cmd == "lock":
        print(json.dumps(write_lock(evals, a.lockfile, a.root)))
        return 0
    digest, _ = eval_hash(evals, a.lockfile, a.root)
    lock_data = read_lock(a.lockfile, a.root)
    if a.cmd == "verify":
        ok = lock_data is not None and lock_data.get("sha256") == digest
        print("ok" if ok else f"stale or missing {a.lockfile}")
        return 0 if ok else 1
    if not a.base:
        ap.error("check needs --base")
    changed = changed_files(a.base, a.head, a.root)
    ok, reasons, d = gate(changed, watch, evals, a.lockfile, lock_data, digest)
    md = summary_md(ok, reasons, d, lock_data)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(md)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
