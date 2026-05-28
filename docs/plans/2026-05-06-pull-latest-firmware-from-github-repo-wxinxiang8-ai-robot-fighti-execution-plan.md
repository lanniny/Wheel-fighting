# Pull latest firmware from GitHub repo wxinxiang8-ai/robot-fighting and adapt-mer...

## Execution Summary
Governed runtime execution plan for `vibe` in mode interactive_governed.

## Frozen Inputs
- Requirement doc: G:\tasks\机器人创新设计大赛\docs\requirements\2026-05-06-pull-latest-firmware-from-github-repo-wxinxiang8-ai-robot-fighti.md
- Runtime input packet: G:\tasks\机器人创新设计大赛\outputs\runtime\vibe-sessions\20260506T130720Z-24f1cdc8\runtime-input-packet.json
- Source task: Pull latest firmware from GitHub repo wxinxiang8-ai/robot-fighting and adapt-mer... Deliverable: Governed implementation artifacts, verification evidence, and cleanup receipts. Constraints: Do not bypass the fixed six-stage governed runtime.; Do not widen scope silently beyond the frozen requirement document.. Update: User approved the frozen execution plan. Now execute Wave 1 in the host conversation: clone upstream wxinxiang8-ai/robot-fighting into external/, produce upstream-diff and vision-code-review reports under docs/reviews/, and run an SSH sanity probe on Cubie 192.168.225.215. Wave 2 (parallel lanes for adapt-merge, vision patches, training pipeline, NPU deploy, UART v2) follows. Final phase_cleanup will be invoked after wave verification.

- Governance scope: root
- Root run id: 20260506T130720Z-24f1cdc8
- Entry intent: vibe
- Requested stop stage: phase_cleanup
- Requested grade floor: none
- Frozen route pack: orchestration-core
- Frozen route skill: vibe
- Frozen route mode: pack_overlay
- Router/runtime skill mismatch: False
- Execution topology companion: G:\tasks\机器人创新设计大赛\outputs\runtime\vibe-sessions\20260506T130720Z-24f1cdc8\execution-topology.json
## Anti-Proxy-Goal-Drift Controls
Prefill from the frozen requirement doc where available. Only diverge with explicit justification.

### Primary Objective
Pull latest firmware from GitHub repo wxinxiang8-ai/robot-fighting and adapt-mer...

### Non-Objective Proxy Signals
- single sample pass only
- current test green only
- demo success only

### Validation Material Role
validation_only

### Declared Tier
Tier C

### Intended Scope
scenario_specific

### Abstraction Layer Target
_author_to_declare_

### Completion State Target
partial

### Generalization Evidence Plan
- Reuse the requirement-declared proof boundary as the starting point.
- cases: []
- note: add independent evidence before generalized completion claims

## Internal Grade Decision
- Grade: XL
- User-facing runtime remains fixed; grade is internal only.
- `vibe` remains the governor and final authority for execution flow.

## Wave Plan
- Wave 1: skeleton, intent freeze, and requirement validation
- Wave 2: implementation decomposition and bounded ownership assignment
- Wave 3: verification, reconciliation, and cleanup handoff

## Delivery Acceptance Plan
- Freeze downstream product acceptance inside the governed requirement doc and reuse it rather than inventing closeout claims later.
- Emit a per-run delivery-acceptance report during `phase_cleanup` so runtime/process success is kept separate from project-delivery success.
- Delivery-acceptance report: G:\tasks\机器人创新设计大赛\outputs\runtime\vibe-sessions\20260506T130720Z-24f1cdc8\delivery-acceptance-report.json
- If manual spot checks are declared in the requirement doc, final completion wording stays blocked until they are cleared or explicitly downgraded to manual review.
- Release truth aggregation remains an outer-layer gate; this run emits the per-run delivery-truth report only.

## Artifact Review Strategy
- If the frozen requirement doc declares `Artifact Review Requirements`, execution must leave behind explicit artifact-review evidence rather than relying on generic completion wording.
- Artifact review may be recorded inline in `phase-execute.json` or through a dedicated `artifact-review.json` sidecar, but one of those governed surfaces must exist when direct artifact review is required.
- Product acceptance stays blocked when required artifact review remains missing, partial, degraded, or manual-review-only.

## Code Task TDD Evidence Plan
TDD mode is not_applicable for this plan; do not block execution on red/green evidence unless a later host/runtime decision explicitly freezes code-task TDD obligations.

## Baseline Document Quality Mapping
- Use the frozen `Baseline Document Quality Dimensions` section in the requirement doc as the authoritative list of document-artifact quality dimensions that artifact review must cover before a document delivery can claim full completion.
- Track each baseline document dimension through artifact-review annotations so the delivery-acceptance report can show which structure, formatting, completeness, reference integrity, layout stability, and output fidelity expectations were inspected.
- Treat missing document-dimension coverage as a manual-review-required hit and keep this mapping separate from UI baselines and code-task TDD evidence.

## Baseline UI Quality Mapping
- Use the frozen `Baseline UI Quality Dimensions` section in the requirement doc as the authoritative list of dimensions that artifact review must cover before a UI delivery can claim full completion.
- Track each baseline dimension through execution and artifact-review annotations so the delivery-acceptance report can show which structure, interaction, state, consistency, responsiveness, and fidelity expectations were inspected.
- Treat missing dimension coverage as a manual-review-required hit and include explicit mapping steps or targeted verification units that drive reviewers to capture the evidence the requirement doc established.

## Task-Specific Acceptance Mapping
- Reuse frozen task-specific acceptance extensions from the requirement doc instead of inventing late closeout criteria.
- Keep base delivery truth separate from task-specific expectations so each can be inspected independently during review.

## Research Augmentation Plan
- Preserve any frozen research augmentation sources from the requirement doc so later reviewers can tell which external standards strengthened the brief.
- Research augmentation may strengthen rough asks, but it must not replace the user-owned requirement surface.

## Execution Topology Snapshot
- Delegation mode: selective_parallel_child_lanes
- Review mode: checkpoint_per_step
- Specialist execution mode: native_bounded_units
- Max parallel units: 2
- Wave `wave-1` has 4 executable step(s).
  Step `wave-1-parallel` -> mode `bounded_parallel`, units `2`.
  Step `wave-1-specialist-post_execution-group-1-parallel` -> mode `bounded_parallel`, units `1`.
  Step `wave-1-specialist-verification-group-1-serial-1` -> mode `sequential`, units `1`.
  Step `wave-1-specialist-verification-group-1-serial-2` -> mode `sequential`, units `1`.

## Specialist Decision Plan
- The governed runtime must keep one explicit specialist decision surface from freeze through delivery acceptance.
- Frozen decision state: approved_dispatch
- Frozen resolution mode: approved_dispatch
- Frozen decision notes: Bounded specialist recommendations were surfaced and promoted into effective approved dispatch.

## Specialist Skill Dispatch Plan
- Specialist routing is mandatory and bounded inside governed `vibe`; it does not transfer runtime authority away from vibe.
- This section lists only effective approved dispatch; non-adopted router candidates and local suggestions remain packet/audit data, not user-facing execution requirements.
- Before specialist execution starts, governed `vibe` emits one unified disclosure for the effective `approved_dispatch` set using each skill's real `native_skill_entrypoint`.
- Each specialist must be invoked through its native workflow, input contract, and validation style.
- Specialist outputs remain subordinate to the frozen requirement and the governed plan.
- Dispatch code-review as specialist_assist.
  Binding profile: verification; dispatch phase: verification; lane policy: serial; parallel in XL: False
  Write scope: specialist:verification; review mode: checkpoint_after_step; execution priority: 90
  Reason: top ranked specialist candidate from pack 'code-quality' via keyword_ranked
  Required inputs: bounded specialist subtask contract, frozen requirement context, relevant source files or domain artifacts
  Expected outputs: bounded specialist findings or code changes, verification notes aligned with the specialist skill
  Verification: Preserve the specialist skill's native workflow, boundaries, and validation style.
- Dispatch peer-review as specialist_assist.
  Binding profile: verification; dispatch phase: verification; lane policy: serial; parallel in XL: False
  Write scope: specialist:verification; review mode: checkpoint_after_step; execution priority: 90
  Reason: top ranked specialist candidate from pack 'science-peer-review' via fallback_task_default
  Required inputs: bounded specialist subtask contract, frozen requirement context, relevant source files or domain artifacts
  Expected outputs: bounded specialist findings or code changes, verification notes aligned with the specialist skill
  Verification: Preserve the specialist skill's native workflow, boundaries, and validation style.
- Dispatch documentation-lookup as specialist_assist.
  Binding profile: deliverable; dispatch phase: post_execution; lane policy: bounded_parallel; parallel in XL: True
  Write scope: specialist:deliverable:documentation-lookup; review mode: checkpoint_after_step; execution priority: 70
  Reason: overlay recommendation from 'retrieval_advice'
  Required inputs: bounded specialist subtask contract, frozen requirement context, relevant source files or domain artifacts
  Expected outputs: bounded specialist findings or code changes, verification notes aligned with the specialist skill
  Verification: Preserve the specialist skill's native workflow, boundaries, and validation style.

## Specialist Consultation
These are specialists resolved for plan-time handling under governed `vibe` before this execution plan was frozen. Depending on policy, they may be consulted live or routed for direct current-session loading.
- Consulted Skill: code-review
  Why now: top ranked specialist candidate from pack 'code-quality' via keyword_ranked
  Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\code-review\SKILL.runtime-mirror.md
- Consulted Skill: peer-review
  Why now: top ranked specialist candidate from pack 'science-peer-review' via fallback_task_default
  Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\peer-review\SKILL.runtime-mirror.md
- Consulted Skill: documentation-lookup
  Why now: overlay recommendation from 'retrieval_advice'
  Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\documentation-lookup\SKILL.runtime-mirror.md

## Unified Specialist Lifecycle Disclosure This unified disclosure keeps routing truth, consultation truth, and execution truth separate while showing one user-readable specialist timeline.  ### discussion_routing - Skill: code-review   State: routed   Why now: top ranked specialist candidate from pack 'code-quality' via keyword_ranked   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\code-review\SKILL.runtime-mirror.md - Skill: peer-review   State: routed   Why now: top ranked specialist candidate from pack 'science-peer-review' via fallback_task_default   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\peer-review\SKILL.runtime-mirror.md - Skill: documentation-lookup   State: routed   Why now: overlay recommendation from 'retrieval_advice'   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\documentation-lookup\SKILL.runtime-mirror.md  ### discussion_consultation - Skill: code-review   State: routed_pending_current_session   Why now: top ranked specialist candidate from pack 'code-quality' via keyword_ranked   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\code-review\SKILL.runtime-mirror.md - Skill: peer-review   State: routed_pending_current_session   Why now: top ranked specialist candidate from pack 'science-peer-review' via fallback_task_default   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\peer-review\SKILL.runtime-mirror.md - Skill: documentation-lookup   State: routed_pending_current_session   Why now: overlay recommendation from 'retrieval_advice'   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\documentation-lookup\SKILL.runtime-mirror.md  ### planning_consultation - Skill: code-review   State: routed_pending_current_session   Why now: top ranked specialist candidate from pack 'code-quality' via keyword_ranked   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\code-review\SKILL.runtime-mirror.md - Skill: peer-review   State: routed_pending_current_session   Why now: top ranked specialist candidate from pack 'science-peer-review' via fallback_task_default   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\peer-review\SKILL.runtime-mirror.md - Skill: documentation-lookup   State: routed_pending_current_session   Why now: overlay recommendation from 'retrieval_advice'   Loaded from: C:\Users\16643\.claude\skills\vibe\bundled\skills\documentation-lookup\SKILL.runtime-mirror.md

## Completion Language Rules
- Do not report runtime completion as downstream project delivery unless the delivery-acceptance report returns `PASS`.
- `completed_with_failures`, degraded execution, or pending manual actions must downgrade completion wording.
- Child-governed completion remains local-scope only and cannot justify root-level completion language.

## Ownership Boundaries
- One owner per artifact set.
- Parallel work must use disjoint write scopes.
- Subagent prompts must end with `$vibe`.
- Specialist help stays bounded and native-mode; it must not become a second planner or a second runtime.

## Verification Commands
- Run targeted repo verification for changed surfaces.
- Run runtime contract gate before claiming completion.
- Review the delivery-acceptance report emitted during `phase_cleanup` before using full completion language.
- Re-run mirror sync and parity validation before release claims.

## Rollback Plan
- Revert only the governed-runtime change set if verification fails.
- Do not roll back unrelated user changes.

## Phase Cleanup Contract
- Remove temp artifacts created by the wave.
- Run node audit and cleanup when needed.
- Write cleanup receipt before completion.
