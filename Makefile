# SwipeSort — common tasks (macOS / Linux). Windows: scripts\swipesort.ps1 <task>
# `make help` lists everything.

UV    ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
MODEL ?= qwen2.5:7b-instruct
PORT  ?= 8765
SERVE  = cd backend && $(UV) run swipesort serve --port $(PORT)

.DEFAULT_GOAL := help
.PHONY: help doctor setup ui run lan watch pair llm llm-eco agent agent-remove agent-status \
        test lint dev clean

help: ## List tasks
	@awk 'BEGIN{FS=":.*## "} /^[a-z-]+:.*## /{printf "  make %-13s %s\n",$$1,$$2}' $(MAKEFILE_LIST)

doctor: ## Check prerequisites
	@command -v $(UV) >/dev/null 2>&1 || { echo "missing: uv  -> curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "missing: node 20+  -> https://nodejs.org (or: brew install node)"; exit 1; }
	@node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' || { echo "node 20+ required"; exit 1; }
	@command -v ollama >/dev/null 2>&1 && echo "ok: ollama (LLM features on)" || echo "optional: ollama not found (LLM features off)"
	@echo "ok: uv, node"

setup: doctor ## Install dependencies and build the UI
	cd backend && $(UV) sync
	cd frontend && npm ci && npm run build

ui: ## Rebuild the UI after frontend changes
	cd frontend && npm run build

run: ## Start on this computer only (http://127.0.0.1:8765)
	$(SERVE)

lan: ## Start and allow paired phones on the local network
	$(SERVE) --lan

watch: ## Start with watched folders (see config: [watch])
	$(SERVE) --watch

pair: ## Show a pairing QR for a running `make lan`
	cd backend && $(UV) run swipesort pair --port $(PORT)

llm: ## Download the LLM (default qwen2.5:7b-instruct)
	ollama pull $(MODEL)

llm-eco: ## Download the small LLM for low-RAM / CPU-only machines
	ollama pull qwen2.5:3b-instruct

agent: ## Start SwipeSort (watch mode) at login
	cd backend && $(UV) run swipesort agent install

agent-remove: ## Stop starting at login
	cd backend && $(UV) run swipesort agent uninstall

agent-status: ## Is the login agent installed?
	cd backend && $(UV) run swipesort agent status

test: ## Run all tests (no models needed)
	cd backend && $(UV) run pytest -q
	cd frontend && npm test -- --run

lint: ## Type-check and lint
	cd backend && $(UV) run mypy swipesort && $(UV) run ruff check .
	cd frontend && npx tsc -b && npx oxlint

dev: ## API with auto-reload (run `cd frontend && npm run dev` alongside)
	$(SERVE) --dev

clean: ## Remove build outputs and caches (not your data in ~/.swipesort)
	rm -rf frontend/dist backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache
