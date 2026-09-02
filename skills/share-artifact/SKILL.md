---
name: share-artifact
description: "Use when a result should be shown as a standalone web page (HTML report, dashboard, rendered doc) instead of pasted into chat - publish it and hand back a public link."
version: 0.1.0
author: DadaDevelopment
license: MIT
metadata:
  hermes:
    tags: [artifacts, sharing, html, reports, publishing]
---

# Share Artifact

When your output is meant to be *viewed by a human as a page* - an
interactive HTML report, a dashboard, a rendered Markdown writeup, a chart,
a small tool/demo - do not paste the raw HTML/Markdown into the chat. Publish
it and hand back a link, the way Claude Code's Artifacts feature works.

## When to use this

- The user asked for "an interactive report", "a dashboard", "a page I can
  share", or anything similarly presentation-shaped.
- You already generated a big HTML/Markdown blob and pasting it into the
  chat would be worse UX than a clickable link.
- The output needs to be shareable with someone who does NOT have access to
  this Hermes session (Telegram, Slack, email).

## When NOT to use this

- Small snippets, code diffs, short answers - just reply normally.
- Files the user explicitly wants to keep private / local only.
- Anything with secrets or credentials in it - this publishes PUBLICLY, no
  auth. Never publish credential dumps, internal-only data, or PII.

## Tools

- `publish_artifact(path, title?, entrypoint?)` - publish a local file or a
  whole directory (index.html + assets/). For a directory, `entrypoint`
  picks which file to serve (default `index.html`).
- `publish_html(html, title?)` - publish a raw HTML string you already have
  in memory.
- `publish_markdown(markdown, title?)` - publish Markdown, auto-rendered
  with GitHub styling.
- `delete_artifact(artifact_id)` - take a previously published artifact down.

All three publish tools return a public URL immediately (no polling, no
build step) plus an `artifact_id` for later deletion.

## Workflow

1. Build the artifact into a working directory (or just hold the HTML/MD in
   a variable) as you normally would.
2. Call `publish_artifact` / `publish_html` / `publish_markdown`.
3. Return the URL to the user with a one-line description. Do NOT also paste
   the full HTML/Markdown into the chat - the whole point is to avoid that.
4. If asked to update the report, just publish again (a new artifact_id/URL
   each time) - there is no in-place edit.

Example:

    User: "Сделай мне интерактивный HTML-дашборд по этим CSV"
    -> write /tmp/report/index.html (+ assets/ if needed)
    -> publish_artifact(path="/tmp/report", title="Sales dashboard")
    -> reply: "Готово: https://dadadevelopment.github.io/hermes-artifacts/a/7f2a9c1b3e/"

## How it works (for reference, not required reading to use it)

Storage is a GitHub repo's `gh-pages` branch
(`DadaDevelopment/hermes-artifacts` by default), one directory per artifact
under `a/<id>/`, served for free by GitHub Pages. Publishing = git clone/
pull + copy files + commit + push; no server to run, no bucket to own. Push
races (two publishes at once) are handled by fetch+reset+retry - safe
because each artifact gets its own directory.

Point it at your own repo instead of the default by setting, in
`config.yaml`:

    plugins:
      entries:
        share-artifact:
          settings:
            repo: "your-org/your-artifacts-repo"
            branch: "gh-pages"
            base_url: "https://your-org.github.io/your-artifacts-repo/"

The target repo's `gh-pages` branch must already exist with GitHub Pages
enabled on it (`gh api -X PUT repos/<owner>/<repo>/pages -f "source[branch]=gh-pages" -f "source[path]=/"`),
and the machine running Hermes needs git push access to it.
