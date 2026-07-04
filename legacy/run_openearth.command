#!/bin/bash
# Launch the frozen v1 Streamlit app from anywhere (double-clickable on macOS).
cd "$(dirname "$0")" || exit 1
exec uv run --project . streamlit run app/main.py
