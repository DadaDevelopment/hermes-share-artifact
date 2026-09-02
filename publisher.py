"""
Git-backed publisher for the share-artifact plugin.

Publishes a file or directory as a static site under a GitHub Pages branch
(default: DadaDevelopment/hermes-artifacts, branch gh-pages, path /a/<id>/)
and returns a public URL. No server, no S3, no DB - the git repo IS the
storage and GitHub Pages IS the CDN.

Concurrency: multiple sessions/machines can publish at once. Push conflicts
are handled by fetch + reset + re-apply + retry (the new artifact directory
is untouched by concurrent pushes to *other* artifact ids, so retrying is
always safe and idempotent).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_MAX_PUSH_RETRIES = 5


class PublishError(RuntimeError):
    pass


@dataclass
class PublishResult:
    id: str
    url: str
    title: str
    entrypoint: str
    created_at: str


def _run(args: list[str], cwd: Optional[Path] = None, timeout: int = 60) -> str:
    proc = subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise PublishError(
            f"command failed: {' '.join(args)}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.stdout


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _slugify(text: str, fallback: str) -> str:
    text = (text or "").strip().lower().replace(" ", "-")
    text = _SLUG_RE.sub("", text)
    text = text.strip("-")
    return text or fallback


def _default_base_url(repo: str) -> str:
    # "owner/name" -> https://owner.github.io/name/
    if "/" not in repo:
        raise PublishError(f"repo must be 'owner/name', got {repo!r}")
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}/"


def _repo_clone_url(repo: str) -> str:
    if repo.startswith("http://") or repo.startswith("https://") or repo.startswith("git@"):
        return repo
    return f"https://github.com/{repo}.git"


_MARKDOWN_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown.min.css">
<style>
  body {{ margin:0; background:#0d1117; }}
  .markdown-body {{ box-sizing:border-box; max-width:900px; margin:0 auto; padding:2.5rem 1.5rem; }}
</style>
</head>
<body>
<article class="markdown-body markdown-body" id="content"></article>
<script id="src" type="text/markdown">{escaped}</script>
<script>
  const src = document.getElementById('src').textContent;
  document.getElementById('content').innerHTML = marked.parse(src);
  document.body.classList.add('markdown-body');
</script>
</body></html>
"""


def render_markdown_html(markdown: str, title: str) -> str:
    escaped = markdown.replace("</script>", "<\\/script>")
    return _MARKDOWN_TEMPLATE.format(title=title or "Report", escaped=escaped)


def _ensure_repo(repo_dir: Path, repo: str, branch: str) -> None:
    if (repo_dir / ".git").exists():
        return
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    _run(["git", "clone", "--branch", branch, "--single-branch", _repo_clone_url(repo), str(repo_dir)], timeout=120)
    _run(["git", "config", "user.name", "hermes-share-artifact"], cwd=repo_dir)
    _run(["git", "config", "user.email", "hermes-share-artifact@local"], cwd=repo_dir)


def _sync_to_remote(repo_dir: Path, branch: str) -> None:
    _run(["git", "fetch", "origin", branch], cwd=repo_dir, timeout=60)
    _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=repo_dir)
    _run(["git", "clean", "-fd"], cwd=repo_dir)


def _copy_payload(dest: Path, path: Optional[Path], html: Optional[str], entrypoint_name: str) -> str:
    dest.mkdir(parents=True, exist_ok=True)
    if html is not None:
        (dest / entrypoint_name).write_text(html, encoding="utf-8")
        return entrypoint_name

    assert path is not None
    if path.is_dir():
        for child in path.iterdir():
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        if (dest / entrypoint_name).exists():
            return entrypoint_name
        if (dest / "index.html").exists():
            return "index.html"
        raise PublishError(
            f"directory has no '{entrypoint_name}' or 'index.html' at its root; "
            "pass entrypoint= to pick the file to serve"
        )

    # Single file.
    shutil.copy2(path, dest / path.name)
    if path.suffix.lower() in (".html", ".htm"):
        if path.name != "index.html":
            shutil.copy2(path, dest / "index.html")
        return "index.html"
    # Non-html single file: wrap in a minimal viewer.
    wrapper = _wrap_single_file(path.name)
    (dest / "index.html").write_text(wrapper, encoding="utf-8")
    return "index.html"


_MARKDOWN_FILE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown.min.css">
<style>
  body {{ margin:0; background:#0d1117; }}
  .markdown-body {{ box-sizing:border-box; max-width:900px; margin:0 auto; padding:2.5rem 1.5rem; }}
</style>
</head>
<body class="markdown-body">
<article id="content"></article>
<script>
  fetch("./{filename}").then(function (r) {{ return r.text(); }}).then(function (t) {{
    document.getElementById("content").innerHTML = marked.parse(t);
  }});
</script>
</body></html>
"""


def _wrap_single_file(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
        body = f'<img src="./{filename}" style="max-width:100%">'
    elif ext == ".md":
        return _MARKDOWN_FILE_TEMPLATE.format(title=filename, filename=filename)
    else:
        body = f'<iframe src="./{filename}" style="width:100%;height:100vh;border:0"></iframe>'
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{filename}</title></head><body style='margin:0'>{body}</body></html>"


def publish(
    *,
    repo_dir: Path,
    repo: str,
    branch: str,
    base_url: str,
    path: Optional[Path] = None,
    html: Optional[str] = None,
    title: str = "",
    entrypoint: str = "index.html",
) -> PublishResult:
    if path is None and html is None:
        raise PublishError("either path or html must be given")
    if path is not None and not path.exists():
        raise PublishError(f"path does not exist: {path}")

    artifact_id = _new_id()
    slug_title = _slugify(title, artifact_id)
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    for attempt in range(_MAX_PUSH_RETRIES):
        _ensure_repo(repo_dir, repo, branch)
        _sync_to_remote(repo_dir, branch)

        dest = repo_dir / "a" / artifact_id
        served_entrypoint = _copy_payload(dest, path, html, entrypoint)

        meta = {
            "id": artifact_id,
            "title": title or artifact_id,
            "entrypoint": served_entrypoint,
            "created_at": created_at,
        }
        (dest / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        _run(["git", "add", "-A"], cwd=repo_dir)
        commit_msg = f"publish {artifact_id}: {title or slug_title}"
        commit = subprocess.run(
            ["git", "commit", "-m", commit_msg], cwd=str(repo_dir), capture_output=True, text=True,
        )
        if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
            raise PublishError(f"git commit failed: {commit.stdout}\n{commit.stderr}")

        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"], cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        if push.returncode == 0:
            url = base_url.rstrip("/") + f"/a/{artifact_id}/"
            return PublishResult(
                id=artifact_id, url=url, title=title or artifact_id,
                entrypoint=served_entrypoint, created_at=created_at,
            )
        # Non-fast-forward or transient: resync and retry (new dir is safe to redo).
        time.sleep(0.5 * (attempt + 1))

    raise PublishError(f"could not push after {_MAX_PUSH_RETRIES} attempts (repo busy)")


def delete(*, repo_dir: Path, repo: str, branch: str, artifact_id: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{6,16}", artifact_id):
        raise PublishError(f"invalid artifact id: {artifact_id!r}")
    for attempt in range(_MAX_PUSH_RETRIES):
        _ensure_repo(repo_dir, repo, branch)
        _sync_to_remote(repo_dir, branch)
        dest = repo_dir / "a" / artifact_id
        if not dest.exists():
            return False
        _run(["git", "rm", "-r", "-q", f"a/{artifact_id}"], cwd=repo_dir)
        subprocess.run(
            ["git", "commit", "-m", f"delete {artifact_id}"], cwd=str(repo_dir), capture_output=True, text=True,
        )
        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"], cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        if push.returncode == 0:
            return True
        time.sleep(0.5 * (attempt + 1))
    raise PublishError(f"could not push delete after {_MAX_PUSH_RETRIES} attempts")
