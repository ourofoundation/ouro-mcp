# UV_PYTHON overrides .python-version ("ouro" is a pyenv env name, not a uv request).
export UV_PYTHON ?= python3

# Project-scoped token stored via:
#   keyring set 'https://upload.pypi.org/legacy/?ouro-mcp' __token__
PUBLISH_URL ?= https://upload.pypi.org/legacy/?ouro-mcp

.PHONY: build publish release

build:
	uv build --clear

publish: build
	uv publish \
		--username __token__ \
		--keyring-provider subprocess \
		--publish-url '$(PUBLISH_URL)'

# usage: make release | make release minor | make release major
release:
	uv version --bump $(or $(filter-out $@,$(MAKECMDGOALS)),patch) --no-sync
	$(MAKE) publish

%:
	@:
