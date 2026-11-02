# Development entry points. Run `make help` to list them.
# Writing to the robot is deliberately absent: use the stackchan-flash skill.

SHELL := /bin/bash
.DEFAULT_GOAL := help

BOARD := elena-stackchan
BOARD_VARIANT := elena-stackchan
LANGUAGE := es-ES
SERVER_DIR := server/main/xiaozhi-server
SERVER_PY := $(CURDIR)/$(SERVER_DIR)/.venv/bin/python
UV_COMPILE := uv pip compile requirements.txt requirements-dev.txt --python-version 3.10 \
	--torch-backend cpu --no-header --quiet
IDF_ENV := source $(CURDIR)/tools/idf-env.sh >/dev/null
FIRMWARE_VENV := $(CURDIR)/.venv-firmware
FIRMWARE_PY := $(FIRMWARE_VENV)/bin/python
FIRMWARE_VENV_READY := $(FIRMWARE_VENV)/.synced
FIRMWARE_REQUIREMENTS := tools/requirements-firmware.txt
FIRMWARE_LOCK := tools/requirements-firmware.lock
FIRMWARE_COMPILE := uv pip compile $(FIRMWARE_REQUIREMENTS) --python-version 3.12 --no-header --quiet

.PHONY: help check-system setup setup-submodules setup-server setup-firmware setup-firmware-venv \
	test test-server test-firmware test-tools \
	check-locks check-server-lock check-firmware-lock check-firmware-venv-lock \
	update-server-lock update-firmware-venv-lock \
	firmware firmware-image firmware-index server-config server-run screen-viewer

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

check-system: ## Report missing system packages
	@tools/check-system.sh

setup: check-system setup-submodules setup-server setup-firmware setup-firmware-venv ## Install everything needed to develop and test

setup-submodules: ## Fetch firmware and server at their pinned commits
	git submodule update --init

setup-server: ## Create the server's Python 3.10 environment (CPU torch)
	cd $(SERVER_DIR) && uv venv --allow-existing --python 3.10 .venv
	cd $(SERVER_DIR) && uv pip sync --python .venv/bin/python --torch-backend=cpu requirements.lock
	uv pip check --python $(SERVER_PY)

setup-firmware: ## Install ESP-IDF v6.1 and its Python 3.12 environment
	@tools/setup-idf.sh

setup-firmware-venv: $(FIRMWARE_VENV_READY) ## Create the Python 3.12 environment for host tests and tools

# Other targets depend on this file, so the environment appears or updates by itself.
$(FIRMWARE_VENV_READY): $(FIRMWARE_LOCK)
	uv venv --allow-existing --python 3.12 $(FIRMWARE_VENV)
	uv pip sync --python $(FIRMWARE_PY) $(FIRMWARE_LOCK)
	uv pip check --python $(FIRMWARE_PY)
	touch $@

test: test-tools test-server test-firmware ## Run every test suite

test-server: ## Run the server tests
	cd $(SERVER_DIR) && $(SERVER_PY) -m pytest -q

test-firmware: $(FIRMWARE_VENV_READY) ## Run the firmware's host-side script tests
	cd firmware && $(FIRMWARE_PY) -m unittest discover -s scripts/tests
	cd firmware/docker/firmware-builder && $(FIRMWARE_PY) -m unittest test_firmware_builder

test-tools: $(FIRMWARE_VENV_READY) ## Run this repository's tool and skill tests
	$(FIRMWARE_PY) -m unittest discover -s tools/tests
	cd .claude/skills/stackchan-flash/scripts && $(FIRMWARE_PY) -m unittest test_stackchan_flash
	cd .claude/skills/local-model/scripts && $(FIRMWARE_PY) -m unittest test_model_gate
	cd .claude/skills/hermes-delegate/scripts && $(FIRMWARE_PY) -m unittest test_delegate

check-locks: check-server-lock check-firmware-venv-lock check-firmware-lock ## Fail if a lock file no longer matches its inputs

check-server-lock: ## Fail if the server's lock does not match its requirements
	@tmp=$$(mktemp) && cp $(SERVER_DIR)/requirements.lock $$tmp && \
	cd $(SERVER_DIR) && $(UV_COMPILE) -o $$tmp && cmp requirements.lock $$tmp && \
	rm -f $$tmp && echo "Server lock is current."

check-firmware-venv-lock: ## Fail if the firmware environment's lock does not match its requirements
	@tmp=$$(mktemp) && cp $(FIRMWARE_LOCK) $$tmp && \
	$(FIRMWARE_COMPILE) -o $$tmp && cmp $(FIRMWARE_LOCK) $$tmp && \
	rm -f $$tmp && echo "Firmware environment lock is current."

check-firmware-lock: ## After a build, fail if component versions changed
	git -C firmware diff --exit-code -- dependencies.lock
	@echo "Firmware lock is current."

update-server-lock: ## Refresh the server's lock after editing its requirements
	cd $(SERVER_DIR) && $(UV_COMPILE) -o requirements.lock $(if $(UPGRADE),--upgrade-package $(UPGRADE))

update-firmware-venv-lock: ## Refresh the firmware environment's lock after editing its requirements
	$(FIRMWARE_COMPILE) -o $(FIRMWARE_LOCK) $(if $(UPGRADE),--upgrade-package $(UPGRADE))

firmware: ## Build the firmware for the configured board
	$(IDF_ENV) && cd firmware && python scripts/build.py $(BOARD) --name $(BOARD_VARIANT) --language $(LANGUAGE)

firmware-image: ## Build an image in dist/firmware; set PURPOSE, optional LANGUAGE, XIAOZHI_CLOUD=1, SCREEN_STREAM=1, AVATAR=<folder>
	$(IDF_ENV) && python $(CURDIR)/tools/build_firmware_image.py --purpose "$(PURPOSE)" \
		--language "$(LANGUAGE)" --board "$(BOARD)" --variant "$(BOARD_VARIANT)" $(if $(XIAOZHI_CLOUD),--xiaozhi-cloud) $(if $(SCREEN_STREAM),--screen-stream) $(if $(AVATAR),--avatar "$(AVATAR)")

firmware-index: ## Refresh the compile database that VS Code reads
	$(IDF_ENV) && cd firmware && idf.py reconfigure

server-config: $(FIRMWARE_VENV_READY) ## Generate the gitignored server config; PROFILE=local (default) or minimax
	$(FIRMWARE_PY) tools/gen_server_config.py --profile $(or $(PROFILE),local) $(if $(FORCE),--force)

server-run: ## Start the server with the generated config; local services run separately
	cd $(SERVER_DIR) && $(SERVER_PY) app.py

screen-viewer: $(FIRMWARE_VENV_READY) ## Watch Elena's screen; set ROBOT, optional ARGS (see docs/screen-stream.md)
	$(FIRMWARE_PY) tools/screen_viewer.py view --robot "$(ROBOT)" $(ARGS)
