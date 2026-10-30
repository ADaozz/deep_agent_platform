# Thread 文件与沙箱隔离

平台界面继续使用统一的 Agent 设置，不恢复旧版工作区创建、选择或资源聚合界面。每个 Thread 由服务端分配私有 Workspace，关联保存在 `thread_workspace` 记录中。新请求不能指定 Workspace ID、宿主路径或挂载参数。

`WorkspaceResolver` 只解析资源归属和目录，`SandboxPolicyBuilder` 将解析结果转换为原生 `SandboxConfig`。实际进程隔离、文件工具路由、挂载校验和模式切换均由原生 Bubblewrap 后端执行，没有复制 Agent Loop。

## 存储

```text
.platform/
  platform.sqlite
  model-config.key
  configuration/policy.yaml
  workspaces/<workspace_id>/files/
  workspaces/<workspace_id>/artifacts/
  workspaces/<workspace_id>/tmp/
  sessions/<thread_id>/checkpoint.sqlite
  sessions/<thread_id>/checkpoint.attachments/
  sessions/<thread_id>/mcp.json
  sandbox-states/<thread_id>/sandbox/home/
  sandbox-states/<thread_id>/sandbox/tmp/
  skill-snapshots/<thread_id>/
```

Workspace 与 Thread 独立存储：以后由获授权的 Project 绑定器共享 Workspace 时，HOME/TMP、checkpoint 和只读 Skill 快照仍然各自独立。目前没有向用户或模型开放 Project 绑定接口。

Skill 快照保留在独立的 `skill-snapshots` 根目录，而非受保护的 `sessions` 根目录下。这样 full-access 可以遮蔽整个会话控制目录，同时让原生文件工具和进程通过 `/agent-skills` 一致读取只读快照。其真实宿主路径也显式只读挂载，不提供全局可写 `/skills`。

## 策略

可通过服务端 `PLATFORM_POLICY` 指定 YAML；默认读取 `.platform/configuration/policy.yaml`。这不是 内置运行时的 YAML，也不是 Agent 可修改的会话参数。

```yaml
workspace:
  isolation: thread
  root: .platform/workspaces
sandbox:
  default_mode: restricted
  state_scope: thread
  state_root: .platform/sandbox-states
  mounts:
    allow_custom: false
    read_only: []
    read_write: []
  full_access:
    require_confirmation: true
```

相对根目录以数据目录的父目录解析。根目录必须互不包含，不得包含平台控制数据、别名链接或系统运行库。当前版本拒绝自定义挂载及环境变量覆盖；将来开放参考资料挂载时，必须先实现管理员白名单。Builder 验证传入 Context 与持久化关联一致，再调用 内置运行时的挂载校验。

restricted 是新 Thread 的默认模式，只挂载本 Workspace、私有 HOME/TMP、只读运行依赖和本 Thread 的 Skill。Bubblewrap 初始化失败直接失败，绝不改为宿主执行。

full-access 不承诺 Thread 之间的文件隔离。用户必须在运行环境菜单输入 `FULL_ACCESS`，对应 `PUT /api/agent/sessions/<id>/sandbox` 的确认字段；运行中的变更由原生 Runner 在安全边界重建沙箱。重建失败保留原模式并清除待生效标记。会话重启沿用用户明确选择并持久化的模式。数据库、密钥、全局配置、会话控制数据、沙箱状态目录及平台 Skills 源目录由原生保护规则遮蔽；不会遮蔽包含当前 Workspace 的整个 `.platform` 父目录。

工具审批 `ask/allow` 与文件系统模式独立。输入 `ALLOW` 只确认自动批准工具，不会开启 full-access。restricted 下即使 execute 请求联网，也保留独立网络命名空间，不能通过宿主本地 HTTP 服务绕过文件隔离。联网检索使用受控宿主工具；full-access 下才允许原生工具审批开放命令网络。

## HTTP 与宿主工具

上传、列举和下载分别使用：

- `POST /api/agent/sessions/<id>/uploads`
- `GET /api/agent/sessions/<id>/files`
- `GET /api/agent/sessions/<id>/files/<path>`

上传记录绑定 Thread，另一 Thread 不能提交同一上传 ID。旧的全局 `/api/agent/uploads` 与 `/api/agent/files` 不再存在。DOCX/PPTX 服务、版本快照及扫描都从该 Thread 的解析目录出发；相同文档 ID 和操作 ID 在不同 Thread 中互不影响。文件 API 与模板、图片资源检查仍拒绝路径穿越和跨目录软链接。

宿主侧 Office 预览拒绝 OOXML 外部图片、数据和本地文件关系，要求资源嵌入文档；普通 HTTP(S) 超链接可以保留。网络工具只将 HTTP(S) 请求转交已配置的外部服务，不提供宿主文件读取。

通用 MCP 服务运行在宿主或远程，不能凭 Bubblewrap 获得 Thread 文件隔离。当前保留其配置，但不启动尚无 Thread 作用域适配的 MCP 服务，并写入 `resource_policy` 事件；界面说明此限制。以后接入 MCP 需为具体服务实现可验证的作用域适配，不能只添加提示词或假设服务遵守路径参数。

## 新布局要求

不兼容旧共享目录、旧 checkpoint 和旧任务 API，不提供迁移、目录回退、旧 URL 别名或历史任务视图。`/api/tasks` 已删除，执行入口统一为 Thread 会话 API。

初始化会写入 `thread-v1` 布局标记。发现没有标记的旧会话、旧任务、上传记录、旧共享文件目录或不支持的布局版本时，启动直接报错，要求使用新的 `PLATFORM_DATA_DIR`。不会删除、复制或自动导入旧数据。新部署需重新配置模型与 Agent 设置；当前布局的 Thread 仍支持正常 checkpoint 恢复和服务重启。

```bash
PLATFORM_DATA_DIR=.platform-thread .venv/bin/uvicorn platform_app.main:app --host 127.0.0.1 --port 8080
```

## 内核依赖与验证

执行引擎随平台源码保存在 `platform_app/agent_core`，不依赖 CLI 包、CLI 仓库或未合并 PR。只读 Skills 和命令联网策略在这份平台自有源码中维护；原生 Bubblewrap 隔离实现被复用，没有另造 Agent Loop。来源与许可见 [内置运行时](agent-core.md)。

真实 Bubblewrap 回归覆盖 100 个并发解析的 Thread、原生读取/目录/搜索/execute 越界、软链接、HOME/TMP、只读 Skills、非法挂载、full-access 确认和原生模式切换、初始化失败，以及 Office 并发同名文档、当前 Thread 重启恢复和旧布局拒绝。浏览器覆盖私有上传/下载、跨 Thread 下载拒绝、模式确认和执行时间线。



本轮平台完整回归 101 项、前端状态测试 12 项和生产构建通过。Chromium 执行过程/隔离交互 16 个场景通过；带格式 DOCX 为 12 页、112 个版本，PPTX 为 12 页、88 个版本并处理三次运行中反馈，均无页面脚本错误。测试模型是确定性的，视觉审查仍保留 pending，不能将自动检查等同于最终视觉验收。DOCX 分页预览性能数据和未达标项见 [DOCX 实时生成](docx-streaming.md)。
