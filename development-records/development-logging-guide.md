# OperatorOS Development Logging Guide

## Purpose

Use development logs to preserve what changed, why it changed, what problems came up, and what solution was chosen. The log should help someone quickly understand current progress without reading the full git diff.

## Location

Store development records in `development-records/`.

Use a focused file per major workstream. For example:

- `no-docker-sam3-tracking-implementation-log.md`
- `rag-diagram-retrieval-implementation-log.md`
- `frontend-polish-implementation-log.md`

## Entry Format

Each workstream log should be organized by numbered tasks.

Use this structure:

```md
## 1. Implement ABC Feature

### Progress Updates

[YYYY-MM-DD HH:MM TZ]
- Most recent update goes here.
- Include code areas touched, decisions made, test results, or blockers.

[YYYY-MM-DD HH:MM TZ]
- Older update goes here.

### Challenges

[YYYY-MM-DD HH:MM TZ]
- Describe the challenge and why it mattered.

### Solution

[YYYY-MM-DD HH:MM TZ]
- Describe the solution that was chosen and why.
```

## Ordering Rules

- Tasks are numbered in the order they are introduced.
- Within each task, progress updates must be sorted newest first.
- Within each task, challenges must be sorted newest first.
- Within each task, solutions must be sorted newest first.
- Do not use checkbox task lists such as `[ ]` or `[x]`.
- Do not rewrite history unless correcting an obvious factual mistake. Add a newer entry instead.

## What To Include

Good log entries include:

- The feature or bug being worked on
- Files or subsystems touched
- Important implementation choices
- Failed approaches and why they failed
- Test commands and results
- Environment assumptions
- Follow-up risk or known gaps

Keep each bullet factual and concise.

## What To Avoid

- Long copied terminal output
- Vague notes such as "worked on backend"
- Duplicate updates that do not add new information
- Checklist formatting
- Unrelated planning notes that belong in a separate plan document
