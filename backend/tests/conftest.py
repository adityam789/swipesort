import os

# Tests never talk to a real model server (CLAUDE.md: tests pass without Ollama).
os.environ.setdefault("SWIPESORT_HOME", os.path.join(os.path.dirname(__file__), ".swipesort-test"))
