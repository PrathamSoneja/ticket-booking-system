# Milestone 2 submission checklist

Deadline: 18 Nov 2026 (hard deadline 20 Nov 2026).

## Code and repository

- [x] One dependency file: `src/requirements.txt` (Streamlit included; `requirements-ui.txt` removed)
- [x] `docs/` and `src/ui/` committed (`docs/` no longer git-ignored)
- [x] `python -m pytest` passes from `src` with Ollama running: 78 passed (twice)
- [x] `docker compose build --no-cache` and `docker compose up -d` work from a clean clone (`docs/docker-verification.md`)
- [ ] Push to the remote (not done automatically)
- [ ] `src/.venv`, `.pytest_cache`, `__pycache__`, `catalog.db*`, `.data/` excluded from the zip
- [x] No secrets, real card numbers or absolute local paths in the repository (only seeded demo accounts and mock cards)

## Zip contents (code + readme)

- [ ] `README.md` (setup, deployment, usage, Docker, UI, tests, requirement matrix)
- [ ] `docker-compose.yml`, `src/` (code, proto, tests, scripts, `ui/`, Dockerfile, requirements)
- [ ] `docs/`: `raft-design.md`, `docker-verification.md`, `stress-test-report.md`,
      `stress-results/`, `ui-verification.md`, `milestone2-audit.md`, `milestone2-completion-report.md`,
      `demo-script.md`
- [ ] Assignment PDF only if the instructor wants it included

## Video (5-10 minutes, Drive link)

- [ ] Follow `docs/demo-script.md`; must include leader kill and consistency after it
- [ ] Shows: election, replication (digests), booking + payment decline + refund, FAQ (LLM, fallback, out-of-domain), seat race, same-key retry, leader failure/recovery, no-quorum, stress results
- [ ] Drive link set to "anyone with the link can view"; link tested in a private window

## Reflection

- [ ] Each member fills in `docs/reflection-template.md` (own copy, own words, AI use disclosed)

## Viva

- [ ] Each member can explain: election, log matching, commit rule (current-term entries only), read barrier, idempotency, no-quorum behaviour, why the LLM cannot change state
- [ ] Re-read "Viva preparation" in `docs/milestone2-completion-report.md`
