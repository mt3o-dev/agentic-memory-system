# Build the distributable release assets. See scripts/build-assets.py.
.PHONY: dist wheel assets gui skills clean

dist: wheel assets   ## Build wheel + sdist + GUI/skills tarballs into dist/

wheel:               ## Build the Python wheel + sdist (uv)
	uv build

assets:              ## Build the GUI + skills tarballs
	python3 scripts/build-assets.py all

gui:                 ## Build just the GUI tarball
	python3 scripts/build-assets.py gui

skills:              ## Build just the memory-* skills tarball
	python3 scripts/build-assets.py skills

clean:               ## Remove build output
	rm -rf dist
