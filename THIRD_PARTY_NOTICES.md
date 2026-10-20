# Third-party notices

EssayPod builds on and includes work from open-source projects. Their original
copyright notices and license files remain in their respective directories.

## PDFMathTranslate

- Project: <https://github.com/Byaidu/PDFMathTranslate>
- Included directory: `translation_block/`
- License: GNU Affero General Public License v3.0
- License file: [`translation_block/LICENSE`](translation_block/LICENSE)

EssayPod contains local integration changes to this component. Because this
component is distributed with and used by EssayPod, EssayPod is released under
the AGPL-3.0-or-later license.

## Kotaemon, RAGFlow, and PaperQA

Some small EssayPod-owned wrappers and design decisions were developed while
evaluating these Apache-2.0 projects:

- Kotaemon: <https://github.com/Cinnamon/kotaemon>
- RAGFlow: <https://github.com/infiniflow/ragflow>
- PaperQA: <https://github.com/Future-House/paper-qa>

Local upstream source snapshots and archives are intentionally excluded from
the public repository because the running EssayPod application does not import
them. Their original license files remain in the local development copies.

## Other dependencies

Python and JavaScript dependencies are declared in `requirements.txt`, the
component requirement files, `translation_block/pyproject.toml`,
`llm_wiki/pyproject.toml`, and `web_ui/package.json`. Each dependency remains
subject to its own license.

