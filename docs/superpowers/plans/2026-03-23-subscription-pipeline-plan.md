# Subscription Pipeline & Self-Hosted Feeds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-subscription ingest with scheduled fetch, blacklist-aware dedup, OR-based export rules, and multi-format public feed outputs (Clash-first), plus admin/user UI to manage subscriptions, export rules, and blacklist.

**Architecture:** Extend the SQLite schema, add subscription scheduler and export rendering endpoints, store per-user export rules, and implement blacklist enforcement on ingest and detection updates. UI adds subscription management, export rules management, and blacklist management under admin. Detection remains via existing xray test pipeline.

**Tech Stack:** FastAPI, SQLite, Uvicorn, Jinja2, Python scheduler thread, PyYAML, pytest.

---

## File Structure (Planned)
**Create:**
- `D:/codex/xray_web/tests/test_subscriptions.py`
- `D:/codex/xray_web/tests/test_export_rules.py`
- `D:/codex/xray_web/tests/test_blacklist.py`
- `D:/codex/xray_web/tests/test_parsers.py`
- `D:/codex/xray_web/templates/subscriptions.html`
- `D:/codex/xray_web/templates/exports.html`
- `D:/codex/xray_web/templates/admin_blacklist.html`

**Modify:**
- `D:/codex/xray_web/main.py`
- `D:/codex/xray_web/templates/index.html`
- `D:/codex/xray_web/static/app.js`
- `D:/codex/xray_web/static/style.css`
- `D:/codex/xray_web/README.md`

---

### Task 1: Add DB schema + migration helpers

**Files:**
- Modify: `D:/codex/xray_web/main.py`
- Test: `D:/codex/xray_web/tests/test_subscriptions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscriptions.py
from main import _init_db
import sqlite3


def test_init_db_creates_subscription_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("XRAY_WEB_DATA_DIR", str(tmp_path))
    _init_db()
    conn = sqlite3.connect(tmp_path / "data.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "subscriptions" in tables
    assert "export_rules" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subscriptions.py::test_init_db_creates_subscription_tables -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# main.py (_init_db)
conn.execute("CREATE TABLE IF NOT EXISTS subscriptions (...)")
conn.execute("CREATE TABLE IF NOT EXISTS export_rules (...)")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_subscriptions.py::test_init_db_creates_subscription_tables -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_subscriptions.py
git commit -m "feat(db): add subscription and export tables"
```

---

### Task 2: Subscription fetch scheduler + parsers

**Files:**
- Modify: `D:/codex/xray_web/main.py`
- Test: `D:/codex/xray_web/tests/test_parsers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parsers.py
from main import _parse_subscription_content


def test_parse_prefers_clash_yaml():
    yaml = """proxies:\n  - name: a\n    type: ss\n    server: 1.1.1.1\n    port: 8388\n    cipher: aes-128-gcm\n    password: p"""
    links = _parse_subscription_content(yaml)
    assert links and links[0].startswith("ss://")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_parsers.py::test_parse_prefers_clash_yaml -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# main.py
# - create subscriptions table
# - add scheduler thread _subscription_fetch_loop()
# - compute next_run based on per-sub interval or global default
# - fetch URL, parse, normalize, upsert with source_sub_id
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_parsers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_parsers.py
git commit -m "feat(subscriptions): add fetch scheduler and parsers"
```

---

### Task 3: Blacklist enforcement (3 fails → 3 days, no auto-unblacklist)

**Files:**
- Modify: `D:/codex/xray_web/main.py`
- Test: `D:/codex/xray_web/tests/test_blacklist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blacklist.py
from main import _update_status, _init_db
import sqlite3


def test_blacklist_after_three_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XRAY_WEB_DATA_DIR", str(tmp_path))
    _init_db()
    owner = "u"
    node_id = "n1"
    for _ in range(3):
        _update_status(owner, node_id, {"success": False, "error": "x"})
    conn = sqlite3.connect(tmp_path / "data.db")
    row = conn.execute("SELECT disabled, disabled_reason FROM nodes WHERE owner=? AND id=?", (owner, node_id)).fetchone()
    assert row[0] == 1 and row[1] == "blacklist"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_blacklist.py::test_blacklist_after_three_fails -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# main.py
# - store consecutive_fail in node_status
# - if >=3, set nodes.disabled=1, disabled_reason='blacklist', blacklist_until=now+3days
# - ingestion skips disabled_reason='blacklist' regardless of blacklist_until
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_blacklist.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_blacklist.py
git commit -m "feat(blacklist): enforce 3-fail blacklist"
```

---

### Task 4: Export rules + public feed endpoints

**Files:**
- Modify: `D:/codex/xray_web/main.py`
- Test: `D:/codex/xray_web/tests/test_export_rules.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export_rules.py
from main import _apply_export_rules


def test_or_rules_pass_any():
    node = {"success_rate": 90, "avg_latency": 900, "recent_successes": 1}
    rules = [{"type":"success_rate","op":">=","value":80}, {"type":"latency","op":"<=","value":800}]
    assert _apply_export_rules(node, rules) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_rules.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# main.py
# - export_rules table CRUD
# - tokenized endpoint /sub/<token>?format=
# - format render: clash/raw/base64/singbox
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_export_rules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_export_rules.py
git commit -m "feat(export): rules and public feed endpoints"
```

---

### Task 5: UI for subscriptions + export rules

**Files:**
- Create: `D:/codex/xray_web/templates/subscriptions.html`
- Create: `D:/codex/xray_web/templates/exports.html`
- Modify: `D:/codex/xray_web/static/app.js`
- Modify: `D:/codex/xray_web/static/style.css`

- [ ] **Step 1: Add templates and basic layout**

```html
<!-- templates/subscriptions.html -->
<h1>订阅管理</h1>
```

- [ ] **Step 2: Wire routes and add JS handlers**

```js
// static/app.js
// add event handlers for subscription CRUD and export rule CRUD
```

- [ ] **Step 3: Manual test**

Run app; verify UI pages render and CRUD flows work.

- [ ] **Step 4: Commit**

```bash
git add templates/subscriptions.html templates/exports.html static/app.js static/style.css
git commit -m "feat(ui): subscription and export rule management"
```

---

### Task 6: Admin blacklist management + system defaults

**Files:**
- Create: `D:/codex/xray_web/templates/admin_blacklist.html`
- Modify: `D:/codex/xray_web/main.py`
- Modify: `D:/codex/xray_web/static/app.js`

- [ ] **Step 1: Add admin routes**

```python
# main.py
# GET /admin/blacklist
# POST /api/admin/blacklist/{node_id}/restore
# POST /api/admin/settings/default-sub-interval
```

- [ ] **Step 2: Manual test**

Verify admin can restore blacklisted nodes.

- [ ] **Step 3: Commit**

```bash
git add main.py templates/admin_blacklist.html static/app.js
git commit -m "feat(admin): blacklist management and defaults"
```

---

### Task 7: Update README and docs

**Files:**
- Modify: `D:/codex/xray_web/README.md`

- [ ] **Step 1: Document subscription manager, export rules, blacklist**
- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update for subscription pipeline"
```

---

## Notes / Constraints
- Dedup uses normalized fingerprint where possible; fallback hash(raw).
- Blacklist requires manual restore even after 3 days.
- Token feeds are public; ensure tokens are unguessable.

---
