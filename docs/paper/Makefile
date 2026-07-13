# Build the OpenEarth v2 preprint. Requires a TeX Live install (latexmk).
# Figures are pre-generated into figures/ from committed repo artifacts;
# regenerate them with:  uv run --with matplotlib python make_figures.py
# (script kept alongside the paper as figures/make_figures.py).

paper.pdf: paper.tex references.bib
	latexmk -pdf -interaction=nonstopmode paper.tex

.PHONY: clean watch
clean:
	latexmk -C paper.tex

watch:
	latexmk -pdf -pvc -interaction=nonstopmode paper.tex
