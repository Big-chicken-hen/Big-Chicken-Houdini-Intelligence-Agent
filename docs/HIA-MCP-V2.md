# HIA MCP V2

HIA MCP V2 是 Codex 的 Houdini 感知、知识、执行与验证层。Codex 仍是唯一负责理解自然语言/图片、记忆、规划和生成 HOM Python 的智能主体；MCP 只把高信号现场信息交给 Codex，并在当前 Houdini UI 主线程执行它生成的批量脚本。

## 为什么替代 179 工具森林

第三方 `fxhoudinimcp` 1.3.0 暴露 179 个工具，其中大量是 `create_node`、`set_parameter`、`connect_nodes` 一类微操作。复杂网络因此需要很多往返调用，模型还要在大量重叠工具间选择。HIA V2 不复制其实现或模块路径，也不维护旧 HIA MCP 的五节点白名单；它按能力域提供可过滤、分页、批量的语义工具，复杂变更优先一次 `hia_execute_hom` 完成。

HIA V2 不是固定五工具桥，也不是另一个 Agent。工具数量由能力矩阵决定。

## 能力矩阵与首版工具

| 能力域 | 工具 | 首版状态 |
|---|---|---|
| 能力发现 | `hia_search_capabilities` | 已实现 |
| 场景感知 | `hia_context`, `hia_inspect`, `hia_scene_graph` | 已实现 |
| 动态节点知识 | `hia_search_node_types`, `hia_node_help` | 已实现；查询当前安装，无白名单 |
| 几何理解 | `hia_geometry_summary` | 已实现 |
| 材质与渲染理解 | `hia_material_render_summary` | 已实现 |
| Solaris/USD | `hia_solaris_summary` | 已实现 |
| 动画 | `hia_animation_summary` | 已实现 |
| 模拟与缓存理解 | `hia_simulation_summary` | 已实现 |
| 高能力执行 | `hia_execute_hom` | 已实现；一次批量 UI 主线程执行 |
| 调试与验证 | `hia_validate`, `hia_scene_diff` | 已实现 |
| 视觉反馈 | `hia_capture_viewport` | 已实现；显式调用才截图/flipbook |
| 本地帮助 | `hia_local_help_search` | 已实现；SQLite FTS5 增量索引 Houdini、项目与用户授权资料 |
| 长任务 job/status/cancel | 无 | 延后到真实渲染/缓存需求出现；不预建调度平台 |

SOP、OBJ、DOP、LOP/Solaris、VOP/MaterialX、ROP/Karma、CHOP、COP、TOP、动画和常见模拟均通过动态节点查询、通用摘要及 HOM 批量执行覆盖，不需要逐节点包装。

## 数据流

```text
Houdini Panel 输入/图片
  -> Codex app-server（理解、记忆、规划、生成 HOM）
  -> hia_mcp_v2 stdio（MCP initialize/tools/list/tools/call）
  -> 127.0.0.1 随机端口 + 独立 Bearer token
  -> /hia-mcp-v2/v1/execute
  -> hia_mcp_runtime
  -> hdefereval.executeInMainThreadWithResult
  -> 当前 Houdini 场景
```

`hia_execute_hom` 给脚本注入 `hou`、`hia_result`、`hia_changed_paths` 与 `hia_mark_changed(path)`。返回 `ok/result/stdout/warnings/errors/created_or_changed_paths/revision/dirty/diff`。传输超时可以停止等待，但脚本一旦进入不可中断的 HOM 调用就不会伪造强杀。

## 本地知识索引

`hia_local_help_search` 保持原有 `query/sources/offset/limit` 参数兼容，没有新增 MCP 工具。实现改为 Python stdlib `sqlite3` + FTS5 的确定性词法索引，不包含向量、embedding、RAG、第二模型、第二 Agent、常驻服务或调度器。数据库与正文 chunk 仅写入项目内 `.runtime/knowledge/knowledge.sqlite3`，由 `.gitignore` 排除。

索引来源固定为：

- 当前 Houdini 通过 `hou` 读取的完整 node-type catalog 与 `$HH/help` 支持文本；
- 已发布的 `.agents/skills/*/SKILL.md`、其 `references` 文本及少量当前项目文档；
- 用户明确放入 `.runtime/knowledge/sources` 的 TXT、Markdown、HTML、SRT、VTT，以及可选 PDF。

查询只读数据库，不再逐次全盘读取正文。首次查询及保守的 10 分钟自动刷新间隔到期时，才执行基于文件大小、纳秒 mtime 和 SHA-256 的增量更新；用户新增或修改资料需要立即生效时可传 `refresh=true`。文档被切为不超过约 1200 字符的短 chunk，并按 FTS5 词法分数返回 Top-K。SQLite 使用 WAL 支持两个 MCP worker 并发读取，写入用 `BEGIN IMMEDIATE` 单事务串行化；进程间不共享长连接。

每条结果都返回来源路径、URL、作者、访问时间、Houdini 版本、许可、SHA-256、`verification` 与 `evidence`。只有从当前真实 Houdini `hou` 会话读取的 node-type catalog 标为 `verified`；Houdini 帮助、项目文档及用户资料都标为 `unverified`，用户 sidecar 不能把资料提升为已验证。当前 Houdini 版本结果优先，其他版本资料仍可保留并明确标注。

用户资料可用同名 `<文件名>.metadata.json` sidecar 提供 `url`、`author`、`accessed_at`、`houdini_version`、`license` 和 `evidence`。PDF 仅在环境已有可导入的 `pypdf` 时解析；没有可选依赖或解析失败时返回明确 warning，不安装依赖、不阻断其他来源。

## 与第三方完全隔离

- distribution、import package、stdio entry point、server id：`hia_mcp_v2`
- Houdini 侧 package：`hia_mcp_runtime`
- 工具前缀：`hia_`
- 环境变量：仅 `HIA_MCP_V2_*`
- route：`/hia-mcp-v2/v1/execute`
- runtime：`.runtime/hia-mcp-v2`
- bind：仅 `127.0.0.1`；每次 session 默认随机 token、随机端口

HIA V2 不读取 `FXHOUDINIMCP_*`，不注册 `/api`，不使用 `fxhoudinimcp` 或 `fxhoudinimcp_server` 模块路径，也不写入 `.runtime/fxhoudinimcp`。两套代码可同时安装而不争用端口、route、token 或 PID 文件。

## 边界

- JSONL 请求最大 1 MiB，loopback 响应最大 4 MiB，HOM 脚本最大 512 KiB；查询默认分页。
- 缺失/错误 token 分别返回 401/403；traceback 保留有用段落并脱敏 Bearer、token、secret、password、API key 与用户目录。
- `notifications/cancelled` 能在 HTTP 提交/UI 主线程执行前取消；进入 HOM 后不可中断。
- viewport 图像小于内联上限时作为 MCP image 返回，否则只返回 `.runtime/hia-mcp-v2` 下的项目路径。
- Web 研究仍由 Codex 与 `houdini-visual-research` Skill 承担；MCP 不包含爬虫、RAG、Planner 或第二个 Agent。

## 复杂视觉任务的低分辨率审阅闭环

复杂且可见结果占主导的任务必须由 Codex 串联现有能力完成有限审阅闭环，不新增 MCP 工具、服务、调度器、评分系统或第二个 Agent：

1. 在主要结构完成、任务范围内的材质/灯光完成、最终交付前等有意义视觉里程碑，Codex 自动调用现有 `hia_capture_viewport`。相邻或没有可见变化的阶段合并或跳过；Box、单参数修改和普通 HOM 报错等简单任务不截图。
2. 阶段预览使用同帧 `flipbook`，默认 `640 x 360`、`return_image=true`；其他宽高比使用同级受限分辨率。动画和模拟只抽代表帧或关键帧，不为审阅生成连续长序列；任意 `frame_range` 跨度不得超过 240 帧。
3. 图片继续只写 `HIA_CACHE_DIR/screenshots`。捕获不打开 MPlay、不抢焦点，并在成功或失败后恢复原相机、自由视图、相机锁定状态和当前帧。
4. 只读 `houdini-artifact-review` 结合预览以及按需的 `hia_validate`、`hia_scene_diff`，检查比例/轮廓、浮空/穿插、支撑/接触、构图、材质、曝光、透明度和参考一致性。它只返回证据和最低修复建议，不写 HIP。
5. 当前主任务是唯一 HIP writer，每轮只修最高影响的可见区域，再用相同证据复核。迭代预算按任务设为小范围；达到要求立即停止，预算耗尽或无法捕获则明确报告未验证项。

截图清理边界没有扩张：仍只针对 `.runtime/cache/screenshots` 既有范围，不触碰 `previews`、`tmp`、附件或用户最终输出。

## 生产接入

WPF launcher 现在提供互斥 backend 选择：默认 `hia_v2`，手动兼容回退为 `fxhoudini`。选择只保存在 `.runtime/launcher/settings.json`；`scripts/launch-houdini.ps1` 仍是唯一生命周期入口，并在启动子进程前清除继承的两套 backend 环境，只注入所选一套。

`hia_v2` 模式下，Bridge 用 `--strict-config` 注册 server id `hia_mcp_v2`，command 是已验证的绝对 Bridge Python，args 为 `-B -m hia_mcp_v2`，`required=true`，工具 approval 为 `approve`，同时显式禁用项目配置中的 `houdini_intelligence`。Houdini UI-ready 钩子按同一选择启动 `hia_mcp_runtime`；Bridge 通过认证 GET `/hia-mcp-v2/v1/health` 验证 protocol、server id 和 scene revision。Panel 显示 `HIA MCP V2：可用`。回退模式保留锁定的 FXHoudiniMCP 1.3.0 路径和 `/api` health 合同，Panel 显示 `FXHoudiniMCP：回退`。

普通项目 `.codex/config.toml` 仍为 `required=false`，不会在未通过启动器运行 Houdini 时阻断普通 Codex 任务；受控 Houdini session 的 `required=true` 只由 Bridge 进程级 strict config 注入。

固定 Codex 0.144.3 的离线 app-server 握手已验证：`thread/start` 成功，`hia_mcp_v2` 暴露 16 个 `hia_` 工具，禁用的 `houdini_intelligence` 暴露 0 个工具。Codex 的真实 `tools/list` 请求携带标准 `_meta` 对象，stdio adapter 已兼容该形状而未增加工具。

## 真实 Houdini 验收

1. 打开 WPF launcher，选择 **HIA MCP V2（推荐）**，启动 Houdini，确认 Panel 顶部显示 `HIA MCP V2：可用`。
2. 让 Codex 调用 `hia_context`，再用 `hia_search_node_types` 分别动态搜索 box、vellum、mtlx、karma。
3. 在 Houdini 选择一个节点，让 Codex 调用 `hia_inspect` 读取当前选择。
4. 让 Codex 用一次 `hia_execute_hom` 创建一个小型、可编辑资产，再用 `hia_scene_diff` 验证变化。
5. 明确要求视觉核对时调用 `hia_capture_viewport`。
6. 确认该 session 的工具列表没有上游 `create_node`、`set_parameter` 等 179 工具。

本轮不自动启动 Houdini GUI。真实 Houdini 21.x 仍需验证 UI 主线程派发、各 node category 的动态 catalog、MaterialX/Solaris/Karma、viewport/flipbook，以及长 render/cache 的宿主行为。
