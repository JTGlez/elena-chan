#!/usr/bin/env python3
"""Hermes pre_tool_call hook: enforces a project's rules on agent tool calls.

Applies only to projects with a .hermes-delegate.json or .hermes-delegate.local.json. Reads the hook payload on stdin. Prints a
block directive, or nothing to allow. A payload or config it cannot read exits 2, which Hermes
treats as a block. It stops honest mistakes; it is not a sandbox.
"""
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

CONFIG_NAMES = (".hermes-delegate.json", ".hermes-delegate.local.json")
MAX_SESSION_DEPTH = 8
HINT = "Report it to the supervisor instead of working around it."

WRAPPERS = {"sudo", "env", "command", "time", "nohup", "exec", "nice", "timeout"}
SHELLS = {"bash", "sh", "zsh", "dash"}
READERS = {"cat", "less", "more", "head", "tail", "cp", "mv", "grep", "rg", "sed", "awk", "source", "."}
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree"}
BUILTIN_CODE = (
    (re.compile(r"['\"]git['\"]\s*,\s*['\"]push['\"]|\bgit\s+push\b"), "git push"),
    (re.compile(r"--no-verify"), "--no-verify"),
)
ABSOLUTE_PATH = re.compile(r"(?<![\w.~-])/[^\s'\"<>|;&()]+")


def split_segments(command):
    """Split a shell line into simple commands, keeping quoted text intact."""
    segments, current, quote, escaped = [], [], None, False
    for char in command:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            current.append(char)
            escaped = True
        elif quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            current.append(char)
            quote = char
        elif char in ";&|\n()":
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def words_of(segment):
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words.pop(0)
    while words and Path(words[0]).name in WRAPPERS:
        wrapper = Path(words.pop(0)).name
        while words and (words[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0])
                         or (wrapper == "timeout" and re.fullmatch(r"[0-9.]+[smhd]?", words[0]))):
            words.pop(0)
    return words


def git_reason(words):
    args = words[1:]
    while args and args[0].startswith("-"):
        option = args.pop(0)
        if option in GIT_VALUE_OPTIONS and args:
            args.pop(0)
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub == "push":
        return "git push: publishing is the supervisor's decision"
    if "--no-verify" in rest:
        return "--no-verify: commit hooks must run"
    if sub == "reset" and "--hard" in rest:
        return "git reset --hard discards work"
    if sub == "clean" and any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", arg) for arg in rest):
        return "git clean -f deletes untracked files"
    if sub == "add" and any(arg in ("-A", "--all", ".", "*") for arg in rest):
        return "git add -A: name the files you mean to commit"
    return None


class Guard:
    def __init__(self, config):
        rules = config.get("guard") or {}
        self.commands = [(re.compile(rule["pattern"]), rule["reason"]) for rule in rules.get("blocked_commands", [])]
        self.code = [(re.compile(rule["pattern"]), rule["reason"]) for rule in rules.get("blocked_code", [])]
        self.secrets = [re.compile(pattern) for pattern in rules.get("secret_paths", [])]
        self.protected = [re.compile(pattern) for pattern in rules.get("protected_paths", [])]

    def is_secret(self, text):
        return any(pattern.search(text) for pattern in self.secrets)

    def segment_reason(self, segment):
        words = words_of(segment)
        if not words:
            return None
        head = Path(words[0]).name
        if head in SHELLS and "-c" in words:
            inner = words[words.index("-c") + 1:]
            return self.command_reason(inner[0]) if inner else None
        if head == "eval":
            return self.command_reason(" ".join(words[1:]))
        if head == "git":
            reason = git_reason(words)
            if reason:
                return reason
        normalized = " ".join([head, *words[1:]])
        for pattern, reason in self.commands:
            if pattern.search(normalized):
                return reason
        if head in READERS and any(self.is_secret(word) for word in words[1:]):
            return "this file holds secrets"
        return None

    def command_reason(self, command):
        for segment in split_segments(command):
            reason = self.segment_reason(segment)
            if reason:
                return reason
        return None

    def code_reason(self, code):
        for pattern, label in BUILTIN_CODE:
            if pattern.search(code):
                return f"{label} is not allowed from code"
        for pattern, reason in self.code:
            if pattern.search(code):
                return reason
        return None

    def decide(self, tool, tool_input):
        tool_input = tool_input or {}
        if tool == "terminal":
            return self.command_reason(str(tool_input.get("command", "")))
        if tool == "execute_code":
            return self.code_reason(str(tool_input.get("code", "")))
        path = str(tool_input.get("path", ""))
        if tool in ("read_file", "write_file", "patch") and self.is_secret(path):
            return "this file holds secrets"
        if tool in ("write_file", "patch"):
            if Path(path).name in CONFIG_NAMES:
                return f"{Path(path).name} is the supervisor's"
            if any(pattern.search(path) for pattern in self.protected):
                return f"{path} is protected"
        return None


def session_cwd(session_id):
    """The directory Hermes recorded for a session, or for the nearest ancestor that has one."""
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    store = home / "state.db"
    if not session_id or not store.is_file():
        return None
    db = sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        for _ in range(MAX_SESSION_DEPTH):
            row = db.execute("select cwd, parent_session_id from sessions where id=?", (session_id,)).fetchone()
            if row is None:
                return None
            if row[0]:
                return row[0]
            session_id = row[1]
            if not session_id:
                return None
    finally:
        db.close()
    return None


def find_config(payload):
    """The first project config above the working directory or any absolute path the call names."""
    cwd = Path(str(payload.get("cwd") or "/"))
    tool_input = payload.get("tool_input") or {}
    candidates = [cwd]
    # A gateway-run session's payload names the gateway's directory; the store holds the session's.
    stored = session_cwd(payload.get("session_id"))
    if stored:
        candidates.insert(0, Path(stored))
    if tool_input.get("path"):
        candidates.append(candidates[0] / str(tool_input["path"]))
    candidates += [Path(match) for match in ABSOLUTE_PATH.findall(json.dumps(tool_input, ensure_ascii=False))]
    for candidate in candidates:
        for directory in (candidate, *candidate.parents):
            paths = [directory / name for name in CONFIG_NAMES if (directory / name).is_file()]
            if paths:
                return paths
    return None


def merged_rules(paths):
    rules = {}
    for path in paths:
        for key, values in (json.loads(path.read_text()).get("guard") or {}).items():
            rules.setdefault(key, []).extend(values)
    return {"guard": rules}


def main():
    try:
        payload = json.load(sys.stdin)
        tool = payload["tool_name"]
        paths = find_config(payload)
        if paths is None:
            return 0
        guard = Guard(merged_rules(paths))
    except (ValueError, KeyError, TypeError, OSError, re.error, sqlite3.Error) as error:
        print(f"guard_hook: cannot evaluate this call: {error}", file=sys.stderr)
        return 2
    reason = guard.decide(tool, payload.get("tool_input"))
    if reason:
        print(json.dumps({"action": "block", "message": f"Blocked by project guard: {reason}. {HINT}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
