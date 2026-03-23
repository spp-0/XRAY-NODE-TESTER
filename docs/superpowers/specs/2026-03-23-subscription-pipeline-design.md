# Multi-Subscription Pipeline & Self-Hosted Feeds Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support multiple subscription sources with scheduled fetch, detection-driven quality scoring, blacklisting, and multi-format self-hosted subscription outputs (Clash-first) per user.

**Architecture:** Add a subscription-ingest scheduler that pulls multiple sources (global default interval with per-sub override), normalizes nodes, applies blacklist filtering, and stores per-user nodes. A detection loop updates node status and triggers blacklist rules. A tokenized public feed endpoint renders filtered nodes in multiple formats (Clash priority). Admin UI manages users, subscriptions, blacklist, and export rules.

**Tech Stack:** FastAPI, SQLite, Xray core, Jinja2, Uvicorn, Python scheduler thread, optional PyYAML for Clash parsing.

---

## 1) Scope & Key Requirements
- Multi subscription sources per user; global default interval + per-sub override.
- Scheduled fetch, parsing for mainstream formats (Clash priority; raw/base64 and Sing-box next).
- Node detection, quality scoring, and OR-based filter rules for export.
- Blacklist strategy: 3 consecutive fails → blacklist 3 days → **no auto-unblacklist**; admin-only manual restore.
- Self-hosted subscription outputs: multiple per user, public token links, multi-format.
- UI updates: subscription management, export rules management, blacklist management, and system defaults.

## 2) Data Model Changes
### New / Extended Tables
**`subscriptions`**
- `id` (PK), `owner`, `name`, `url`, `type`
- `enabled`, `interval_min` (nullable; inherit global default)
- `last_fetch_at`, `last_status`, `last_error`

**`nodes` (extend)**
- `source_sub_id` (nullable)
- `disabled` (0/1), `disabled_reason` (manual/blacklist)
- `blacklist_until` (timestamp)

**`node_status` (extend)**
- `success_rate`, `last_ok`, `last_fail`, `avg_latency`
- `consecutive_fail`

**`export_rules`**
- `id` (PK), `owner`, `name`, `token`, `format`, `enabled`
- `rules_json` (OR-based rule list)
- `created_at`

**`settings` (extend)**
- `default_sub_interval_min`

## 3) Core Flows
### 3.1 Subscription Fetch Scheduler
- Runs every 30s; selects due subscriptions (`enabled=1` and `now >= next_run`).
- Interval = subscription override or global default.
- Fetch URL; parse to node list; normalize.
- Apply blacklist filter: if node id in blacklist and not manually cleared → skip.
- Upsert nodes with `source_sub_id`.

### 3.2 Detection & Blacklist
- After each test, update:
  - `consecutive_fail` and `success_rate` / `avg_latency`.
- If `consecutive_fail >= 3` then:
  - `disabled=1`, `disabled_reason='blacklist'`, `blacklist_until=now+3days`.
- No automatic unblacklist. Admin can manually restore.

### 3.3 Exported Feeds
- Token URL: `/sub/<token>?format=clash|raw|base64|singbox`
- Apply OR rules from `rules_json`.
- Format rendering:
  - Clash YAML (priority) using node mapping
  - Raw link list
  - Base64
  - Sing-box (as available)

## 4) UI Additions
### User UI
- Subscription manager: list/add/edit/disable/pull-now
- Export rules manager: create multiple outputs, choose format, rule builder (OR)
- Auto-detect settings remain

### Admin UI
- User management (existing)
- Subscription oversight (global stats)
- Blacklist management (restore / view)
- System defaults (default_sub_interval_min)

## 5) Rule Builder (OR)
Example rules list:
- `recent_successes >= 3`
- `success_in_last_minutes <= 30`
- `success_rate >= 80%`
- `avg_latency <= 800ms`

Rule evaluation: include node if **any** rule passes.

## 6) Safety & Performance
- Dedup by node fingerprint (hash of raw)
- Limit fetch size & parse timeouts
- Log errors to subscription `last_error`
- Avoid repeated tests for disabled nodes unless manually restored

## 7) Migration Plan
- Add new tables and columns (safe ALTERs).
- Initialize default global interval.
- Migrate existing nodes to `source_sub_id = NULL`.
- Initialize export rules for admin if desired.

## 8) Open Questions
- Sing-box schema breadth (minimal vs full compatibility)
- Clash output completeness (ws/grpc/reality details)

---

## Approval
If this design looks correct, proceed to implementation planning.
