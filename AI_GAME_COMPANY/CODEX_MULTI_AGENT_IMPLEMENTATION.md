# CODEX MULTI-AGENT IMPLEMENTATION — EXECUTABLE MASTER DOCUMENT

Repository: `gojo3105-a11/Dory_tycoon`

Target branch currently used by the project: `claude/delete-current-content-mgn4xm`

This file is an implementation instruction for Codex. Read it together with `CLAUDE.md`, `HANDOVER.md`, `AI_GAME_COMPANY/README.md`, `AI_GAME_COMPANY/config/company_policy.json`, `AI_GAME_COMPANY/config/TASKBOARD.json`, and `AI_GAME_COMPANY/config/AGENTS.json`.

## 0. Objective

Extend the existing `AI_GAME_COMPANY` orchestrator without replacing it.

The finished system must support this user experience:

```text
User types one sentence:
"Game02 만들어."

CEO
-> Producer
-> Game Director
-> Technical Director
-> specialist implementation agents
-> QA
-> Quality Reviewer
-> Release Engineer
-> Android APK verification
-> CEO final report
```

This is a logical multi-agent company running through Codex roles. It is NOT permission to launch many Codex processes concurrently in the same working tree.

## 1. Non-negotiable compatibility

The existing project is already a working Unity Game Factory:

```text
GameSpec
-> Editor generators
-> Scene / Prefab / Level / UI
-> Validate
-> Unity Test
-> Android Build
-> APK
```

Do not rewrite the factory.

Keep these existing guarantees:

- existing `owner: codex` tasks keep working
- tasks without `agent_role` keep working
- `files` remains the hard diff allowlist
- `depends_on` still blocks execution
- a clean Codex run lands in REVIEW, not DONE
- no automatic commit/push
- `company_policy.json` is authoritative for cost and safety
- no paid API escalation
- actual APK presence is required before build success
- existing Unity version and verified Android baseline are not changed without an explicit task

## 2. Required implementation files

Prefer the following additions:

```text
AI_GAME_COMPANY/config/AGENTS.json
AI_GAME_COMPANY/company/orchestrator/agent_registry.py
AI_GAME_COMPANY/company/orchestrator/agent_dispatcher.py
AI_GAME_COMPANY/company/orchestrator/manager.py
AI_GAME_COMPANY/tests/test_agent_registry.py
AI_GAME_COMPANY/tests/test_agent_routing.py
AI_GAME_COMPANY/tests/test_manager_planning.py
AI_GAME_COMPANY/AGENT_TEAM.md
```

Modify only where necessary:

```text
AI_GAME_COMPANY/company/orchestrator/teamwork.py
AI_GAME_COMPANY/company/orchestrator/main.py
AI_GAME_COMPANY/company/orchestrator/dashboard.py
AI_GAME_COMPANY/company/orchestrator/orders.py
AI_GAME_COMPANY/config/TASKBOARD.json
```

Do not add files merely to match this suggested layout if a smaller change integrates better with the current codebase.

## 3. TASKBOARD backward-compatible schema

Extend Task with optional fields:

```json
{
  "agent_role": "gameplay_engineer",
  "department": "게임개발부",
  "task_type": "gameplay_code",
  "priority": 50,
  "handoff_from": "technical_director",
  "handoff_to": "qa_engineer"
}
```

All new fields are optional.

Old tasks must deserialize, execute, save, and retain their existing fields unchanged.

If `agent_role` is absent:
- retain current behavior
- do not rewrite all old tasks
- use a safe default role only while constructing the Codex prompt

## 4. Agent Registry

Implement `agent_registry.py`.

Responsibilities:

- load UTF-8 / UTF-8 BOM JSON
- validate `_version`
- validate unique agent IDs
- reject unknown agent IDs
- expose get(agent_id)
- expose list_agents()
- expose prompt(agent_id)
- expose department(agent_id)
- validate task_type against `allowed_task_types` when task_type is present
- never silently fall back for an explicitly invalid agent ID

The registry must be data-driven. Do not duplicate the 12 prompts across Python files.

## 5. Prompt injection

Extend `teamwork.build_prompt()`.

Current HOUSE_RULES remain mandatory.

When a task has an agent role, add a section before the task goal:

```text
CURRENT COMPANY ROLE
Agent: Gameplay Engineer
Agent ID: gameplay_engineer
Department: 게임개발부

ROLE INSTRUCTIONS
<the prompt from AGENTS.json>
```

Then include the existing GOAL, allowlist, acceptance, notes and HOUSE_RULES.

Precedence:

1. company policy
2. CLAUDE.md / HOUSE_RULES
3. task file allowlist
4. agent role instructions
5. task goal

The role prompt cannot grant permissions forbidden by higher layers.

## 6. Dispatcher

Implement an agent dispatcher.

It must:
- resolve an agent from task.agent_role
- verify the role exists
- verify the role can perform the task type
- reject attempts by a read-only role to edit production code
- map specialist categories to roles
- not execute two writing agents at the same time in the shared working tree

Suggested routing:

```text
objective / release decision -> ceo
task breakdown -> producer
game design -> game_director
architecture -> technical_director
gameplay -> gameplay_engineer
core/save/economy -> systems_engineer
ui -> ui_ux_engineer
art -> art_director
level/stage -> level_designer
test/qa -> qa_engineer
review -> quality_reviewer
build/release -> release_engineer
```

## 7. CEO one-sentence order

Add a command compatible with the existing CLI style.

Preferred UX:

```powershell
cd C:\Dory_tycoon\AI_GAME_COMPANY
python -m company.orchestrator.main team order --game game02 --goal "Game02 만들어."
```

If an existing `orders.py` natural-language order path already does most of this, reuse it instead of creating a competing input system.

The order command must:

1. record the raw user goal
2. have CEO normalize it into OBJECTIVE/SUCCESS/PRIORITY
3. Producer produce structured tasks
4. validate the plan
5. add tasks to TASKBOARD
6. assign `owner: codex`
7. assign `agent_role`
8. assign dependencies
9. show the plan before execution in the console/dashboard
10. execute automatically unless a HUMAN_GATE is required

Do not ask for routine confirmation.

## 8. Manager planning format

Use structured JSON, not free-form parsing.

Example:

```json
{
  "project": "game02",
  "objective": "Create the Game02 Idle Factory Tycoon vertical slice.",
  "success": [
    "core loop playable",
    "save/load works",
    "upgrade UI works",
    "tests pass",
    "APK verified"
  ],
  "tasks": [
    {
      "title": "Design idle core loop",
      "agent_role": "game_director",
      "task_type": "game_design",
      "goal": "...",
      "files": [],
      "acceptance": ["..."],
      "depends_on": []
    }
  ]
}
```

Validate every generated task before inserting it.

Reject:
- unknown agent
- empty goal
- invalid path
- dependency cycle
- duplicate task id
- write task with empty allowlist
- role that lacks write permission for a code-writing task

## 9. Sequential company workflow

Default execution sequence:

```text
CEO
Producer
Game Director
Technical Director
specialist(s)
QA
Quality Reviewer
Release Engineer
CEO
```

Specialists may include several tasks, but writing tasks remain sequential.

Do not implement shared-tree parallel writes.

If future worktree isolation is introduced, parallelism may be added later under a separate task.

## 10. Handoffs

Each task result should support a structured handoff:

```text
STATUS:
SUMMARY:
FILES_READ:
FILES_CHANGED:
ACCEPTANCE_RESULT:
RISKS:
HANDOFF_TO:
HANDOFF_REASON:
```

Store concise handoff information in TASKBOARD notes or a dedicated run result structure.

Do not let handoff prose change `owner` or bypass dependencies by itself. Python code remains responsible for validated state transitions.

## 11. QA gate

Implementation tasks never go straight to release.

After implementation:
- task goes REVIEW
- QA validates acceptance
- automated tests run where available
- failures route back to the responsible implementation role
- successful QA hands to Quality Reviewer

QA uses:
- PASS
- FAIL
- NOT_VERIFIED

Severity:
- BLOCKER
- CRITICAL
- MAJOR
- MINOR
- POLISH

BLOCKER/CRITICAL prevents release.

## 12. Quality review gate

Quality Reviewer is read-only.

Check:
- architecture
- regression
- GameSpec compatibility
- mobile performance
- null/state safety
- duplication
- deprecated API
- Runtime UnityEditor reference
- hardcoding
- allowlist compliance

Quality score:

- gameplay 20
- graphics 20
- UI/UX 15
- animation/VFX 15
- audio 10
- stability 10
- mobile 10

Target >= 85.

Do not invent visual scores when a device/screenshot was not inspected. Mark those dimensions NOT_VERIFIED where appropriate.

## 13. Build / Release gate

Use the existing build pipeline, not a second build system.

Before saying BUILD SUCCESS read:

```text
Reports/errors/latest.txt
Reports/build-status/latest.txt
Reports/runs/latest.txt
Reports/sync-status/latest.txt
```

Success requires all of:

- process exit code success
- Unity Build Report success
- current APK exists on disk

Where possible also verify:
- timestamp
- file size
- hash
- relation to current run

Failure categories:

```text
COMPILE_FAILURE
TEST_FAILURE
VALIDATION_FAILURE
UNITY_BUILD_FAILURE
ANDROID_TOOLCHAIN_FAILURE
ARTIFACT_MISSING
```

Device-only visual checks become:

`HUMAN_GATE_DEVICE_TEST`

## 14. Dashboard

Connect the registry to the current office-style dashboard.

Show real state derived from TASKBOARD/runs, not decorative fake activity.

Departments:

```text
경영실
프로젝트관리실
게임기획부
기술기획실
게임개발부
플랫폼개발부
UIUX개발부
디자인부
콘텐츠기획부
품질보증부
기술감사실
빌드출시부
```

For each agent show:
- display name
- department
- state
- current task
- last result

Possible states:

```text
IDLE
PLANNING
WORKING
WAITING
REVIEWING
BLOCKED
DONE
```

Do not hardcode fake busy states.

## 15. CLI acceptance target

Preserve:

```powershell
python -m company.orchestrator.main team run --task <ID>
python -m company.orchestrator.main team board
```

Add or provide equivalent commands for:

```powershell
python -m company.orchestrator.main team agents
python -m company.orchestrator.main team status
python -m company.orchestrator.main team order --game game01 --goal "..."
python -m company.orchestrator.main team next
```

`team next` selects only an executable TODO task whose dependencies are satisfied.

## 16. Game01 validation first

Do not immediately generate all 10 games.

Use Game01 Factory Runner to validate the new orchestration layer while preserving existing gameplay.

Recommended first live test goal:

```text
"Game01의 현재 미완료 작업을 분석해서 부서별로 재배정하고,
QA와 Release까지 연결하되 기존 정상 기능을 변경하지 마."
```

The multi-agent layer is considered ready for Game02 only after:
- old tasks still work
- new agent_role tasks work
- routing tests pass
- prompt injection tests pass
- QA/review gates work
- existing Game01 tests are not weakened
- existing build flow still works

## 17. Game02 target after infrastructure passes

Then support:

```text
"Game02 만들어."
```

Expected department flow:

```text
CEO
-> Producer
-> Game Director
-> Technical Director
-> Systems Engineer (idle economy / offline reward)
-> UI/UX Engineer
-> Art Director when assets are needed
-> QA Engineer
-> Quality Reviewer
-> Release Engineer
-> CEO
```

Do not treat Game02 as a reskin of Game01.

## 18. Tests required

Add automated Python tests at minimum for:

### Registry
- loads valid AGENTS.json
- duplicate ID rejected
- missing required field rejected
- unknown role rejected

### Backward compatibility
- old Task without agent_role loads
- old Task saves without losing data
- new Task with agent_role loads

### Prompt
- role prompt injected
- HOUSE_RULES preserved
- allowlist preserved
- acceptance preserved

### Routing
- gameplay routes to gameplay_engineer
- UI routes to ui_ux_engineer
- QA routes to qa_engineer
- build routes to release_engineer
- invalid role rejected

### Dependency
- unmet dependency cannot run
- cycle detection in generated plan

### Safety
- allowlist violation still blocks
- no automatic commit/push
- paid API flags are not bypassed
- read-only roles cannot perform production-code writing tasks

### Manager
- one sentence produces structured plan
- invalid manager output rejected
- duplicate IDs prevented

Do not remove or weaken existing tests.

## 19. Documentation

Create `AI_GAME_COMPANY/AGENT_TEAM.md` with:

- organization chart
- 12 roles
- role responsibilities
- owner vs agent_role explanation
- task lifecycle
- handoff format
- CLI usage
- CEO one-sentence examples
- Human Gates
- Game01 validation procedure
- Game02 example
- troubleshooting

## 20. Do not fake capabilities

If Codex cannot run Unity in the current execution environment:
- do not claim compilation success
- do not claim PlayMode success
- do not claim APK success

Leave those as NOT_VERIFIED and use the build PC/orchestrator for evidence.

## 21. Implementation phases

Implement in this order:

```text
PHASE 1  read current architecture and tests
PHASE 2  Agent Registry
PHASE 3  Task optional agent_role compatibility
PHASE 4  role prompt injection
PHASE 5  dispatcher
PHASE 6  structured manager/producer planning
PHASE 7  team CLI
PHASE 8  QA/reviewer/release gates
PHASE 9  dashboard integration
PHASE 10 tests and docs
PHASE 11 Game01 dry/live validation
PHASE 12 prepare Game02 one-sentence workflow
```

Do not skip tests between phases.

## 22. Final implementation report

When finished report exactly:

```text
ANALYSIS
IMPLEMENTED
AGENTS
CHANGED_FILES
TESTS_RUN
TEST_RESULTS
UNITY_VERIFICATION
APK_VERIFICATION
BACKWARD_COMPATIBILITY
KNOWN_LIMITATIONS
NEXT_COMMAND
```

For `NEXT_COMMAND`, provide the exact PowerShell command the user should run on the build PC.

Never claim a test/build result that was not actually observed.

## 23. Start now

Read the repository first.

Then implement the smallest backward-compatible change that makes the 12 roles real and executable.

Do not replace the existing Game Factory.

Do not invent a second task system.

Do not bypass policy, allowlists, review, or build verification.

The final user experience must converge on:

```powershell
cd C:\Dory_tycoon\AI_GAME_COMPANY
python -m company.orchestrator.main team order --game game02 --goal "Game02 만들어."
```

and then the existing orchestrator should move the work through the company departments until a human gate or verified release result is reached.
