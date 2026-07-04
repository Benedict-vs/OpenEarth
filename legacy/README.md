# OpenEarth v1 (legacy)

The original Streamlit app, frozen as-is with its own pinned dependencies. It is **not** a uv
workspace member — it has its own resolution so the old pins never fight the v2 stack.

Run it:

```bash
cd legacy && uv run --project . streamlit run app/main.py
# or: make legacy   (from the repo root)
```

This directory is deleted in one commit when v2 reaches feature parity (end of Phase 4).
Do not add features here; known v1 defects are fixed in `packages/core`, not in this tree.
