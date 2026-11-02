# -*- coding: utf-8 -*-
"""
备用推送：当 github.com:443 连不上、但 api.github.com 可用时，用 GitHub 的
Git Data API 把本地提交送上去。

原理：Git 是内容寻址的，只要 blob / tree / commit 的元数据（作者、时间、message）
完全一致，远端生成的 commit sha 就与本地一模一样，不会造成分叉。

用法：
    python tools/push_via_api.py <token> [commit...]

    不写 commit 时默认推送「本地领先远端的所有提交」。
    token 也可以放在环境变量 GITHUB_TOKEN 里，避免出现在命令行历史。

依赖：只需要本地有 git 命令，无第三方库。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_OWNER_REPO = "Raya-L/huyi-engine"
DEFAULT_BRANCH = "main"


def _git(*args: str, binary: bool = False):
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        capture_output=True, check=True).stdout
    return out if binary else out.decode("utf-8")


class GitHub:
    def __init__(self, token: str, owner_repo: str):
        self.token = token
        self.base = f"https://api.github.com/repos/{owner_repo}"

    def __call__(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"token {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "huyi-push-via-api")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"GitHub API {method} {path} 失败：{e.code} {e.read().decode()[:300]}")
        return json.loads(body) if body.strip() else {}


def _person(raw: str) -> dict:
    """'Name <mail> 1762000000 +0800' -> GitHub API 需要的 author 结构"""
    name_email, _, tail = raw.rpartition("> ")
    name, email = name_email.rsplit(" <", 1)
    ts, tz = tail.split()
    sign = -1 if tz.startswith("-") else 1
    offset = sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
    local = dt.datetime.fromtimestamp(int(ts), dt.timezone(dt.timedelta(seconds=offset)))
    return {"name": name, "email": email,
            "date": local.strftime("%Y-%m-%dT%H:%M:%S") + f"{tz[0]}{tz[1:3]}:{tz[3:5]}"}


def _commit_meta(sha: str) -> dict:
    head, _, message = _git("cat-file", "-p", sha).partition("\n\n")
    meta: dict = {"message": message, "parents": []}
    for line in head.splitlines():
        if line.startswith("tree "):
            meta["tree"] = line.split()[1]
        elif line.startswith("parent "):
            meta["parents"].append(line.split()[1])
        elif line.startswith("author "):
            meta["author"] = _person(line[7:])
        elif line.startswith("committer "):
            meta["committer"] = _person(line[10:])
    return meta


def _blob_in(rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "-c", "core.quotepath=false",
                        "rev-parse", f"{rev}:{path}"], capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def _changed_paths(sha: str) -> list[str]:
    out = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", sha)
    return [p for p in out.split("\0") if p]


def push(token: str, commits: list[str], owner_repo: str, branch: str) -> None:
    gh = GitHub(token, owner_repo)

    remote = gh("GET", f"/git/ref/heads/{branch}")["object"]["sha"]
    base_tree = gh("GET", f"/git/commits/{remote}")["tree"]["sha"]
    print(f"远端 {branch} = {remote[:7]}")

    if not commits:
        out = _git("rev-list", "--reverse", f"{remote}..HEAD")
        commits = [c for c in out.split() if c]
        if not commits:
            print("本地没有领先远端的提交，无需推送")
            return

    for rev in commits:
        sha = _git("rev-parse", rev).strip()
        meta = _commit_meta(sha)
        if meta["parents"][:1] != [remote]:
            raise SystemExit(f"{rev[:7]} 的父提交不是远端 HEAD（{meta['parents'][0][:7]}），"
                             "请先推送更早的提交或先同步远端")

        entries = []
        for path in _changed_paths(sha):
            if _blob_in(sha, path) is None:          # 删除
                entries.append({"path": path, "mode": "100644",
                                "type": "blob", "sha": None})
                continue
            content = _git("show", f"{sha}:{path}", binary=True)
            blob = gh("POST", "/git/blobs", {
                "content": base64.b64encode(content).decode(), "encoding": "base64"})
            mode = "100755" if _git("ls-tree", sha, path).startswith("100755") else "100644"
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})

        tree = gh("POST", "/git/trees", {"base_tree": base_tree, "tree": entries})
        base_tree = tree["sha"]
        if tree["sha"] != meta["tree"]:
            print(f"  ⚠ {sha[:7]}：tree {tree['sha'][:7]} 与本地 {meta['tree'][:7]} 不同，"
                  "commit sha 将不一致（提交仍会成功）")

        new = gh("POST", "/git/commits", {
            "message": meta["message"], "tree": tree["sha"], "parents": [remote],
            "author": meta["author"], "committer": meta["committer"]})
        remote = new["sha"]
        mark = "✓" if new["sha"] == sha else "→"
        print(f"  {mark} {sha[:7]}  ({len(entries)} 个文件变动)")

    gh("PATCH", f"/git/refs/heads/{branch}", {"sha": remote, "force": False})
    local_head = _git("rev-parse", "HEAD").strip()
    print(f"✓ {branch} 已更新到 {remote[:7]}")

    # 顺手把本地 origin/<branch> 对齐，避免 git status 一直显示分叉
    subprocess.run(["git", "update-ref", f"refs/remotes/origin/{branch}", remote],
                   capture_output=True)
    if remote != local_head:
        print(f"注意：远端 {remote[:7]} 与本地 HEAD {local_head[:7]} 不同，"
              "可在能连 github.com 的机器上 git fetch 后检查")


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    token = next((a for a in args if a.startswith(("ghp_", "github_pat_"))), None) \
        or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(__doc__)
        return 2
    commits = [a for a in args if not a.startswith(("ghp_", "github_pat_"))]
    push(token, commits, DEFAULT_OWNER_REPO, DEFAULT_BRANCH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
