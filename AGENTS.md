# Agent instructions

This fork restores Mobile Stadium in international Pokémon Stadium 2. OMM scope: `mobile-stadium`.

## Sources and continuity

- Read relevant internal context first; through OMM, read `get_role(name="project-base")` and scoped `context` (initially 1500–2500 characters). Open only needed records/sources and verify them against code.
- Before ROM, text or build work, read the Mobile Stadium project rules and use the OMM `mobile-stadium-expert` skill for technical investigation. Project specifics complement the global base.
- [README.md](README.md#mobile-stadium-maintenance) contains the technical documentation and repository map. Keep planning, decisions and pending work in canonical OMM; update the handoff with CAS, preserving other workstreams. Do not duplicate shared skills/rules locally.
- `OMM.md` and `memory/`, if present, are legacy snapshots. Do not write to them. If OMM is unavailable, report it and retain a durable local checkpoint with synchronization pending.

## Project constraints

- Preserve reference checkouts, original ROMs, saves, fixtures, evidence, accepted releases and unrelated local changes. Never commit commercial ROMs or private data.
- Retail Japanese ROMs are the reference. Locate patches by signature/profile and fail on ambiguity. Do not hand-edit generated `assets/localization`; preserve authored kit inputs and regional differences.
- Do not run `make init` or `make distclean`; do not use `make clean` as automatic cleanup. Follow [artifact retention](README.md#artifact-retention); `build/` also contains reference results.
- Distinguish builds, static checks, emulators and real N64 execution. Do not make speculative cursor timing changes. Preserve the eight trailing newlines in `de_msg.txt` when editing it.
- Do not automatically add Claude/Anthropic `Co-authored-by` trailers. Preserve human authors/coauthors, upstream credits and licenses.

## Closing work

Review diff, paths, links and command scope. Run validations only when relevant and authorized. Record evidence, completed/pending work and next steps in OMM. Clean only known, inactive task outputs after durable retention. Commit/push require current authorization; documentation maintenance does not resume paused development.
