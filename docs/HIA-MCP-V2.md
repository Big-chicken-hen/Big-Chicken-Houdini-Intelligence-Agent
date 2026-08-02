# HIA MCP V2

HIA MCP V2 是 Codex 的 Houdini 感知、知识、执行与验证层。Codex 仍是唯一负责理解自然语言/图片、推理、规划、生成记忆正文和生成 HOM Python 的智能主体；MCP 只把高信号现场信息交给 Codex，并在当前 Houdini UI 主线程执行它生成的批量脚本。可选 Qwen encoder 只把文本确定性编码为向量，不生成答案、不写记忆，也不操作 Houdini。

## 为什么替代 179 工具森林

第三方 `fxhoudinimcp` 1.3.0 暴露 179 个工具，其中大量是 `create_node`、`set_parameter`、`connect_nodes` 一类微操作。复杂网络因此需要很多往返调用，模型还要在大量重叠工具间选择。HIA V2 不复制其实现或模块路径，也不维护旧 HIA MCP 的五节点白名单；它按能力域提供可过滤、分页、批量的语义工具。复杂资产按语义阶段或连贯子系统使用少量 `hia_execute_hom`，每批回到真实场景与图像审阅后再继续，不能把整件资产塞进一个 HOM 脚本。

HIA V2 不是固定五工具桥，也不是另一个 Agent。当前能力矩阵公开 18 个工具。目录由 stdio 注册表的唯一事实源 `TOOL_SPECS` 派生，不再维护第二份手写工具清单；`hia_search_capabilities` 可检索工具名、能力域、描述、参数名及少量中英文别名，并用 `catalog_health` 报告 registered/catalogued/missing/orphaned。`checkpoint/检查点/备份` 指向 `hia_execute_hom`，`runtime/recovery/恢复` 指向 `hia_context`；空结果明确区分 `NO_MATCH` 与 `CATALOG_INCOMPLETE`。

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
| 通用效果实验 | `hia_run_effect_experiment` | 已实现；临时 baseline + 2–3 candidates、多帧证据与 contact sheet，不评分 |
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

`hia_local_help_search` 保持原有 `query/sources/offset/limit` 参数兼容，并增加 `queries` 批量形状、`mode=lexical|vector|hybrid` 与 `sources=memory`；默认 `hybrid`。已初始化 corpus 的 SQLite FTS5 是可靠的确定性基础；全新项目第一次需要显式 `refresh=true` 或运行 CLI 建立索引，严格只读查询不会暗中创建数据库。可选 Qwen encoder 只增加向量候选，不参与回答、推理或写入；不可用时检索降级为 FTS5，并在结果中返回原因。

索引来源固定为：

- 随 Release 分发的版本化 SideFX 官方工作流知识包：`manifest.json`、`coverage.json`、`sources.json` 与 manifest 声明的全部卡片；
- 随 Release 分发、保持 `community_unverified` 标记的版本化社区教程包；
- 当前 Houdini 通过 `hou` 读取的完整 node-type catalog 与 `$HH/help` 支持文本；
- 已发布的 `.agents/skills/*/SKILL.md`、其 `references` 文本及少量当前项目文档；
- 用户明确选择或放入 `.runtime/knowledge/sources` 的 TXT、Markdown、HTML、SRT、VTT、已有字幕 sidecar 的媒体，以及可选 PDF；
- 用户明确选择的 Thread 中公开 user 文本与 assistant final 文本，以及通过 `hia_project_memory` 明确写入的 active 项目记忆。reasoning、原始工具输出、内部事件和无 transcript 媒体不会被暗中摄入。

内置包首次通过显式 `refresh=true` 或 CLI `bootstrap` 幂等复制到 `.runtime/knowledge/builtin/<pack-id>/<version>-<digest>/`，活动指针只保存项目相对路径。复制和索引均离线执行，包含 coverage/source registry 与每张卡的完整正文；每卡的 `source_ids` 会解析为可追溯的多个 SideFX 官方 URL。包升级只替换独立的 built-in collection，不覆盖用户导入资料或项目记忆，也不依赖开发机已有 `.runtime`。

`refresh=false` 是严格只读查询：索引层不扫描来源文件、不写 SQLite、不补 vector backlog，也不删除或切换已有向量层。这个承诺针对 corpus/index；若查询首次启动可选 embedding worker，其依赖或模型运行时仍可能在项目 `.runtime` 下创建自身缓存文件，不能据此宣称整个进程对文件系统绝对零写。只有显式 `refresh=true` 才按本次 sources 执行一次基于文件大小、纳秒 mtime 与正文 SHA-256 的增量刷新。刷新统计分别报告 `files_scanned`、`inline_records_scanned`、正文变化、metadata 变化、删除/未变数量，以及 refresh reason/requested groups/actual groups；inline catalog 的易变访问时间不参与稳定 metadata hash。模型 revision、维度和 normalized 状态共同参与向量 signature，模型切换只重建向量层，保留正文和 FTS5。

普通 hybrid 查询不会顺便回填全库 backlog；显式刷新时也只允许为本次 lexical candidates 补少量必要向量。向量索引未完成时，`ranking_scope=lexical_candidates`：每个 query 只在自己的词法候选文档内做向量重排；严格 AND 零命中时至多执行一次 OR 放宽，以减少长查询的假零命中，但它不是对零词汇重合内容的全库语义召回。没有可信 lexical candidate 时返回 lexical/空结果并公开 partial 原因，不能用按摄入顺序形成的局部向量冒充全库 semantic。只有 `complete=true` 后才启用全局向量排名。

默认 `response_format=compact`，每条 match 只保留标题、短摘要、来源/URL、Houdini 版本、验证状态和分数；`full`/`diagnostic` 才返回完整 metadata。对官方卡、社区卡、用户资料、显式 Thread 导出和项目记忆，`full` 都按 chunk 顺序重建已索引正文，并同时受单条上限和本次 `max_bytes` 预算约束。full 的分页单位是记录：预算截断正文尾部时返回 `content_truncated=true`，但不提供记录内正文游标；调用方可提高 `max_bytes` 或缩小到单条，`next_offset` 只前进到下一条记录。默认 64 KiB、允许 4–256 KiB 的 `max_bytes` 与 `limit` 共同约束响应，超出时返回 `truncated`。batch 响应只在顶层返回一次公共 retrieval/index 状态，每个 query 保留自己的 `next_offset` 与 matches；只有所有未完成 query 的游标一致时，顶层才兼容性返回公共 `next_offset`。FTS、query encode、vector scan 和 serialization 都有轻量 timing。

`hia_local_help_search` 是唯一通用本地知识检索工具；`source_kinds` 可精确隔离 `builtin_official_workflow`、`community_tutorial`、`user_document`、`user_transcript`、`thread_export` 与 `project_memory`。场景首次写入前的一次相关 batched lookup 由 Codex/AGENTS/Skill 工作流负责，MCP 不在 `hia_execute_hom` 前增加数据库审批、状态机或强制 Gate，也不诱导并行重复搜索。

encoder runtime 与 corpus index 是两个独立状态面：`retrieval.encoder` 说明实际 encoder/profile/model revision/dim/device/ready/degraded/fallback，`index.corpus` 说明 total/vector/pending chunks、complete/partial、ranking scope、global recall 与本次增量数量；其中 `index.corpus.inventory` 还报告 active built-in pack 的 ID/version/digest、card/document/chunk 数，以及用户资料、项目记忆、Houdini help 和项目参考的文档数。encoder ready 不等于 corpus complete，partial corpus 也不宣称全库语义召回。

### 独立知识与记忆 CLI

知识/记忆管理首先是项目相对、可文档化的独立 CLI，不依赖 launcher、Panel 或 Houdini GUI。可在项目根直接运行；如果 `.venv` 尚不存在，请先执行 `powershell -File .\scripts\hia-knowledge.ps1 environment-install`：

```powershell
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl bootstrap
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl status
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl build --batch-size 32
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl sources list
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl sources import --path <local-file>
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl sources delete --source-id <managed-id>
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl sources refresh
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl thread import --thread-id <thread-id> --snapshot-file .runtime/thread-snapshots/<thread-id>.json
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl thread remove --thread-id <thread-id>
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl memory list
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl memory record --memory-type decision --title <title> --body <body>
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl memory delete --memory-id <id>
.\.venv\Scripts\python.exe -B .\houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py --project-root . --format jsonl memory supersede --memory-id <id> --memory-type decision --title <title> --body <body>
```

`bootstrap` 是无 Houdini、无网络的首次安装入口：它校验发布包、复制 active built-in pack 并建立完整 FTS5 正文索引；随后可选的 `build` 才分批补齐向量层。`--format jsonl` 使用 `hia-knowledge-index-jsonl/1` 输出 `start/progress/completed/error`；`--format json` 只输出最终 completed/error 对象。退出码为 0 成功、1 runtime error、2 参数/安全校验错误、3 未找到、130 Ctrl+C。build 每批独立提交，只编码缺失或 signature 已变化的 chunk，终止后可恢复；向量扫描使用 SQLite 流式游标与有界 heap，不物化全库向量。

`sources import` 把用户选中的受支持普通文件复制到 `<project-root>/.runtime/knowledge/sources` 并保留原路径与 SHA-256，不移动或修改原文件；delete 只接受该托管目录内的精确普通文件 ID，并立即清理 document、FTS、chunks 与 vectors。该普通来源入口显式导入媒体时只接受同 basename（或 `<媒体名>.<字幕后缀>`）的 SRT/VTT/TXT sidecar，只托管 transcript 和媒体 provenance，绝不复制大型媒体、下载 ffmpeg 或执行转写；没有 sidecar 返回退出码 3 和 `TRANSCRIPT_REQUIRED`。无 sidecar 的音频或视频必须先显式运行 `assets repair` 准备项目本地 FFmpeg/转写环境，再用 `assets import` 进入带 checkpoint 的媒体管线。文件夹导入继续只扫描文本/PDF，不递归选择媒体。memory 子命令复用 `hia_project_memory` 的同一事务实现，不建立第二份记忆库。

确定性来源适配器只接收调用方显式选择的文件、单个 Thread snapshot 或单条项目记忆；`refresh_explicit_sources` 将其规范记录事务化写入同一 SQLite/FTS/vector 层。托管字幕在 CLI 中保持 `user_transcript` 身份；Thread message key 在正文修改时保持稳定，显式删除会同步移除 FTS chunks 与 vectors。

Thread snapshot 固定为项目根内、非 reparse、大小不超过 4 MiB 的普通 JSON 文件，schema 为 `hia-thread-snapshot/1`，顶层只允许 `schema/thread_id/messages`。`thread import` 要求参数和文件内 `thread_id` 一致，并以本次已投影消息完整替换该 Thread：只保留公开 `user` 与 `assistant` final（包括真实 `phase=final_answer`），排除 commentary、reasoning、tool 和内部事件；消失的 message key 在同一事务内删除。`thread remove` 删除该 Thread 的全部 document/chunks/FTS/vectors，但不删除 snapshot 文件。稳定 JSON/JSONL 结果包含 `status/thread_id/source_kind`、接收/索引/排除数量、`refresh` 与 `retrieval`；launcher helper 只做同约束转发：

```powershell
.\scripts\hia-knowledge.ps1 thread-import -ThreadId <thread-id> -SnapshotFile .runtime/thread-snapshots/<thread-id>.json
.\scripts\hia-knowledge.ps1 thread-remove -ThreadId <thread-id>
```

CLI 的 `--project-root` 可由 `HIA_PROJECT_ROOT` 替代，省略两者时从源码位置推导，所有默认路径均由该 root 生成而非写死盘符。`status` 分开返回 index、embedding 和 runtime：实际 worker Python/venv 是否存在、所选 device、运行时已证明的 CUDA availability（未证明时为 `null/not_reported`）、profile/model/model path、SQLite index path，以及 ready/degraded/fallback 原因；Python、model 和 device 可由现有 `HIA_EMBEDDING_*` 环境覆盖。

迁移说明：旧版“首次查询或每 10 分钟自动刷新”行为已经废止；调用方需要更新资料时显式传 `refresh=true` 或调用 `sources refresh`。旧的已配置运行环境仍可用 `python -m hia_mcp_runtime.knowledge_index_cli status|build`，但完整、无 launcher 依赖的当前契约是上述项目相对 CLI 及其全部子命令。

`compact` 结果只返回标题、短摘要、来源/URL、Houdini 版本、验证状态和分数；`full`/`diagnostic` 才返回来源路径、作者、访问时间、许可、SHA-256、`verification` 与 `evidence` 等完整 provenance。只有从当前真实 Houdini `hou` 会话读取的 node-type catalog 标为 `verified`；社区教程固定为 `community_unverified`，用户资料、显式 Thread 导出与项目记忆固定为 `user_supplied_unverified`，sidecar 不能提升其可信度。当前 Houdini 版本结果优先，其他版本资料仍可保留并明确标注。

用户资料可用同名 `<文件名>.metadata.json` sidecar 提供 `url`、`author`、`accessed_at`、`houdini_version`、`license` 和 `evidence`。PDF 仅在环境已有可导入的 `pypdf` 时解析；没有可选依赖或解析失败时返回明确 warning，不安装依赖、不阻断其他来源。

## 显式项目记忆

`hia_project_memory` 是唯一持久项目记忆入口，没有第二个 memory 工具，也不把聊天、自动 compaction、工具日志或诊断报告自动转成记忆。Codex 必须先形成最终可复用正文，再显式调用写动作。

- actions：`record`、`search`、`list`、`delete`、`supersede`；
- memory types：`decision`、`preference`、`asset`、`lesson`、`workflow`；
- `record` 与 `supersede` 是显式写入，`delete` 是显式遗忘；`search` 与 `list` 只读；
- stable ID 由 runtime 生成，正文、状态、来源 Thread/Turn、tags、scope、FTS 行和可选向量都留在 `.runtime/knowledge/knowledge.sqlite3`；
- `supersede` 保留可审计关系，默认查询不返回已取代记录；记录内容和 provenance 由调用参数决定，不从聊天暗中补全。

`list` 与 Panel 列表默认只显示 `scope=project`；该范围为空不代表其他 scope 的记忆被删除或丢失。查看其他 scope 时必须显式指定对应 scope。

记忆搜索同样默认 hybrid，并遵循相同的 encoder/corpus 状态、partial-index 排名保护和 FTS5 降级规则；只读搜索不补 vector backlog。

## 可选 encoder 与双 profile

稳定 profile registry 以 `src/hia_core/embedding_contract.py` 为准：

| Profile ID | 官方 model ID | 体量/权重 | 默认/最大维度 |
|---|---|---|---:|
| `qwen3-embedding-0.6b` | `Qwen/Qwen3-Embedding-0.6B` | 约 1.21 GB，BF16 | 1024/1024 |
| `qwen3-embedding-8b` | `Qwen/Qwen3-Embedding-8B` | 约 15.2 GB，BF16 分片 | 1024/4096 |

两者均为 Apache-2.0、32K context、100+ 语言，支持 MRL 和 query instruction。同一时刻只加载一个模型；launcher 不提供维度 UI，普通 profile 默认 1024，只有高级环境配置才显式请求 4096。8B 在 16 GB 显存上可能因运行时开销无法稳定全 GPU 加载，不能承诺可用，也不为此引入量化框架。

MRL 由 worker 在构造 `SentenceTransformer` 时传入 `truncate_dim=profile.dim`，并在该截断维度上执行 normalize；不是取得完整 Python 向量后再 slice。

所选 8B 缺失、显存/内存不足、初始化失败或模型损坏时，只能先降级到**已经安装**且可加载的 0.6B，再降级到 FTS5；所选 0.6B 失败则直接 FTS5。公共搜索结果以 `retrieval.encoder` 报告 requested/active profile、model、device、ready/degraded/fallback/repair，以 `index.corpus` 报告向量完整度与召回范围；底层 `retrieval.vector` 仅保留兼容摘要。import、搜索或 fallback 绝不隐式下载，也没有量化、reranker 或第三模型。

独立 `hia_embedding_worker` 使用协议 `hia-embedding-stdio/1`，由项目根 `.venv/Scripts/python.exe` 运行 `python -m hia_embedding_worker`（console entry point 同名）。它是 HIA MCP 生命周期内的单一持久 stdio 子进程，不是网络服务、Agent 或 scheduler，也绝不加载进 Houdini Python/UI 主线程。`.venv` 是 Bridge、本地知识解析器和 worker 共用的唯一 HIA managed environment；Houdini embedded Python/hython 与 FXHoudiniMCP fallback venv 不会激活、合并或安装到它。managed CPython base、uv、模型、Hugging Face/Transformers/Torch cache、临时文件、SQLite 正文和向量仍全部留在 `.runtime`。`.venv` 与 `.runtime` 都不进入发行包；旧 `.runtime/toolchains/hia-embedding/venv` 仅作为事务迁移 source，在新 `.venv` 完整验证并再次确认后才可能安全清理，失败时保持原样。

官方来源：[0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[0.6B files](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/main)、[8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B)、[8B files](https://huggingface.co/Qwen/Qwen3-Embedding-8B/tree/main)。

## 与第三方完全隔离

- distribution、import package、stdio entry point、server id：`hia_mcp_v2`
- Houdini 侧 package：`hia_mcp_runtime`
- 工具前缀：`hia_`
- transport 隔离变量：`HIA_MCP_V2_*`；共享 project/cache/embedding 分别使用已文档化的 `HIA_PROJECT_ROOT`、`HIA_CACHE_DIR` 与 `HIA_EMBEDDING_*`
- route：`/hia-mcp-v2/v1/execute`
- runtime：`.runtime/hia-mcp-v2`
- bind：仅 `127.0.0.1`；每次 session 默认随机 token、随机端口

HIA V2 不读取 `FXHOUDINIMCP_*`，不注册 `/api`，不使用 `fxhoudinimcp` 或 `fxhoudinimcp_server` 模块路径，也不写入 `.runtime/fxhoudinimcp`。两套代码可同时安装而不争用端口、route、token 或 PID 文件。

历史文档或测试报告中的旧五节点白名单、旧 `houdini_intelligence` backend 名称和盘符绝对路径示例只用于解释迁移，不代表当前配置。当前 HIA backend 是 `hia_mcp_v2`，路径从 project root/环境推导；FXHoudiniMCP 只保留为明确选择的兼容回退。

## 边界

- JSONL 请求最大 1 MiB，loopback 响应最大 4 MiB，HOM 脚本最大 512 KiB；查询默认分页。
- 缺失/错误 token 分别返回 401/403；traceback 保留有用段落并脱敏 Bearer、token、secret、password、API key 与用户目录。
- `notifications/cancelled` 能在 HTTP 提交/UI 主线程执行前取消；进入 HOM 后不可中断。
- viewport 图像小于内联上限时作为 MCP image 返回，否则返回实际本地路径。真实已保存且父目录安全可写的当前 HIP 使用 `<hip-parent>/.hia/screenshots`；未保存或路径不安全时回退 `HIA_CACHE_DIR/screenshots`。结果始终给出 `storage_scope=hip|runtime_fallback` 与绝对路径。
- Web 研究仍由 Codex 与 `houdini-visual-research` Skill 承担；MCP 不包含爬虫、自治 RAG、Planner 或第二个 Agent。

## 复杂视觉任务的低分辨率审阅闭环

复杂且可见结果占主导的任务由 Codex 串联现有能力完成有限审阅闭环。普通里程碑继续复用 `hia_capture_viewport`、`hia_validate` 与 `hia_scene_diff`；只有需要主观候选对比的 Full 任务才使用唯一的 `hia_run_effect_experiment`。该工具只执行临时 baseline/candidate 参数实验并返回多帧事实与 contact sheet，不评分、不生成 EffectSpec，也不新增服务、调度器或第二个 Agent：

1. 在主要结构完成、任务范围内的材质/灯光完成、最终交付前等有意义视觉里程碑，Codex 自动调用现有 `hia_capture_viewport`。相邻或没有可见变化的阶段合并或跳过；Box、单参数修改和普通 HOM 报错等简单任务不截图。
2. 阶段预览使用同帧 `flipbook` 与 `return_image=true`。省略宽高时从 live viewport 推导；只给一边时保留 viewport 纵横比；两边都给时精确采用请求尺寸。动画和模拟只抽代表帧或关键帧，不为审阅生成连续长序列；任意 `frame_range` 跨度不得超过 240 帧。
3. 图片在安全的已保存 HIP 下写入同级唯一 `.hia/screenshots`，否则写入 `HIA_CACHE_DIR/screenshots`。路径每次调用重新读取，不缓存首次 HIP，因此 Save As 后自然切换。捕获不打开 MPlay、不抢焦点，并在成功或失败后恢复原相机、自由视图、相机锁定状态和当前帧。
4. 只读 `houdini-artifact-review` 结合预览以及按需的 `hia_validate`、`hia_scene_diff`，检查比例/轮廓、浮空/穿插、支撑/接触、构图、材质、曝光、透明度和参考一致性。它只返回证据和最低修复建议，不写 HIP。
5. 当前主任务是唯一 HIP writer，每轮只修最高影响的可见区域，再用相同证据复核。迭代预算按任务设为小范围；达到要求立即停止，预算耗尽或无法捕获则明确报告未验证项。

截图清理边界没有扩张：仍只针对 `.runtime/cache/screenshots` fallback，不扫描或清理 HIP-local `.hia`，也不触碰 `previews`、`tmp`、附件或用户最终输出。带 `checkpoint_label` 的 AI Goal stage checkpoint 同样优先写 `<hip-parent>/.hia/checkpoints`；当前 launcher session 目录只保留绑定 session/Thread/Goal/保存 HIP 的受验证指针，供既有崩溃恢复链发现。失败或不安全时仍使用 session checkpoint 目录。

## 生产接入

WPF launcher 现在提供互斥 backend 选择：默认 `hia_v2`，手动兼容回退为 `fxhoudini`。选择只保存在 `.runtime/launcher/settings.json`；`scripts/launch-houdini.ps1` 仍是唯一生命周期入口，并在启动子进程前清除继承的两套 backend 环境，只注入所选一套。

`hia_v2` 模式下，Bridge 用 `--strict-config` 注册 server id `hia_mcp_v2`，command 是已验证的绝对 Bridge Python，args 为 `-B -m hia_mcp_v2`，`required=true`，工具 approval 为 `approve`，同时显式禁用项目配置中的 `houdini_intelligence`。Houdini UI-ready 钩子按同一选择启动 `hia_mcp_runtime`；Bridge 通过认证 GET `/hia-mcp-v2/v1/health` 验证 protocol、server id 和 scene revision。Panel 显示 `HIA MCP V2：可用`。回退模式保留锁定的 FXHoudiniMCP 1.3.0 路径和 `/api` health 合同，Panel 显示 `FXHoudiniMCP：回退`。

普通项目 `.codex/config.toml` 仍为 `required=false`，不会在未通过启动器运行 Houdini 时阻断普通 Codex 任务；受控 Houdini session 的 `required=true` 只由 Bridge 进程级 strict config 注入。

固定 Codex 0.144.3 的离线 app-server 握手已验证：`thread/start` 成功；当前 `hia_mcp_v2` registry 暴露 18 个 `hia_` 工具，禁用的 `houdini_intelligence` 暴露 0 个工具。Codex 的真实 `tools/list` 请求携带标准 `_meta` 对象，stdio adapter 已兼容该形状。

## 真实 Houdini 验收

1. 打开 WPF launcher，选择 **HIA MCP V2（推荐）**，启动 Houdini，确认 Panel 顶部显示 `HIA MCP V2：可用`。
2. 让 Codex 调用 `hia_context`，再用 `hia_search_node_types` 分别动态搜索 box、vellum、mtlx、karma。
3. 在 Houdini 选择一个节点，让 Codex 调用 `hia_inspect` 读取当前选择。
4. 让 Codex 用一个有界 `hia_execute_hom` 批次创建小型、可编辑资产；复杂资产则按主形体、结构、细节和材质分批，并在批次之间用真实图像及 `hia_scene_diff` 验证。
5. 明确要求视觉核对时调用 `hia_capture_viewport`。
6. 确认该 session 的工具列表没有上游 `create_node`、`set_parameter` 等 179 工具。

本轮不自动启动 Houdini GUI。真实 Houdini 21.x 仍需验证 UI 主线程派发、各 node category 的动态 catalog、MaterialX/Solaris/Karma、viewport/flipbook，以及长 render/cache 的宿主行为。

## 任务专用 Context Pack 与专业验证基础

`hia_context` 可按 `task`、当前选择和 `change_scope` 生成任务专用 Context Pack。场景实体先在 Houdini UI 主线程做紧凑快照，随后只执行一次串行、批量的缓存 FTS5 查询；最多 4 个知识查询，整个 Pack 受 4–32 KiB UTF-8 字节预算约束，并为实时场景、知识命中和当前 runtime 证据标明来源。

`hia_validate` 继续是同一个工具，统一返回 `node_errors`、`empty_output`、`critical_paths`、`geometry_summary`、`changed_scope`，并在调用方显式提供 `semantic_checks` 时增加 `semantic_expectations`。语义检查最多 32 条，只表达通用、可观察的存在性、有限/非零样本、标量或向量 magnitude 范围，以及源字段到目标字段和禁止目标的映射契约；每项最多读取 256 个有界样本且只返回统计，不硬编码 Pyro/FEM 规则或返回大数组。无法在 `cook=false` 下安全取得当前 geometry 时返回 `unknown`/`not_proven`，不隐式 cook。空间相交检查仅保留扩展边界，本版没有引入 BVH、SDF、评分系统或新的验证工具。

`cook=false` 不显式 cook，且遇到仍需 cook 的 SOP 时不读取 geometry；OBJ inspect 也不会调用 `ObjNode.geometry()`。显式 `paths` 不再因同时提供 `root_path` 而展开整棵子树，root-only 检查受 `limit` 约束。`empty_output` 只检查显式目标或 display/render/`OUT_*` 最终输出职责，部分有效的 geometry 统计标为 `partial`；重复的 `Cooking was interrupted` 只保留一个根因摘要、代表路径和受影响数量。Context Pack 的 recent evidence 同样只公开少量代表路径、真实路径总数和检查摘要，避免路径明细挤占字节预算。

Cook/cache 证据按 target 和 frame 记录 `needsToCook()`、`isTimeDependent(for_last_cook=True)`、`cookCount()`、`lastCookTime()` 的可用前后值，以及 cook-start、out-of-date、cache-hit、dependency-invalidation 和 reset 是否真实可观察。`needsToCook()` 只证明 out-of-date，不会被升级为 dependency-invalidation 的因果证据；后者没有直接 HOM 事件时保持 `not_proven`。只有 `cookCount()` 明确递增才报告 `recompute_verified`；需要 cook 却选择 `cook=false` 时报告 `stale_cache_risk`，其余情况为 `recompute_not_proven`。`changed_scope` 同样区分 `observed_no_out_of_scope_change`、`scope_not_observable` 与 `scope_violation`：不可完整观察是 notice，越界、语义失败和 stale risk 才进入显著 warning/error。

`hia_context(include_runtime_capabilities=true)` 返回 `hia-runtime-capabilities/1`，绑定当前 Houdini build，对 HIA 实际依赖的少量 HOM 方法分别报告 `documented`、`callable`、`probe_status` 与脱敏 error。`cook()`、`geometry()` 和体积采样只检查是否 callable，不在 capability probe 中调用。依据为 SideFX 当前官方 [`hou.OpNode`](https://www.sidefx.com/docs/houdini/hom/hou/OpNode.html)、[`hou.Geometry`](https://www.sidefx.com/docs/houdini/hom/hou/Geometry.html)、[`hou.Volume`](https://www.sidefx.com/docs/houdini/hom/hou/Volume.html) 与 [`hou.VDB`](https://www.sidefx.com/docs/houdini/hom/hou/VDB.html) 文档；文档存在不会被当作当前 build 中可调用的证明。

`hia_execute_hom` 的原始 `script` 仍是唯一必填写入接口；可选 `task`、`mutable_root`、`protected_paths`、`expected_outputs`、`checks` 与同一份 `semantic_checks` 只是轻量执行 envelope。它复用定向 Scene Diff 和上述检查输出前后证据，但不是 HOM 沙箱，也不会把脚本转换为 IR。`expected_outputs` 只隐式补目标存在性和节点错误检查；`empty_output`、`geometry_summary`、语义检查以及依赖新鲜输出的 cook 必须由调用方显式请求，`fresh_validation=false` 不会再偷偷追加或执行这些检查。`hia_context(include_context_pack=false)` 会明确关闭 Context Pack 和知识检索，即使同一次调用还带有 `task`、`change_scope` 或 `knowledge_queries`。

每批写入位于一个 Houdini Undo group 内，但只在真实 HOM 异常，或已观察到的显式验证、scope、删除契约失败时请求 Undo；`unknown`、`partial` 与 `NO_OBSERVED_EFFECT` 会保持失败或未证明状态，不会触发整批回滚。调用方读取 `rollback.status` 与错误中的 `automatic_retry_safe`：只有 Undo 栈和 HIP dirty 状态都恢复到批处理前，回滚才会报告 `rolled_back`；随后只有 `automatic_retry_safe=true` 才允许一次修正后的有界重试。Undo 已撤销临时节点但 dirty 状态未恢复时会返回 `DIRTY_STATE_NOT_RESTORED` 和 `not_proven`，不会虚报无残留。未证明回滚、超时和可能存在外部文件/render 副作用时必须先检查实际场景。`protected_paths` 比较持久节点类型、参数、flag 与拓扑，不把切帧造成的 cook 计数、缓存或求值结果变化误判为场景写入。

运行时身份绑定 launcher session、Houdini PID 和已加载的 executor 路径，并报告 HIP、scene revision 及 executor 源码 loaded/disk mtime。launcher session、Houdini 进程或已加载 executor 路径不一致时，读写工具都在 dispatch 前硬拒绝，避免从错误会话取得证据或修改场景；正常重连或重启后再调用，不做热重载。仅当会话、进程和已加载模块均一致，而磁盘源码 mtime 更新时，继续调用 Houdini 中已加载的 executor，并返回 `restart_required=false` 的 advisory。HIP 路径和 revision 是状态证据，不会因为正常打开另一份 HIP 而永久锁死写入。

每次 HOM 执行返回后，runtime 在 `.runtime/hia-mcp-v2/execution-traces/<session>.jsonl` 追加一条不超过 64 KiB 的机器事实：脚本 SHA-256、前后 revision、观察到的路径、check 状态、错误 code 与阶段耗时。Trace 不保存脚本正文、task 正文、聊天、stdout、结果、traceback、凭据，也不会自动写项目记忆或晋升为知识。协议工具为 18 个、Houdini runtime 工具为 17 个；本次只新增唯一通用 `hia_run_effect_experiment`，没有新增服务。
