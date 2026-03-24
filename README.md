# Xray 节点检测系统（FastAPI）

用于批量检测 `vmess / vless / trojan / ss` 节点可用性，支持多用户隔离、订阅拉取、规则导出与管理员后台。

默认访问：`http://<服务器IP>:8088`

## 功能总览
- 节点管理：新增、编辑、删除、批量导入、筛选删除、分页查询。
- 节点检测：单测、全量检测、按筛选条件检测、实时刷新状态与延迟。
- 自动检测：按用户独立配置间隔（分钟/小时/天/周）。
- 订阅导入：支持明文链接、Base64 文本、Clash YAML。
- 订阅源管理：保存多个订阅源，支持手动拉取与后台定时拉取。
- 自建订阅导出：创建导出规则，按 OR 规则筛选节点并生成公开订阅链接。
- 自动拉黑：连续失败 3 次可自动拉黑 3 天（可按用户开关）。
- 管理员后台：用户创建、改角色、重置密码、踢下线、删除用户。
- 黑名单管理页：管理员可恢复被自动拉黑的节点。
- 登录安全：失败次数限制与锁定机制，基于 `HttpOnly` Session Cookie。

## 当前仓库结构

> 当前代码结构为仓库根目录直接运行（`main.py` 在根目录）。

```text
xray_web/
├─ main.py
├─ templates/
├─ static/
├─ tests/
├─ docs/
├─ README.md
├─ Dockerfile
├─ docker-compose.yml
├─ docker-entrypoint.sh
└─ deploy.sh
```

## 数据与运行目录

应用运行时数据目录由 `XRAY_WEB_DATA_DIR` 控制，默认 `/data/xray1`。

典型运行时文件：
- `data.db`：核心数据库（节点、状态、用户、会话、订阅、导出规则、设置）。
- `xray`：Xray 可执行文件。
- `geoip.dat` / `geosite.dat`：Xray 资源文件。
- `vmess.txt` / `nodes.json`：仅用于“数据库为空时”的一次性兼容导入。

## 快速启动（源码方式）

### 1) 依赖
- Python 3.10+（建议 3.12）
- `curl`
- 可执行 `xray` 二进制（以及 `geoip.dat` / `geosite.dat`）

### 2) 安装 Python 依赖
```bash
python -m pip install -U fastapi uvicorn[standard] jinja2 python-multipart pyyaml
```

### 3) 设置环境变量（示例）
Linux/macOS:
```bash
export XRAY_WEB_DATA_DIR=/data/xray1
export XRAY_BIN=/data/xray1/xray
export XRAY_ADMIN_USER=admin
export XRAY_ADMIN_PASS='请改成强密码'
export XRAY_AUTH_SALT='请改成随机字符串'
```

PowerShell:
```powershell
$env:XRAY_WEB_DATA_DIR = "D:\xray1"
$env:XRAY_BIN = "D:\xray1\xray.exe"
$env:XRAY_ADMIN_USER = "admin"
$env:XRAY_ADMIN_PASS = "请改成强密码"
$env:XRAY_AUTH_SALT = "请改成随机字符串"
```

### 4) 启动
```bash
uvicorn main:app --host 0.0.0.0 --port 8088
```

## 自动任务机制（代码行为）

- 后台每 30 秒扫描一次“到期的订阅源”，自动拉取并导入新节点。
- 后台每 30 秒扫描一次“开启自动检测的用户”，到期即触发全量检测。
- 自动拉黑条件：
  - 单节点连续失败 `>= 3` 次。
  - 且用户“自动拉黑”开关为开启。
  - 拉黑后 `disabled=1`，`disabled_reason=blacklist`，默认 3 天后到期（需管理员手动恢复）。
- 被拉黑节点不会进入导出结果（导出时会过滤 `disabled=1`）。

## 自建订阅导出说明

导出规则页面可创建公开链接：`/sub/{token}?format=xxx`

支持格式：
- `clash`
- `v2ray`（内部 `raw`）
- `base64`
- `singbox`
- `raw`（API 可用）

规则语义：
- `rules_json` 为 OR 关系，任意规则命中即纳入导出。
- 规则类型（当前实现）：
  - `success_rate`
  - `latency`
  - `recent_successes`
  - `success_in_last_minutes`

## 安全建议（强烈）

- 部署后立即修改默认管理员密码（默认 `admin/admin123`）。
- 设置随机 `XRAY_AUTH_SALT`，避免不同实例使用同一盐值。
- 仅将服务暴露在可信网络，公网请加 HTTPS 反代和访问控制。
- 仅导入可信订阅地址，避免服务端被滥用于探测内网。
- 定期备份 `data.db` 与 `xray/geoip.dat/geosite.dat`。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `XRAY_WEB_DATA_DIR` | `/data/xray1` | 数据目录 |
| `XRAY_BIN` | `$XRAY_WEB_DATA_DIR/xray` | Xray 可执行文件路径 |
| `XRAY_TEST_URL` | `https://www.google.com/generate_204` | 连通性检测目标 URL |
| `XRAY_TEST_WORKERS` | `8` | 并发检测线程数 |
| `XRAY_TEST_TIMEOUT` | `6` | 单次检测超时（秒） |
| `CURL_BIN` | `curl` | curl 命令路径 |
| `XRAY_ADMIN_USER` | `admin` | 初始管理员用户名（仅用户表为空时生效） |
| `XRAY_ADMIN_PASS` | `admin123` | 初始管理员密码（仅用户表为空时生效） |
| `XRAY_AUTH_SALT` | `xray-web` | 密码哈希盐 |
| `XRAY_SESSION_DAYS` | `7` | Session 有效期（天） |
| `XRAY_LOGIN_MAX_ATTEMPTS` | `5` | 登录窗口内允许失败次数 |
| `XRAY_LOGIN_WINDOW_MIN` | `10` | 登录失败统计窗口（分钟） |
| `XRAY_LOGIN_LOCK_MIN` | `15` | 触发锁定后锁定时长（分钟） |
| `XRAY_AUTO_CHECK_INTERVAL` | `30` | 新用户默认自动检测间隔（分钟） |
| `XRAY_DEFAULT_SUB_INTERVAL_MIN` | `60` | 订阅源默认拉取间隔（分钟） |

## Docker 说明

仓库包含 `Dockerfile` / `docker-compose.yml` / `deploy.sh`。容器启动时若检测不到 `xray`，会自动下载最新 Xray 并放入数据目录。

注意：当前 `Dockerfile` 使用 `COPY app/ /app/`。如果你的源码是当前仓库这种“根目录直接包含 `main.py`”结构，需要先调整 `COPY` 路径再构建。

## 测试

```bash
pytest -q
```

当前仓库已有测试覆盖：
- 订阅表初始化
- 规则导出逻辑（OR 语义）
- 连续失败自动拉黑逻辑
- Clash 订阅解析优先级

## 常见问题

- 无法访问页面：检查监听地址、防火墙、反向代理配置。
- 检测全部失败：优先检查 `xray` 是否可执行、`geoip.dat/geosite.dat` 是否在数据目录。
- 登录总是失败：检查 `XRAY_AUTH_SALT` 是否意外变更、确认用户密码是否被重置。
