Run the acceptance checks for a slice: $ARGUMENTS (for example S1).

1. Make sure the Docker stack is up (`docker compose ps` in docker/); start it if not.
2. Run `python -m pytest -q`, then `python -m tools.verify_slice $ARGUMENTS`.
3. For every failing check: say why it failed, fix it if it is in your workstream's folders, and rerun. If it belongs
   to another workstream, write what is needed into BREADCRUMBS.md tagged with that workstream instead.
4. Finish with the full verify_slice output and a one-line breadcrumb with the result.
