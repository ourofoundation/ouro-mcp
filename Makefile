# UV_PYTHON overrides .python-version ("ouro" is a pyenv env name, not a uv request).
export UV_PYTHON ?= python3

.PHONY: build release

build:
	uv build --clear

# usage: make release | make release minor | make release major
# Bumps pyproject.toml. Commit and push to main to publish via GitHub Actions.
release:
	uv version --bump $(or $(filter-out $@,$(MAKECMDGOALS)),patch) --no-sync
	@echo ""
	@echo "Bumped to $$(uv version --short). Commit and push to main to publish."

%:
	@:
