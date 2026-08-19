# Build the distributable release assets. See scripts/build-assets.py.
.PHONY: dist wheel gui-build assets gui skills clean

dist: wheel gui-build assets   ## Build wheel + sdist + GUI/skills tarballs into dist/

wheel:               ## Build the Python wheel + sdist (uv)
	uv build

gui-build:           ## Build the Svelte GUI (node) → gui/dist
	cd gui && npm ci && npm run build

assets:              ## Package the GUI + skills tarballs (needs gui/dist built)
	python3 scripts/build-assets.py all

gui:                 ## Build just the GUI tarball
	python3 scripts/build-assets.py gui

skills:              ## Build just the memory-* skills tarball
	python3 scripts/build-assets.py skills

clean:               ## Remove build output
	rm -rf dist
