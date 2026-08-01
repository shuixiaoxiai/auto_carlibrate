# Repository delivery rules

- After a deliverable source change is complete and verified, commit it to `main` and push it to `origin` unless the user explicitly asks not to push.
- Generate Windows handoff archives with `python tools/create_source_archive.py`. Do not create archives that include `dist-local`, `build`, or other historical artifacts.
- When a new handoff archive is created locally, force-add that specific ZIP under `dist-local/`, commit it, and push it together with the corresponding source changes or in the immediately following archive commit.
- Report the commit, archive path, SHA-256, test result, and push result in the final response.
