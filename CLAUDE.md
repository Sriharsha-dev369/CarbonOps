# CarbonOps

## Building a spec

Build a spec as a series of commits, one per layer, so the git history doubles as a map of the work, both mid-build and later.

- A layer is one coherent slice, e.g. deps and config → models → data → a module or group of stubs → wiring → app → tests → docs. Aim for roughly 5–10 commits per spec.
- Commit each layer once it works: the project imports, and the tests written so far run.
- Commit message: a short imperative summary of what the layer adds. When the spec dictates a message, use it on the final commit.
- To find where something changed, use `git log --stat` and `git show <commit>` before re-reading files.
