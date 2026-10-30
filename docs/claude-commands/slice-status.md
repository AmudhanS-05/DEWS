Report where the current slice stands for this workstream.

1. Read docs/SLICES.md and find the first slice whose rows for this workstream are not all done.
2. Read the top 30 lines of BREADCRUMBS.md (main repo folder) and `git log --oneline -15`.
3. Run `python -m pytest -q`.
4. Print: current slice, tasks done (with the breadcrumb evidence), tasks left, anything blocked on a YOU item, and
   the next task you would do. Do not change any files.
