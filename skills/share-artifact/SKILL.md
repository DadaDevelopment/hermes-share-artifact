---
name: share-artifact
description: "Use when a result should be shown as a standalone web page (HTML report, dashboard, rendered doc) instead of pasted into chat - publish it and hand back a public link."
version: 0.2.0
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
- Anything with secrets or credentials in it - this publishes PUBLICLY (the
  URL itself is the only access control, no login). Never publish
  credential dumps, internal-only data, or PII.

## Tools

- `publish_artifact(path, title?, entrypoint?)` - publish a local file or a
  whole directory (index.html + assets/). For a directory, `entrypoint`
  picks which file to serve (default `index.html`).
- `publish_html(html, title?)` - publish a raw HTML string you already have
  in memory.
- `publish_markdown(markdown, title?)` - publish Markdown, auto-rendered
  with GitHub styling.
- `delete_artifact(artifact_id)` - take a previously published artifact down.
- `list_artifacts()` - list what's been published in this profile.

All publish tools return a public URL immediately (no polling, no build
step, no git push) plus an `artifact_id` for later deletion.

## Workflow

1. Build the artifact into a working directory (or just hold the HTML/MD in
   a variable) as you normally would.
2. Call `publish_artifact` / `publish_html` / `publish_markdown`.
3. Return the URL to the user with a one-line description. Do NOT also paste
   the full HTML/Markdown into the chat - the whole point is to avoid that.
4. If asked to update the report, just publish again (a new artifact_id/URL
   each time) - there is no in-place edit.

Example:

    User: "Sdelay mne interaktivnyy HTML-dashboard po etim CSV"
    -> write /tmp/report/index.html (+ assets/ if needed)
    -> publish_artifact(path="/tmp/report", title="Sales dashboard")
    -> reply: "Gotovo: https://harness.dada-tuda.ru/share/artifact/a1b2c3d4e5f6/9f8e7d.../"

## How it works (for reference, not required reading to use it)

Storage is the LOCAL FILESYSTEM under this plugin's profile-scoped data
directory - nothing is ever pushed anywhere. Publishing = write files to
disk + compute an HMAC signature over a random id. A small stdlib HTTP
server binds `127.0.0.1:<port>` (default 8791) and serves
`/share/artifact/<id>/<sign>/...`; whatever reverse proxy already
terminates public TLS for this Hermes instance (Caddy/nginx) forwards that
one path prefix to it. The signature is the access control: holding the
full URL is what grants viewing; ids/signs are not guessable or
enumerable, and there is no directory listing.

Nothing here talks to git, GitHub, or any external service. Deleting an
artifact removes its directory from disk; the URL 404s immediately after.

## One-time reverse-proxy setup (operator, not the agent)

Add ONE route to the proxy in front of this Hermes instance forwarding
`/share/artifact/*` to `127.0.0.1:<port>` (same port as
`plugins.entries.share-artifact.settings.port`, default 8791). Example for
Caddy:

    handle_path /share/artifact/* {
        reverse_proxy 127.0.0.1:8791
    }

Reload the proxy after adding it. The plugin's server binds loopback-only
by design - it is never exposed directly, only through the proxy that
already owns the box's public hostname and TLS.

## Config (optional; all have sane defaults)

    plugins:
      entries:
        share-artifact:
          settings:
            port: 8791
            base_url: "https://harness.dada-tuda.ru"

`base_url` defaults to `HERMES_DASHBOARD_PUBLIC_URL` if set (which it
already is on any instance with a public dashboard URL configured).
