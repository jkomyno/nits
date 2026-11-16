set shell := ["bash", "-cu"]

default:
    @just --list

# unit tests
test:
    uv run pytest -q

# per-rule precision/recall on the labeled corpus
eval:
    uv run nits eval

# meaning-preserving transform suite
invariance:
    uv run nits invariance

# regenerate calibration.py from the labeled corpus
calibrate:
    uv run nits eval --write

# everything that must be green before a commit
check: eval invariance test
    @echo "ok"
