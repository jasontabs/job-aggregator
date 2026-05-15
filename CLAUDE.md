# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a Claude Code **Skills** repository for a Job Aggregator agent. Skills are invokable instruction sets that Claude Code loads on demand to handle specialized workflows.

## Skill Structure

Each skill lives in `Skills/<skill-name>/` and contains:

```
Skills/<skill-name>/
├── SKILL.md          # Required: YAML frontmatter (name, description) + instructions
├── evals/
│   └── evals.json    # Test prompts and assertions
├── agents/           # Subagent instruction files (grader.md, comparator.md, analyzer.md)
├── references/       # Documentation loaded into context as needed
├── scripts/          # Reusable executable helpers bundled with the skill
└── assets/           # Templates, icons, or other static files
```

### SKILL.md frontmatter

```yaml
---
name: skill-identifier
description: When to trigger and what the skill does. This is the primary trigger mechanism.
---
```

The `description` field is critical — it controls when Claude invokes the skill. Be specific about trigger contexts. Skills tend to undertrigger, so descriptions should be slightly "pushy" about when to activate.

## Skill Development Workflow

The `skill-generator` skill (`Skills/skill-generator/SKILL.md`) governs how to create and iterate on skills. The core loop:

1. Draft the skill's `SKILL.md`
2. Write test cases in `evals/evals.json`
3. Run test cases with and without the skill (as parallel subagents)
4. Grade assertions, aggregate benchmark, launch `eval-viewer/generate_review.py` for human review
5. Read `feedback.json`, improve the skill, repeat

### Running the eval viewer

```bash
python <skill-creator-path>/eval-viewer/generate_review.py \
  <workspace>/iteration-N \
  --skill-name "my-skill" \
  --benchmark <workspace>/iteration-N/benchmark.json
```

Use `--static <output_path>` in headless/no-display environments.

### Aggregating benchmarks

```bash
python -m scripts.aggregate_benchmark <workspace>/iteration-N --skill-name <name>
```

### Description optimization (triggering accuracy)

```bash
python -m scripts.run_loop \
  --eval-set <path-to-trigger-eval.json> \
  --skill-path <path-to-skill> \
  --model <current-model-id> \
  --max-iterations 5 \
  --verbose
```

### Packaging a skill

```bash
python -m scripts.package_skill <path/to/skill-folder>
```

## Key Conventions

- Keep `SKILL.md` under 500 lines; offload details into `references/` files with clear pointers from the skill body.
- Workspace directories (`<skill-name>-workspace/`) live as siblings to the skill directory, organized by `iteration-1/`, `iteration-2/`, etc.
- Always run both a with-skill and a without-skill (or old-skill) baseline in the same turn — never sequential.
- The `eval_metadata.json` grading format requires fields `text`, `passed`, and `evidence` (not `name`/`met`/`details`).
- Capture `total_tokens` and `duration_ms` from task notifications immediately into `timing.json` — this data is not persisted elsewhere.
