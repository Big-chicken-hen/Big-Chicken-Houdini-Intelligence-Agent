# HIA MCP V2

HIA MCP V2 是 Codex 的 Houdini 感知、知识、执行与验证层。Codex 仍是唯一负责理解自然语言/图片、推理、规划、生成记忆正文和生成 HOM Python 的智能主体；MCP 只把高信号现场信息交给 Codex，并在当前 Houdini UI 主线程执行它生成的批量脚本。可选 Qwen encoder 只把文本确定性编码为向量，不生成答案、不写记忆，也不操作 Houdini。

## 为什么替代 179 工具森林

第三方 `fxhoudinimcp` 1.3.0 暴露 179 个工具，其中大量是 `create_node`、`set_parameter`、`connect_nodes` 一类微操作。复杂网络因此需要很多往返调用，模型还要在大量重叠工具间选择。HIA V2 不复制其实现或模块路径，也不维护旧 HIA MCP 的五节点白名单；它按能力域提供可过滤、分页、批量的语义工具，复杂变更优先一次 `hia_execute_hom` 完成。

HIA V2 不是固定五工具桥，也不是另一个 Agent。当前能力矩阵公开 17 个工具。

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
| 本地帮助 | `hia_local_help_search` | 已实现；兼容的 SQLite FTS5 + 可选 Qwen hybrid 检索 |
| 项目记忆 | `hia_project_memory` | 已实现；只允许显式写入、查询、列出、删除或取代 |
| 长任务 job/status/cancel | 无 | 延后到真实渲染/缓存需求出现；不预建调度平台 |

SOP、OBJ、DOP、LOP/Solaris、VOP/MaterialX、ROP/Karma、CHOP、COP、TOP、动画和常见模拟均通过动态节点查询、通用摘要及 HOM 批量执行覆盖，不需要逐节点包装。

## 数据流

```text
Houdini Panel 输入/图片
  -> Codex app-server（理解、推理、规划、生成记忆正文与 HOM）
  -> hia_mcp_v2 stdio（MCP initialize/tools/list/tools/call）
  -> 127.0.0.1 随机端口 + 独立 Bearer token
  -> /hia-mcp-v2/v1/execute
  -> hia_mcp_runtime
     -> hdefereval.executeInMainThreadWithResult -> 当前 Houdini 场景
     -> 独立持久 hia_embedding_worker stdio（仅在显式配置本地 encoder 时）
```

`hia_execute_hom` 给脚本注入 `hou`、`hia_result`、`hia_changed_paths` 与 `hia_mark_changed(path)`。返回 `ok/result/stdout/warnings/errors/created_or_changed_paths/revision/dirty/diff`。传输超时可以停止等待，但脚本一旦进入不可中断的 HOM 调用就不会伪造强杀。

## 本地知识索引

`hia_local_help_search` 保持原有 `query/sources/offset/limit` 参数兼容，并增加 `queries` 批量形状、`mode=lexical|vector|hybrid` 与 `sources=memory`；默认 `hybrid`。SQLite FTS5 是始终可用的确定性基础，可选 Qwen encoder 只增加向量候选。encoder 不参与回答、推理或写入，且不可用时整个检索硬降级为 FTS5，并在结果中返回原因。

索引来源固定为：

- 当前 Houdini 通过 `hou` 读取的完整 node-type catalog 与 `$HH/help` 支持文本；
- 已发布的 `.agents/skills/*/SKILL.md`、其 `references` 文本及少量当前项目文档；
- 用户明确放入 `.runtime/knowledge/sources` 的 TXT、Markdown、HTML、SRT、VTT，以及可选 PDF。

查询不再逐次全盘读取正文。首次查询及保守的 10 分钟自动刷新间隔到期时，才执行基于文件大小、纳秒 mtime 和 chunk SHA-256 的增量更新；用户新增或修改资料需要立即生效时可传 `refresh=true`。删除的来源会同步删除正文、FTS 行与对应向量。模型或维度切换只重建向量层，保留文档正文和 FTS5。

普通 hybrid 查询不会顺便回填全库 backlog，只为本次 lexical candidates 最多补 32 个精确 chunk。向量索引未完成时，每个 query 只在自己的 lexical candidate 文档内做向量重排；没有可信 lexical candidate 时返回 lexical/空结果并公开 partial 原因，不能用按摄入顺序形成的局部向量冒充全库 semantic。只有 `complete=true` 后才启用全局向量排名。`retrieval.vector.index` 返回 `complete/partial`、`vector_chunks`、`total_chunks`、`pending_chunks`、`chunks_indexed_this_call`、`ranking_scope` 和 `partial_reason`。

全量回填与普通查询分离。launcher 使用已验证的 Bridge Python 调用 `python -B -m hia_mcp_runtime.knowledge_index_cli --project-root <root> status|build`；协议为 `hia-knowledge-index-jsonl/1`，逐行输出 `start/progress/completed/error`。每批独立提交，只处理缺失或正文 hash 已变化的 chunk，Ctrl+C 或进程退出后可继续；它不是新的 MCP 工具、常驻服务或调度器。向量排名仍通过 SQLite 流式游标和有界 heap 选取 Top-K，不把全库向量物化进内存。

每条结果都返回来源路径、URL、作者、访问时间、Houdini 版本、许可、SHA-256、`verification` 与 `evidence`。只有从当前真实 Houdini `hou` 会话读取的 node-type catalog 标为 `verified`；Houdini 帮助、项目文档及用户资料都标为 `unverified`，用户 sidecar 不能把资料提升为已验证。当前 Houdini 版本结果优先，其他版本资料仍可保留并明确标注。

用户资料可用同名 `<文件名>.metadata.json` sidecar 提供 `url`、`author`、`accessed_at`、`houdini_version`、`license` 和 `evidence`。PDF 仅在环境已有可导入的 `pypdf` 时解析；没有可选依赖或解析失败时返回明确 warning，不安装依赖、不阻断其他来源。

## 显式项目记忆

`hia_project_memory` 是唯一持久项目记忆入口，没有第二个 memory 工具，也不把聊天、自动 compaction、工具日志或诊断报告自动转成记忆。Codex 必须先形成最终可复用正文，再显式调用写动作。

- actions：`record`、`search`、`list`、`delete`、`supersede`；
- memory types：`decision`、`preference`、`asset`、`lesson`、`workflow`；
- `record` 与 `supersede` 是显式写入，`delete` 是显式遗忘；`search` 与 `list` 只读；
- stable ID 由 runtime 生成，正文、状态、来源 Thread/Turn、tags、scope、FTS 行和可选向量都留在 `.runtime/knowledge/knowledge.sqlite3`；
- `supersede` 保留可审计关系，默认查询不返回已取代记录；记录内容和 provenance 由调用参数决定，不从聊天暗中补全。

记忆搜索同样默认 hybrid，并遵循完全相同的 encoder 状态、渐进补齐和 FTS5 降级规则。

## 可选 encoder 与双 profile

稳定 profile registry 以 `src/hia_core/embedding_contract.py` 为准：

| Profile ID | 官方 model ID | 体量/权重 | 默认/最大维度 |
|---|---|---|---:|
| `qwen3-embedding-0.6b` | `Qwen/Qwen3-Embedding-0.6B` | 约 1.21 GB，BF16 | 1024/1024 |
| `qwen3-embedding-8b` | `Qwen/Qwen3-Embedding-8B` | 约 15.2 GB，BF16 分片 | 1024/4096 |

两者均为 Apache-2.0、32K context、100+ 语言，支持 MRL 和 query instruction。同一时刻只加载一个模型；launcher 不提供维度 UI，普通 profile 默认 1024，只有高级环境配置才显式请求 4096。8B 在 16 GB 显存上可能因运行时开销无法稳定全 GPU 加载，不能承诺可用，也不为此引入量化框架。

MRL 由 worker 在构造 `SentenceTransformer` 时传入 `truncate_dim=profile.dim`，并在该截断维度上执行 normalize；不是取得完整 Python 向量后再 slice。

所选 8B 缺失、显存/内存不足、初始化失败或模型损坏时，只能先降级到**已经安装**且可加载的 0.6B，再降级到 FTS5；所选 0.6B 失败则直接 FTS5。每次降级在 `retrieval.vector` 下返回 `requested_profile`、`active_profile`、`status`、`degraded`、`fallback_reason` 与 `repair`，绝不在 import、搜索或 fallback 时隐式下载。没有量化、reranker 或第三模型。

独立 `hia_embedding_worker` 使用协议 `hia-embedding-stdio/1`，由 `.runtime/toolchains/hia-embedding/venv/Scripts/python.exe` 运行 `python -m hia_embedding_worker`（console entry point 同名）。它是 HIA MCP 生命周期内的单一持久 stdio 子进程，不是网络服务、Agent 或 scheduler，也绝不加载进 Houdini Python/UI 主线程。模型、venv、Hugging Face/Transformers/Torch cache、临时文件、SQLite 正文和向量全部留在 `.runtime`，发行包不包含这些内容。

官方来源：[0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[0.6B files](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/main)、[8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B)、[8B files](https://huggingface.co/Qwen/Qwen3-Embedding-8B/tree/main)。

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
- Web 研究仍由 Codex 与 `houdini-visual-research` Skill 承担；MCP 不包含爬虫、自治 RAG、Planner 或第二个 Agent。

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

固定 Codex 0.144.3 的离线 app-server 握手已验证：`thread/start` 成功；当前 `hia_mcp_v2` registry 暴露 17 个 `hia_` 工具，禁用的 `houdini_intelligence` 暴露 0 个工具。Codex 的真实 `tools/list` 请求携带标准 `_meta` 对象，stdio adapter 已兼容该形状。

## 真实 Houdini 验收

1. 打开 WPF launcher，选择 **HIA MCP V2（推荐）**，启动 Houdini，确认 Panel 顶部显示 `HIA MCP V2：可用`。
2. 让 Codex 调用 `hia_context`，再用 `hia_search_node_types` 分别动态搜索 box、vellum、mtlx、karma。
3. 在 Houdini 选择一个节点，让 Codex 调用 `hia_inspect` 读取当前选择。
4. 让 Codex 用一次 `hia_execute_hom` 创建一个小型、可编辑资产，再用 `hia_scene_diff` 验证变化。
5. 明确要求视觉核对时调用 `hia_capture_viewport`。
6. 确认该 session 的工具列表没有上游 `create_node`、`set_parameter` 等 179 工具。

本轮不自动启动 Houdini GUI。真实 Houdini 21.x 仍需验证 UI 主线程派发、各 node category 的动态 catalog、MaterialX/Solaris/Karma、viewport/flipbook，以及长 render/cache 的宿主行为。
