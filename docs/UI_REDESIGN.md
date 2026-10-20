# EssayPod Light UI Redesign

## Goal

The redesign gives EssayPod one coherent, light product language inspired by modern AI workspaces and native macOS interaction. It deliberately preserves the existing React state machines, backend routes, streaming protocols, local storage keys, and Knowledge Graph implementation.

## Architecture

The UI remains a single-page React workspace with two top-level surfaces:

1. **Paper workspace** — project library, paper import, audio, reviewer brief, translation, paper Q&A, video studio, and publishing copy.
2. **Knowledge Graph** — the existing LLM Wiki Graph UI embedded from `/api/wiki/graph-view`.

The redesign is implemented as a final CSS cascade layer:

- `web_ui/app/globals.css` — legacy component rules and Markdown rendering behavior.
- `web_ui/app/overrides.css` — existing feature-specific fixes.
- `web_ui/app/light-ui.css` — the new design system and complete light visual treatment, imported last.
- `web_ui/app/page.tsx` — unchanged business interactions; only workspace copy and accessibility labels were adjusted.
- `web_ui/app/layout.tsx` — imports the new visual layer and applies the `essaypod-light-ui` body class.

This separation makes rollback safe and keeps feature work independent from the design layer.

## Design system

- Neutral light canvas, white work surfaces, subtle borders, and restrained shadows.
- Black primary actions, blue informational states, green completion states, and red failures.
- System typography (`SF Pro`/Inter/PingFang) for the application; serif typography is retained only for long-form paper reading.
- Compact 8–12 px corner radii; status chips remain pill-shaped only where their compact form is useful.
- Apple-like motion uses `cubic-bezier(.22, 1, .36, 1)` with restrained entrance, hover, press, and modal transitions.
- `prefers-reduced-motion` disables non-essential motion.
- Visible keyboard focus states remain available across buttons, inputs, links, iframes, and interactive list rows.

## Project rail

The left project rail follows a compact Codex-style information hierarchy:

- Import and Knowledge Graph are stable, equal-level primary navigation rows.
- Search and the filtered result count sit directly above a dedicated scrolling paper list.
- Each paper uses a two-level row: title/date first, then source and processing status.
- Selected and hover states use quiet surface fills instead of elevated cards.
- Destructive controls stay hidden until hover or keyboard focus, avoiding layout shifts while keeping deletion accessible.
- Long titles are limited to two lines with a native tooltip, and horizontal overflow is disabled.
- The account area remains fixed at the bottom while only the paper list scrolls.

## Preserved feature contract

The redesign does not alter these workflows or endpoints:

- Task library: `GET /api/tasks`, `DELETE /api/tasks/{id}`
- Local upload: `POST /api/tasks`, then `POST /api/tasks/{id}/generate`
- URL import: `POST /api/tasks/from-url`, background stage polling, and `POST /api/tasks/{id}/download/retry`
- Paper/audio: PDF, transcript, timed transcript, audio, live audio, and DVR resources
- Reviewer brief: regular and event-stream endpoints
- Translation: source/Chinese Markdown, assets, original-layout PDF, and reading PDF
- Paper Q&A: status, document index, conversations, and streamed chat
- Video studio: load/save layout, generation, preview, cover, and output resources
- Publishing copy: stored result and streamed generation
- LLM Wiki: Graph iframe and related Wiki routes

## Data-safety hardening

`app.py` writes task `metadata.json` through a same-directory temporary file and atomic rename. This prevents a reader from observing a partially written JSON document while background import, Wiki compilation, and UI polling update the same task.

## Backup and rollback

Local pre-redesign snapshots are intentionally excluded from the public
repository. Use Git history to inspect or restore an earlier public version,
then restart `EssayPod.app`. A source rollback does not touch `.env`, uploaded
papers, task data, generated media, or installed dependencies.

## Verification checklist

After UI changes:

1. Run `npm run build` and the rendered HTML test from `web_ui/`.
2. Run the backend test suite.
3. Compare the API calls in `page.tsx` against the backend route table.
4. Confirm the task list and Wiki status endpoints on the running local backend.
5. Restart `EssayPod.app` so its local frontend is rebuilt from the updated source.
