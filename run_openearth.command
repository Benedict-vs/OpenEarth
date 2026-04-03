#!/bin/bash
cd "$HOME/OneDrive/Documents/03 Projects/OpenEarth" || exit 1
source .venv/bin/activate
streamlit run app/main.py
