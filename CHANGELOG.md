# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.0 - 2026-09-15

### Added

- **Segment-aware Codex rollout summarizer.** A Codex rollout file is not
  necessarily one working session: resuming can fork a thread and replay the
  ancestor's entire history into the new file, and a single thread id
  accumulates work across days under changing names. `summarize_codex_rollout.py`
  now maps a rollout with `--segments` (lineage via `forked_from_id` and embedded
  `session_meta`, inherited-replay detection, idle-gap segmentation with a
  3-hour default tunable through `--gap-hours`) and summarizes one working
  session with `--segment N|last`. The `resume-codex-session` and
  `recall-session` skills map segments before reading, so a handoff carries the
  session the user actually meant instead of several conflated ones.
- **Mirror-copy test.** The two plugins ship copies of the shared scripts rather
  than symlinks, because plugin installation copies the directory tree. Nothing
  caught a one-sided edit before; `tests/test_mirror_copies.py` now asserts the
  copies are byte-identical, matched by script basename.

### Fixed

- **Session lookup by name no longer returns nothing when ripgrep is missing.**
  `find_claude_session.py` shelled out to `rg` and swallowed the resulting
  `OSError`, so on a machine without ripgrep on `PATH` every name lookup
  silently reported zero candidates. The search backend is now chosen with
  `shutil.which("rg")` and falls back to an equivalent pure-Python scan of the
  same json/jsonl files when ripgrep is unavailable or exits abnormally.
- **Name lookup through ripgrep works on Windows and with non-ASCII transcripts.**
  ripgrep output was split on `:`, so a Windows path such as `C:\Users\...` lost
  everything after the drive letter and every match was discarded; a lone search
  root that is a file (`history.jsonl` only) printed no path at all; and output
  was decoded with the locale encoding, which crashed on non-ASCII session names
  under a non-UTF-8 locale. ripgrep now runs with `--with-filename --null` and its
  output is decoded as UTF-8.

### Changed

- **Test suite is stable on a fresh clone.** Scope selection (`--days`/`--since`)
  filters transcripts by mtime, which git does not preserve, so three scope tests
  failed on every fresh clone. Fixture mtimes are now pinned in `setUp`;
  assertions are unchanged. `python3 -m unittest discover -s tests` is green.

## 0.1.0 - 2026-04-26

### Added

- Initial release: cross-agent session handoff between Claude Code and Codex.
- Claude Code plugin with `recall-context`, `recall-session`, and
  `resume-codex-session` skills.
- Codex plugin with `recall-context`, `recall-session`, and
  `resume-claude-session` skills.
