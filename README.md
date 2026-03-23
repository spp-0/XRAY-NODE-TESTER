# Xray 节点检测系统（FastAPI）

## 1. 项目概述
本系统用于批量检测各类节点（vmess / vless / trojan / ss）的可用性，提供：
- Web 管理界面（中文）
- 多用户隔离（各自节点互相不可见）
- 批量导入 / 导出 / 订阅导入（明文 / Base64 / Clash）
- 过滤 / 搜索 / 分页 / 分组测试
- 流式更新检测状态
- 登录防爆破
- 自动定时检测（用户自定义：分钟/小时/天/周）
- 管理员后台用户管理

访问地址：`http://<服务器IP>:8088`

---

## 2. 目录结构说明（默认：/data/xray1）
> 实际数据目录由环境变量 `XRAY_WEB_DATA_DIR` 控制；下文以默认值 `/data/xray1` 举例。

```
/data/xray1/
├─ app/                    # FastAPI 应用代码
│  ├─ main.py              # 主程序
│  ├─ templates/           # 页面模板
│  └─ static/              # 静态资源
├─ data.db                 # 主要数据（SQLite：节点/状态/用户/会话/设置）
├─ xray                    # Xray 核心程序
├─ geoip.dat               # Xray 资源
├─ geosite.dat             # Xray 资源
├─ venv/                   # 可选 Python 虚拟环境（当前服务未使用）
├─ vmess.txt               # 兼容迁移：仅数据库为空时用于首次导入
├─ nodes.json              # 兼容迁移：仅数据库为空时用于首次导入
└─ README.md               # 项目说明文档
```

### 数据是否都保存在 data.db？
**是的。当前所有节点、检测状态、用户与会话等都保存在 `data.db`（SQLite）中。**
- 数据库路径：`$XRAY_WEB_DATA_DIR/data.db`（默认 `/data/xray1/data.db`）。

### 旧文件（vmess.txt / nodes.json）说明
- 这两个文件只用于“从旧版本迁移”的**兼容导入**：仅当数据库为空时，程序会自动读取并导入一次。
- 迁移完成后，新增/修改/删除均直接写入 `data.db`，程序不再依赖这两个文件。

### 可清理的文件
- `vmess.txt`、`nodes.json`：确认已迁移导入后，可留作备份或删除
- `venv/`：当前服务使用系统 Python（`/usr/bin/python3`），venv 可保留或删除
- `tmp*.json`：测试时生成的临时配置文件（可删除，会自动再生成）

---

## 3. 运行方式（当前服务器）
服务由 systemd 管理：
```
# 查看状态
systemctl status xray-web.service --no-pager

# 重启
systemctl restart xray-web.service

# 日志
journalctl -u xray-web.service --no-pager -n 100
```

服务配置文件：`/etc/systemd/system/xray-web.service`

---

## 4. 登录与安全

### 安全部署清单（强烈建议）
- **务必修改默认管理员密码**：部署时设置强密码 `XRAY_ADMIN_PASS`（不要使用默认 `admin123`）。
- **务必修改盐值**：设置随机 `XRAY_AUTH_SALT`（避免所有实例使用同一个盐）。
- **建议仅内网访问**：如需公网访问，务必加 HTTPS（反向代理）并限制访问来源。
- **订阅导入有风险**：订阅导入会由服务器去抓取 URL 内容；请只添加可信订阅链接，避免被用于探测内网地址（可在反代/防火墙侧做域名白名单）。
- **备份敏感数据**：`data.db` 包含节点链接与用户信息，备份/迁移时注意权限与加密。

- 默认管理员账号由环境变量控制
- 登录防爆破：
  - 10 分钟内失败 5 次 → 锁定 15 分钟（可调）

### 修改密码
- 登录后右上角点击“修改密码”进入独立页面

---

## 5. 自动检测（定时任务）
- 每用户独立开关
- 可自定义数值 + 单位（分钟/小时/天/周）
- 由后端线程自动触发

---

## 6. 管理员后台
入口：登录后顶部“管理员后台”
功能：
- 创建用户
- 修改角色（admin/user）
- 重置密码
- 踢下线
- 删除用户（含其全部节点）

---

## 7. 配置参数（环境变量）
可在 systemd service 中设置：
```
Environment=XRAY_WEB_DATA_DIR=/data/xray1
Environment=XRAY_BIN=/data/xray1/xray
Environment=XRAY_TEST_WORKERS=8
Environment=XRAY_TEST_TIMEOUT=6
Environment=XRAY_TEST_URL=https://www.google.com/generate_204
Environment=XRAY_ADMIN_USER=admin
Environment=XRAY_ADMIN_PASS=admin123
Environment=XRAY_AUTH_SALT=xray-web
Environment=XRAY_LOGIN_MAX_ATTEMPTS=5
Environment=XRAY_LOGIN_WINDOW_MIN=10
Environment=XRAY_LOGIN_LOCK_MIN=15
Environment=XRAY_AUTO_CHECK_INTERVAL=30
```
修改后：
```
systemctl daemon-reload
systemctl restart xray-web.service
```

---

## 8. 数据备份
建议定期备份：
- `/data/xray1/data.db`
- `/data/xray1/geoip.dat`
- `/data/xray1/geosite.dat`
- `/data/xray1/xray`

---

## 9. Docker 部署
项目目录包含：
- `Dockerfile`
- `docker-compose.yml`
- `docker-entrypoint.sh`
- `deploy.sh`

一键部署：
```
bash deploy.sh
```

容器启动时若未检测到 `xray`，将自动下载最新版本并解压资源文件。

---

## 10. 部署手册（新服务器）
### 10.1 环境要求
- Linux 服务器
- Python 3.10+（建议 3.12）
- `curl`
- 可执行的 `xray` 核心 + `geoip.dat` + `geosite.dat`

### 10.2 部署步骤
1) 创建目录
```
mkdir -p /data/xray1/app
```

2) 上传项目文件
- `app/` 目录（main.py, templates, static）
- `xray`, `geoip.dat`, `geosite.dat`

3) 安装依赖
```
python3 -m pip install -U fastapi uvicorn[standard] jinja2 python-multipart pyyaml
```

4) 创建 systemd 服务
```
cat <<'EOF' > /etc/systemd/system/xray-web.service
[Unit]
Description=Xray Node Tester Web
After=network.target

[Service]
Type=simple
WorkingDirectory=/data/xray1/app
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8088
Environment=XRAY_WEB_DATA_DIR=/data/xray1
Environment=XRAY_BIN=/data/xray1/xray
Environment=XRAY_TEST_WORKERS=8
Environment=XRAY_TEST_TIMEOUT=6
Environment=XRAY_TEST_URL=https://www.google.com/generate_204
Environment=XRAY_ADMIN_USER=admin
Environment=XRAY_ADMIN_PASS=admin123
Environment=XRAY_AUTH_SALT=xray-web
Environment=XRAY_LOGIN_MAX_ATTEMPTS=5
Environment=XRAY_LOGIN_WINDOW_MIN=10
Environment=XRAY_LOGIN_LOCK_MIN=15
Environment=XRAY_AUTO_CHECK_INTERVAL=30
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
```

5) 启动服务
```
systemctl daemon-reload
systemctl enable --now xray-web.service
```

6) 放行端口（示例）
```
firewall-cmd --add-port=8088/tcp --permanent
firewall-cmd --reload
```

7) SELinux 允许端口（如启用）
```
dnf -y install policycoreutils-python-utils
semanage port -a -t http_port_t -p tcp 8088 || semanage port -m -t http_port_t -p tcp 8088
```

---

## 11. 常见问题
- 无法访问：检查防火墙 + SELinux
- 页面无数据：检查 `data.db` 是否存在并可写
- 测试失败：确认 `xray` 可执行、`geoip.dat` / `geosite.dat` 存在

---

如需进一步扩展（验证码、用户分组、节点标签等），可继续完善。
