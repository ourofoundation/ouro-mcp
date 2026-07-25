# UV_PYTHON overrides .python-version ("ouro" is a pyenv env name, not a uv request).
export UV_PYTHON ?= python3

.PHONY: build publish release

build:
	uv build --clear

publish: build
	uv publish

# usage: make release | make release minor | make release major
release:
	uv version --bump $(or $(filter-out $@,$(MAKECMDGOALS)),patch) --no-sync
	$(MAKE) publish

%:
	@:
