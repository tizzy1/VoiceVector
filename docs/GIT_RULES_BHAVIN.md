# Git & collaboration rules — Bhavin's agent

You are building **Bhavin's part** of VoiceVector (server shell, WebSocket hub, both front-end apps, design, controls, demo, report UI, submission). Bhavin is also the **repo owner and merge owner** for the shared files. Three people and three AI agents work in one repo at the same time. These rules keep us from overwriting each other. Follow them exactly.

## 1. What you may edit
```
CLAUDE.md  README.md  .env.example  .gitignore  requirements.txt  pyproject.toml
server/contracts.py   web/src/protocol.ts        (shared — process in §4)
server/main.py  hub.py  session.py  feed.py
server/tests/integration/
web/  (everything EXCEPT web/src/audio/)
tools/  docs/
```
**You must NOT edit Tudor's or Sanju's areas**, even though you own the repo:

| Owner | Their area |
|---|---|
| Tudor | `data/` (except two generated files), `server/sim/`, `server/rules/`, `server/pilot/engine.py decision.py errors.py clearance.py`, `server/tests/sim|pilot|rules/`, `server/tests/voice/golden_parse.jsonl` |
| Sanju | `server/voice/`, `server/agents/`, `server/pilot/readback.py validator.py variants_build.py`, `web/src/audio/`, `server/tests/voice/`, `data/readback_variants.json`, `data/voices.json` |

**If a change in their area seems needed: STOP and tell Bhavin**, who asks the owner in the group chat.

## 2. First job: scaffold (before anyone else starts)
1. Create the GitHub repo, unzip the starter into it, invite Tudor and Sanju.
2. Add the **complete dependency list now** so nobody needs to add packages later:
   - `requirements.txt`: `fastapi uvicorn[standard] pydantic anthropic elevenlabs fal-client httpx[http2] rapidfuzz numpy python-dotenv pytest pytest-asyncio websockets`
   - `web/package.json`: `react react-dom zustand maplibre-gl deck.gl @deck.gl/core @deck.gl/layers @deck.gl/geo-layers @deck.gl/mesh-layers @deck.gl/mapbox framer-motion qrcode` (+ dev: `vite typescript @vitejs/plugin-react @types/react @types/react-dom`)
3. Create empty package folders with `__init__.py` for `server/sim`, `server/pilot`, `server/rules`, `server/voice`, `server/voice/parser`, `server/agents`, so imports resolve from minute one.
4. Commit the lockfiles (`package-lock.json`), push, then install your hook (below).

## 3. One-time setup
```sh
sh tools/git/install-hook.sh bhavin     # blocks commits into Tudor's/Sanju's areas
cp .env.example .env
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cd web && npm install && cd ..
```

## 4. Shared-file process (you are the gatekeeper)
- **`server/contracts.py` + `web/src/protocol.ts`:** change them only when a teammate asks in chat or you need an additive field yourself. Rules (CONTRACTS.md §7): additive optional fields only, without agreement; renames/removals need the affected owner's OK. **Always update both files in the same commit**, then post "contracts changed: <what>" in the chat.
- **Dependencies:** you are the only one who adds packages. When asked, add the package, run `npm install` / `pip install`, commit the lockfile alone (`[deps] add X`), and push straight away.
- **`server/session.py` wiring:** constructor signatures are fixed by CONTRACTS.md §2. Don't change how Tudor's or Sanju's classes are constructed without telling them.

## 5. Work loop (repeat for every task)
```sh
git checkout main && git pull --rebase origin main
git checkout -b bhavin/<short-task>      # e.g. bhavin/radar-tags
# ...work, commit small and often...
git add <only your files> && git commit -m "[radar] data tags with history dots"
git pull --rebase origin main
pytest server/tests/integration && python server/tests/integration/smoke_test.py && (cd web && npm run build)
git checkout main && git merge --ff-only bhavin/<short-task> && git push origin main
git branch -d bhavin/<short-task>
```
- Merge to `main` at least every 1–2 hours. Never keep a big branch overnight.
- Commit prefixes: `[server]`, `[hub]`, `[radar]`, `[viz]`, `[ui]`, `[report]`, `[deps]`, `[contracts]`, `[docs]`.
- Never `git push --force` to `main`. Never commit `.env`, `runs/`, `web/dist/`.
- `package-lock.json` conflict: take main's version (`git checkout --theirs web/package-lock.json`), run `npm install`, commit.

## 6. If you hit a merge conflict
- Conflict in **your** files: resolve it and re-run the integration tests.
- Conflict in **Tudor's or Sanju's** files: don't hand-merge it. Take main's version, finish the rebase, and tell the owner.
- The hook blocks a commit: unstage the listed files (`git restore --staged <file>`) and report what you needed.

## 7. Done means
Integration tests and the smoke test pass, the web build passes, your branch is merged into `main`, and both `/` and `/viz` load against the live server.

## 8. Post-MVP technical-depth ownership
For the planned trace, benchmarks, forecast, and live-import work, coordinate additive message/API changes with Sanju and Tudor under `CONTRACTS.md` §3.1. Bhavin owns shared contract definitions, `protocol.ts`, platform endpoints and display. Treat benchmark/forecast/live-import work as future scope until implemented and measured. Show forecast output as advisory; imported targets are simulation-only and no real-network control is permitted.
