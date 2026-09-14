# Dory Game Factory AI Team

The company is one sequential Codex workflow with twelve logical roles. It extends the existing Game Factory and does not replace GameSpec, Unity generation, validation, tests, or Android build.

## Organization

```text
CEO
└─ Producer / PM
   └─ Game Director
      └─ Technical Director
         ├─ Gameplay Engineer
         ├─ Systems Engineer
         ├─ UI/UX Engineer
         ├─ Art Director
         └─ Level Designer
            └─ QA Engineer
               └─ Code / Quality Reviewer
                  └─ Build / Release Engineer
                     └─ CEO release decision
```

The role definitions, permissions, task types, prompts, departments, AI names, and display order come from `config/AGENTS.json`. Python code never duplicates those prompts.

## Roles

| Role | Responsibility |
|---|---|
| CEO | Normalize the user goal, success conditions, priority, and release decision |
| Producer / PM | Create executable tasks, allowlists, dependencies, and handoffs |
| Game Director | Define the core loop, progression, difficulty, and game differentiation |
| Technical Director | Map design to Core, Gameplay, Modules, UI, Editor, and GameSpec |
| Gameplay Engineer | Player, combat, physics, obstacles, enemies, and game feel |
| Systems Engineer | Save, economy, progression, input, pooling, audio, and shared systems |
| UI/UX Engineer | Portrait mobile HUD, menus, shop, settings, tutorial, and safe area |
| Art Director | Asset direction and policy-safe Gemini/local image requests |
| Level Designer | Stage flow, patterns, pacing, and procedural generation |
| QA Engineer | Acceptance, regression, tests, severity, and evidence |
| Quality Reviewer | Independent architecture, quality, performance, and release review |
| Release Engineer | Existing Unity test/build pipeline and current APK verification |

## Owner and agent role

`owner: codex` means the Codex execution adapter performs the task. `agent_role` selects the company prompt and permissions used for that run. Legacy tasks without `agent_role` retain their original behavior. `task_type` must be listed in the selected role's `allowed_task_types`.

Every task keeps `files` as a hard diff allowlist. Role instructions cannot widen policy, project rules, or that allowlist. Only one code-writing role may reserve the shared working tree at a time.

## Task lifecycle and handoff

```text
TODO -> IN_PROGRESS -> REVIEW -> DONE
                     └-> BLOCKED
```

Codex never marks its own code DONE. A clean run lands in REVIEW. QA and release evidence decide whether work advances. A handoff stores:

```text
STATUS
SUMMARY
FILES_READ
FILES_CHANGED
ACCEPTANCE_RESULT
RISKS
HANDOFF_TO
HANDOFF_REASON
```

Dependencies are checked by Python. Handoff prose cannot change ownership or bypass them.

## Commands

Run from `C:\Dory_tycoon\AI_GAME_COMPANY`:

```powershell
python -m company.orchestrator.main team agents
python -m company.orchestrator.main team status
python -m company.orchestrator.main team board
python -m company.orchestrator.main team run --task <ID>
python -m company.orchestrator.main team next
python -m company.orchestrator.main team order --game game02 --goal "Game02 만들어."
```

`team order` keeps the raw goal, creates a validated seven-stage structured plan, writes it to `company/plans/<game>.json`, adds Codex-owned role tasks to the shared board, shows the plan, and starts the first executable task. Use `--dry-run` to inspect without writing or executing.

## QA and release gates

QA results are `PASS`, `FAIL`, or `NOT_VERIFIED`. Severities are `BLOCKER`, `CRITICAL`, `MAJOR`, `MINOR`, and `POLISH`. BLOCKER and CRITICAL failures stop release.

Quality is scored across gameplay 20, graphics 20, UI/UX 15, animation/VFX 15, audio 10, stability 10, and mobile 10. The target is 85. Visual scores require a device or screenshot inspection; otherwise they remain NOT_VERIFIED.

Release uses the existing pipeline. Success requires a successful process, a successful Unity Build Report, and a current APK on disk. Read `Reports/errors/latest.txt`, `Reports/build-status/latest.txt`, `Reports/runs/latest.txt`, and `Reports/sync-status/latest.txt` before reporting a result. Device-only visual checks remain `HUMAN_GATE_DEVICE_TEST`.

## Game01 validation

Before starting Game02, run the Python suite, then Game01 Unity tests and the completion gate. Do not weaken existing tests or modify the verified Unity/Android baseline.

```powershell
python -m unittest discover -s tests
python -m company.orchestrator.main test --game game01 --platform both
python -m company.orchestrator.main gate --game game01
```

## Game02 example

```powershell
python -m company.orchestrator.main team order --game game02 --goal "Game02 만들어."
```

Game02 must use a distinct idle economy and offline reward loop. It is not a Game01 reskin.

## Human gates and troubleshooting

Human action is limited to initial logins/licenses, Android signing, store credentials/products, paid decisions forbidden by policy, and device-only visual review. The workflow never commits or pushes automatically.

- Invalid role or task type: fix `agent_role`/`task_type` against `config/AGENTS.json`.
- No executable task: accept the preceding REVIEW item or resolve its BLOCKED evidence.
- Writer busy: wait for the current shared-tree task to finish; do not remove the lock while it is active.
- Unity or APK unverified: run on the build PC and inspect the four report files above.
