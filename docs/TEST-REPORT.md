# 开发与测试报告

本报告记录本轮 `feature/panel-ui` 开发与测试中实际观察到的问题。已解决条目不会删除；同一根因再次出现时更新出现次数与证据。

> 本文件是追加式历史记录。各节中的“当前”、发行包版本和测试计数只描述该节
> 当时的工作树，不代表最新发行候选；发布前必须以最新提交上的 source preflight、
> 定向/完整测试和新构建归档为准，不复用旧 `.runtime/release` ZIP 或校验文件。

## 问题记录

### TR-001：真实 Houdini GUI 验收等待人工执行

- 时间：2026-07-18 13:23:40 +08:00
- 阶段：初始审计
- 命令或操作：计划验证 Panel 是否仍会抢占 Houdini 视口键盘焦点
- 预期：在真实 Houdini GUI 中完成附件对话框、发送、流式输出与关闭 Panel 的焦点验收
- 实际：本轮明确禁止启动真实 Houdini GUI，离线测试不能证明宿主窗口的最终焦点行为
- 原始证据：任务要求“不要启动真实 Houdini GUI”；同时要求无法离线证明焦点时记录为等待真实 GUI 验收
- 分类：跳过 / 人工验收
- 处理：保留为真实阻断，不以单元测试冒充 GUI 结果
- 临时方案：实现与运行离线定向测试；最终提供一次简短人工验收步骤
- 状态：未解决，等待真实 GUI 验收
- 出现次数：1

### TR-002：初次协议契约路径假设错误

- 时间：2026-07-18 13:26:10 +08:00
- 阶段：初始只读审计
- 命令或操作：读取 `contracts/codex-app-server-protocol.json`，并并行读取 Bridge 事件、协议、README 与仓库规则
- 预期：读取现有 Codex app-server 协议契约及相邻文件
- 实际：目标契约路径不存在，PowerShell 以退出码 1 结束；同批并行输出未返回
- 原始证据：`Get-Content : 找不到路径“contracts\codex-app-server-protocol.json”，因为该路径不存在。`；`Exit code: 1`
- 分类：路径假设 / 命令失败
- 处理：先记录失败，再用只读文件名搜索定位真实契约；其余文件改为独立读取
- 临时方案：不再假设契约位置，使用排除 `.agents/` 的 `rg --files`
- 状态：已解决；真实文件位于 `contracts/codex-app-server/0.144.3/`
- 出现次数：1

### TR-003：并行文档测试审计查询以退出码 1 结束

- 时间：2026-07-18 13:27:37 +08:00
- 阶段：文档与测试只读审计
- 命令或操作：并行读取 `test_p1_assets.py` 指定行，并分别用 `rg` 查询文档/diagnostics 引用和 compaction/requestUserInput 引用
- 预期：三项只读查询均返回审计结果
- 实际：并行调用整体以退出码 1 结束且未返回 stdout，无法从该批结果判断是哪一个 `rg` 无匹配
- 原始证据：`Script failed`；`Exit code: 1`；无 stdout。完整查询范围为 `tests/unit/test_p1_assets.py` 236–385、890–925 行，以及 `tests/unit` 内文档、diagnostics、compaction 与 requestUserInput 关键词
- 分类：命令非零 / 并行审计查询
- 处理：先记录；将查询拆开，允许“无匹配”作为正常审计结果，不修改产品代码
- 临时方案：使用独立命令和已确认存在的显式文件
- 状态：已解决；第 3 条 compaction/requestUserInput 查询无匹配，`rg` 按约定返回 1
- 出现次数：2；第二次为读取 closeEvent 测试后查询 `attachment_dialog_finished|deleteLater` 无匹配，第一段输出有效、第二个 `rg` 按约定返回 1

### TR-004：诊断集成点搜索包含不存在的 launcher 目录

- 时间：2026-07-18 13:27:41 +08:00
- 阶段：运行时诊断只读审计
- 命令或操作：`rg -n --glob '!*.pyc' "HIA_PROJECT_ROOT|PROJECT_ROOT|project_root" houdini_package/python_libs/hia_panel services/bridge/hia_bridge launcher tests/unit`
- 预期：在现有 Panel、Bridge、launcher 与测试路径中定位项目根来源
- 实际：已有目录命中正常，但仓库没有顶层 `launcher` 目录，命令最终退出码为 1
- 原始证据：`rg: launcher: 系统找不到指定的文件。 (os error 2)`
- 分类：路径假设 / 命令失败
- 处理：确认不是代码回归；后续只查询已确认存在的显式路径
- 临时方案：不重试不存在的目录
- 状态：已解决
- 出现次数：1

### TR-005：协议目录中没有 contextCompaction 文本匹配

- 时间：2026-07-18 13:28:45 +08:00
- 阶段：自动上下文整理只读审计
- 命令或操作：先读取 `protocol-inventory.json` 845–870 行，再执行 `rg -n "contextCompaction|ContextCompaction" contracts/codex-app-server/0.144.3`
- 预期：确认 `thread/compacted` 与兼容 `contextCompaction` 的协议命名位置
- 实际：版本化契约确认包含 `thread/compacted` / `ContextCompactedNotification`；随后关键词搜索无匹配并按 `rg` 约定以退出码 1 结束
- 原始证据：协议 inventory 第 858 行为 `thread/compacted`、第 859 行为 `ContextCompactedNotification`；`rg` 无 stdout、退出码 1
- 分类：搜索无匹配 / 兼容事件确认
- 处理：确认当前协议只注册 `thread/compacted` 通知；Panel 将 `contextCompaction` 作为已接收 item 类型兼容显示，不修改协议白名单
- 临时方案：不重试无匹配搜索
- 状态：已解决
- 出现次数：1

### TR-006：PowerShell 默认读取中文时终端显示乱码

- 时间：2026-07-18 13:29:30 +08:00
- 阶段：文档与测试只读审计
- 命令或操作：用未显式指定编码的 `Get-Content` 查看 `docs/TEST-REPORT.md` 与包含中文的测试源码
- 预期：终端正确显示 UTF-8 中文
- 实际：终端输出出现 mojibake；字节头与显式 UTF-8 读取均确认仓库文件内容正常
- 原始证据：原始字节头为 `23 20 E5 BC 80...`；使用 `UTF8Encoding` 读取可正确得到 `# 开发与测试报告`
- 分类：终端编码 / 显示问题
- 处理：不修改文件编码；后续中文审计统一显式使用 UTF-8
- 临时方案：`Get-Content -Encoding utf8` 或显式 `UTF8Encoding`
- 状态：已解决，有稳定 workaround
- 出现次数：1

### TR-007：Panel 定时器合并补丁上下文不匹配

- 时间：2026-07-18 13:37:50 +08:00
- 阶段：焦点与生命周期实现
- 命令或操作：用一个 `apply_patch` 同时替换 heartbeat、scene work 与 event poll 的三个 `QTimer.singleShot` 调度点
- 预期：三个调度方法改用 Panel 持有的 single-shot timer
- 实际：补丁无法匹配 `_schedule_scene_work_poll` 的预期上下文并被原子拒绝；文件未发生该批修改
- 原始证据：`apply_patch verification failed: Failed to find expected lines ... def _schedule_scene_work_poll(self, delay_ms: int)`
- 分类：补丁上下文 / 编辑失败
- 处理：先记录失败，再显式读取三个方法的当前代码并拆分为小补丁
- 临时方案：保持原文件不变直至精确补丁应用
- 状态：已解决；读取精确上下文后，三个调度点已用拆分后的匹配补丁更新
- 出现次数：1

### TR-008：状态同步失败分支引用了不存在的 TurnPhase 成员

- 时间：2026-07-18 14:40:43 +08:00
- 阶段：运行时问题报告集成自审
- 命令或操作：只读检查 `PanelTurnState` 的实际阶段枚举与 `mark_start_uncertain()` 实现
- 预期：新增诊断分支使用现有的“不确定/同步中”阶段
- 实际：新增代码引用了不存在的 `TurnPhase.START_UNKNOWN`；真实枚举用 `TurnPhase.RECONCILING` 表示该状态
- 原始证据：`TurnPhase` 仅含 `IDLE`、`STARTING`、`IN_PROGRESS`、`RECONCILING`；`mark_start_uncertain()` 将阶段改为 `RECONCILING`
- 分类：实现错误 / 状态枚举
- 处理：先记录，再将条件改为 `RECONCILING` 且要求 `turn_id is None`
- 临时方案：在修正前不运行该分支测试
- 状态：已解决；条件已改为现有 `TurnPhase.RECONCILING` 且仅在 `turn_id` 未确认时记录
- 出现次数：1

### TR-009：Git diff 检查提示工作区换行符将转为 CRLF

- 时间：2026-07-18 14:44:36 +08:00
- 阶段：中途差异检查
- 命令或操作：`git status --short --branch`、`git diff --stat` 与 `git diff --check`
- 预期：确认修改范围且无空白错误
- 实际：命令成功、`git diff --check` 未报告空白错误，但 Git 对若干已修改文本文件提示下次写入时 LF 将替换为 CRLF
- 原始证据：`warning: in the working copy of '... ', LF will be replaced by CRLF the next time Git touches it`；退出码 0
- 分类：Git 换行提示 / 非失败警告
- 处理：不批量改写或规范化用户工作树；保持当前内容与 Git 配置，最终再次检查实际 diff
- 临时方案：不运行会机械重写全文件换行的格式化操作
- 状态：已确认，无代码回归
- 出现次数：7 次差异检查命令，共出现 68 条文件提示；最终 `git diff --check` 新增 20 条相同提示且退出码为 0

### TR-010：旧附件对话框延迟销毁可能清除新对话框引用

- 时间：2026-07-18 14:45:56 +08:00
- 阶段：附件对话框生命周期自审
- 命令或操作：只读检查 `finished`、`deleteLater` 与 `destroyed` 的引用释放顺序
- 预期：只释放实际关闭的对话框引用，不影响随后新开的对话框
- 实际：`_attachment_dialog_destroyed()` 无条件置空；若旧对象延迟销毁发生在新对话框创建后，可能误清除新引用
- 原始证据：当前实现为 `def _attachment_dialog_destroyed(...): self._attachment_dialog = None`
- 分类：生命周期竞态 / 实现错误
- 处理：先记录，再仅当 destroyed 信号对象仍是当前保存对象时置空
- 临时方案：修正前避免快速连续重开附件对话框
- 状态：已解决；destroyed 处理仅在信号对象仍等于当前保存对象时释放引用
- 出现次数：1

### TR-011：切换 Thread 后手动报告可能沿用上一轮诊断键

- 时间：2026-07-18 14:48:34 +08:00
- 阶段：每 Turn 报告归属自审
- 命令或操作：对照现有 Thread 切换分支检查 `_diagnostic_turn_key` 生命周期
- 预期：切换到不同 Thread 后，新的手动记录不会追加到上一 Thread 的报告
- 实际：附件与流关联会清空，但新增诊断上下文尚未同步清空，可能沿用上一轮 key
- 原始证据：`_apply_session()` 的 Thread 变化分支仅清附件和 stream IDs；诊断 key/snapshot 未重置
- 分类：报告归属 / 生命周期错误
- 处理：先记录，再增加一个简单诊断上下文清理方法并在两个 Thread 切换入口调用
- 临时方案：修正前不要在切换 Thread 后立即用手动记录按钮
- 状态：已解决；两个不同 Thread 的切换入口现在同时清空当前诊断 key、draft、snapshot 与工具/错误缓存
- 出现次数：1

### TR-012：developerInstructions 超出现有 512 字符测试边界

- 时间：2026-07-18 14:53:50 +08:00
- 阶段：Bridge 指令静态一致性检查
- 命令或操作：用 Python AST 读取 `session.py` 中合并后的 `developerInstructions` 常量并计算长度
- 预期：保留新路由规则，同时满足现有测试的 512 字符上限
- 实际：合并后长度为 613，若直接运行定向测试会失败
- 原始证据：命令输出首行为 `613`
- 分类：指令长度 / 预期测试失败
- 处理：先记录，再压缩重复措辞并同步直接相关测试断言，不删除实时 MCP/HOM、显式离线、源码读取边界、自动整理和 request_user_input 规则
- 临时方案：修正并重新计数后再运行测试
- 状态：已解决；压缩后为 479 字符，保留全部要求的通用运行时规则并低于 512 字符边界
- 出现次数：1

### TR-013：首次 developerInstructions 长度复核脚本使用了错误常量名

- 时间：2026-07-18 15:07:00 +08:00
- 阶段：Bridge 指令静态一致性复核
- 命令或操作：用 Python AST 查找模块级 `_DEVELOPER_INSTRUCTIONS` 后读取长度
- 预期：输出当前 `developerInstructions` 文本及字符数
- 实际：`session.py` 没有该模块级常量，结果列表为空并触发 `IndexError: list index out of range`
- 原始证据：命令退出码 1；异常为 `IndexError: list index out of range`
- 分类：检查脚本错误 / 非产品失败
- 处理：先记录失败，再按实际 `thread/start` 参数赋值结构读取字符串
- 临时方案：无；只读脚本未修改任何文件
- 状态：已解决；按字典键 `developerInstructions` 读取后成功得到 479 字符
- 出现次数：1

### TR-014：首轮定向测试有六项直接相关断言未同步

- 时间：2026-07-18 15:10:00 +08:00
- 阶段：首轮定向测试
- 命令或操作：`python -m unittest -v tests.unit.test_p1_assets tests.unit.test_panel_wiring tests.unit.test_conversation_tool_activity tests.unit.test_panel_attachments tests.unit.test_panel_network_response tests.unit.test_bridge_client_reply tests.unit.test_bridge_session tests.unit.test_codex_protocol_contract tests.unit.test_runtime_diagnostics`
- 预期：全部直接相关测试通过
- 实际：运行 142 项，136 项通过，5 项失败、1 项错误
- 原始证据：`test_panel_wires_authoritative_terminal_and_bounded_reconciliation` 查找已变化的源码子串时报 `ValueError: substring not found`；四个 B2 Panel 测试仍断言旧标签 `Revision：7/8`，实际为 `场景版本：7/8  ·  未保存：不可用`；Bridge 指令测试仍断言 `普通 Houdini 场景请求...`，实际压缩文本为 `普通场景请求...`
- 分类：直接相关测试断言未同步 / 非运行时产品失败
- 处理：先记录完整失败，再仅更新上述源码定位与显示/提示词断言；随后只重跑失败测试模块
- 临时方案：无；其他 136 项定向测试结果有效
- 状态：已解决；仅同步六处直接相关断言，随后六项逐项重跑全部通过
- 出现次数：1

### TR-015：并行审查状态查询使用了过短等待时间

- 时间：2026-07-18 15:12:00 +08:00
- 阶段：并行只读终审协调
- 命令或操作：等待审查代理更新，传入 `timeout_ms=1000`
- 预期：快速获取当前审查状态
- 实际：工具在执行前拒绝参数，要求 `timeout_ms` 至少为 10000
- 原始证据：`timeout_ms must be at least 10000`
- 分类：编排参数错误 / 未执行
- 处理：先记录，再使用允许的最短 10000 毫秒等待
- 临时方案：无；未触碰项目文件或运行时
- 状态：已解决；改用 10000 毫秒后调用被正常接受
- 出现次数：1

### TR-016：首次合规等待未收到并行审查更新

- 时间：2026-07-18 15:13:00 +08:00
- 阶段：并行只读终审协调
- 命令或操作：以 `timeout_ms=10000` 等待任一审查代理更新
- 预期：至少一个只读审查完成或发回进度
- 实际：10 秒窗口内没有新消息，等待正常超时
- 原始证据：`Wait timed out.`
- 分类：协调等待超时 / 非测试失败
- 处理：先记录，不重复阻塞等待；改用非阻塞状态列表并继续本地主线检查
- 临时方案：本地主线继续，审查结果稍后收取
- 状态：已确认，无项目影响
- 出现次数：1

### TR-017：工作期间出现来源不明的未跟踪 launcher 文件

- 时间：2026-07-18 15:16:00 +08:00
- 阶段：并行范围终审
- 命令或操作：只读 `git diff --name-status` 与工作树范围核对
- 预期：仅保留初始未跟踪 `.agents/` 与本任务创建的诊断文件
- 实际：审查代理观察到额外未跟踪 `scripts/launcher/HiaLauncher.Core.psm1`（43,403 bytes），它不在本任务修改清单中且仓库搜索不到引用
- 原始证据：并行只读审查状态消息；代理明确未读取、未修改该文件
- 分类：并发工作树变化 / 用户或其他任务文件
- 处理：完整保留，不读取、不删除、不移动、不覆盖、不暂存；后续由同文件中的独立 `LCH-*` 记录确认它属于并行启动器任务
- 临时方案：本任务仅报告其存在，不把它计入实现成果
- 状态：已解决；归属已由并行启动器任务确认，本任务仍不读取或改动该文件
- 出现次数：1

### TR-018：最终只读 UI 审查发现非模态调用与诊断归属竞态

- 时间：2026-07-18 15:20:00 +08:00
- 阶段：Panel 最终只读审查
- 命令或操作：逐分支审查附件 Dialog 与 Turn ACK/通知的诊断绑定顺序
- 预期：附件选择器真正非模态；只有通过当前 Turn 校验的 ACK/通知才能绑定诊断 Thread/Turn；取消 Thread 选择后不沿用旧报告
- 实际：`QDialog.open()` 属于异步模态对话框入口，即使先设 `NonModal` 也不能保证非模态；两个路径在 token/`observe_started()` 校验前调用 `_bind_diagnostic_turn()`；进入无 Thread 状态时未清理诊断上下文
- 原始证据：`panel.py` 约第 976、1863、2126、856 行的当前实现；`test_p1_assets.py` 还断言了 `dialog.open()`
- 分类：UI 模态语义 / 迟到事件归属竞态
- 处理：先记录，再将 Dialog 启动改为 `show()`；仅在 ACK/started 被当前状态接受后绑定；无 Thread 分支调用现有诊断清理方法；只更新直接相关测试
- 临时方案：修正前不进行真实 Houdini GUI 验收
- 状态：已解决；Dialog 改用 `show()`，诊断绑定移到当前状态接受之后，无 Thread 时清理上下文；直接测试通过
- 出现次数：1

### TR-019：最终诊断审查发现终态、重启、脱敏与场景变化边界

- 时间：2026-07-18 15:22:00 +08:00
- 阶段：运行时 diagnostics 最终只读审查
- 命令或操作：对照任务的终态触发、每 Turn 合并、脱敏和场景变化要求审查 Panel 与 writer
- 预期：warning 只随最终失败合并；凭据形式全部脱敏；场景变化不由既有 Dirty 状态误判；恢复中的 Turn 仍可记录；同一 Turn 不重复建报告
- 实际：warning 只显示未进入最终失败报告；`API key: sk-...`、裸 `sk-*` 与 Basic-auth URL 不在现有脱敏覆盖；revision 不可用时当前 Dirty 被直接当作本 Turn 修改；恢复活动 Turn 时空诊断快照会漏记。审查还指出 pre-Turn 图片/选择失败立即落盘后可能与随后成功 Turn 的“成功不写”语义冲突，以及 writer 的进程内 key 映射无法跨 Panel 重启合并既有文件
- 原始证据：`panel.py` 的 `_remember_codex_error()`、`_refresh_diagnostic_scene_result()`、`_bind_diagnostic_turn()`、`_record_pre_turn_issue()`；`runtime_diagnostics.py` 的脱敏正则与进程内 `_paths`
- 分类：诊断证据完整性 / 脱敏缺口 / 生命周期边界
- 处理：先记录；最小修正 warning 合并、凭据脱敏、初始 scene revision/dirty 比较和活动 Turn 恢复。pre-Turn 明确失败仍按任务列出的图片复制/选择失败自动报告保留；跨 Panel 进程追溯不新增索引或数据库，改用确定性文件指纹复用的最小方式评估后实现或明确限制
- 临时方案：修正前不声称 diagnostics 已覆盖上述边界
- 状态：已解决；warning 仅随最终报告合并，补齐 Basic URL、Authorization scheme、空格 API key 与裸 `sk-*` 脱敏，保存初始 scene revision/dirty，恢复活动 Turn 时重建快照，并在项目 diagnostics 内按整行 Thread/Turn 复用既有报告；未新增索引、数据库或服务。pre-Turn 图片/选择失败按任务明确列出的自动触发保留并在随后 Turn 中复用同一报告
- 出现次数：1

### TR-020：UI 终审组合补丁因上下文不匹配被原子拒绝

- 时间：2026-07-18 15:24:00 +08:00
- 阶段：TR-018 最小修正
- 命令或操作：一次补丁同时修改 Dialog、无 Thread 清理、ACK/started 绑定顺序和源码测试
- 预期：四组直接相关改动一次应用
- 实际：`apply_patch` 无法匹配无 Thread 分支的预期上下文并拒绝整份补丁
- 原始证据：`apply_patch verification failed: Failed to find expected lines ... elif state_applied and not self._turn_state.busy`
- 分类：编辑上下文不匹配 / 原子拒绝
- 处理：先记录，读取当前精确行后拆分为小补丁
- 临时方案：原子拒绝保证产品文件未部分修改
- 状态：已解决；拆分后的代码和测试补丁均已应用，相关定向测试通过
- 出现次数：2；第二次为组合测试补丁的 `test_session_connection_snapshot_updates_compact_codex_status` 上下文未命中，同样原子拒绝

### TR-021：宽泛的出现次数补丁误改了 TR-001

- 时间：2026-07-18 15:27:00 +08:00
- 阶段：开发测试报告维护
- 命令或操作：用未带条目标题上下文的补丁把首个 `出现次数：1` 改为 2
- 预期：更新 TR-020 的第二次补丁上下文失败
- 实际：补丁成功但命中了文件最前面的 TR-001，导致该条出现次数暂时写入了无关证据
- 原始证据：`rg` 显示错误文本位于第 19 行，TR-020 仍为 `出现次数：1`
- 分类：报告编辑定位错误
- 处理：先保留本条记录，再以条目标题和完整邻近文本恢复 TR-001、更新 TR-020
- 临时方案：在修正前不使用该两项次数做最终汇总
- 状态：已解决；TR-001 已恢复为 1 次，TR-020 已准确更新为 2 次
- 出现次数：1

### TR-022：终审定向测试的一项新增用例缺少 TurnStateToken 导入

- 时间：2026-07-18 15:31:00 +08:00
- 阶段：终审修正后的三模块定向测试
- 命令或操作：`python -m unittest -v tests.unit.test_p1_assets tests.unit.test_panel_wiring tests.unit.test_runtime_diagnostics`
- 预期：71 项全部通过
- 实际：70 项通过，`test_stale_start_ack_and_notification_do_not_rebind_diagnostics` 在构造测试 token 时发生 1 项 error
- 原始证据：`NameError: name 'TurnStateToken' is not defined`；产品实现相关的其余测试均通过
- 分类：新增测试导入遗漏
- 处理：先记录，只在测试模块现有 turn_state 导入中加入 `TurnStateToken`，然后只重跑该测试
- 临时方案：无
- 状态：已解决；补齐测试导入后单项精确重跑通过，随后三模块 72 项全部通过
- 出现次数：1

### TR-023：跨 Panel 报告复用的首次实现使用了子串匹配

- 时间：2026-07-18 15:35:00 +08:00
- 阶段：同 Turn 报告跨 Panel 复用自审
- 命令或操作：审查新增的 diagnostics 文件 Thread/Turn 查找逻辑
- 预期：只复用 Thread ID 与 Turn ID 完全一致的既有报告
- 实际：首次实现用 `line in content`，短 ID 理论上可能命中以它为前缀的较长 ID 行
- 原始证据：`_find_existing_report()` 对 `thread_line`、`turn_line` 使用全文子串判断
- 分类：报告归属精确性 / 新增实现自审
- 处理：先记录，再把报告拆为行集合并要求两条字段整行完全相等
- 临时方案：修正前不运行跨 Panel 复用测试
- 状态：已解决；改为字段整行集合匹配，跨 writer/Panel 复用测试通过
- 出现次数：1

## 测试汇总

- 已通过测试：首轮定向测试 142 项中的 136 项；修正后重跑原失败的 6 项，6 项全部通过；终审三模块定向测试最终 72 项全部通过，新增 pre-Turn 合并用例单项通过；完整测试仅运行一次，553 项全部通过（20.507 秒）
- 已失败并解决：TR-002（定位真实版本化契约目录）、TR-004（移除不存在的搜索目录）、TR-005（确认兼容值不是协议方法）、TR-006（显式 UTF-8 读取）
- 审计查询说明：TR-003 已确认是无匹配触发并行包装失败，不是产品或测试失败
- 编辑异常：TR-007 已通过精确上下文补丁解决，首次原子拒绝未留下部分修改
- 实现自审：TR-008 已按真实状态枚举修正
- 警告：TR-009 为 Git 换行提示，7 次只读差异命令共发出 68 条同类提示；最终 `git diff --check` 退出码 0、无空白错误，未触发文件批量改写
- 生命周期自审：TR-010 已改为按对象身份释放附件对话框引用
- 报告归属自审：TR-011 已在两个 Thread 切换入口完成清理
- Bridge 指令检查：TR-012 已压缩至 479 字符；TR-013 的首次复核脚本错误已纠正
- 首轮定向测试：TR-014 的 5 项失败和 1 项错误已通过六项精确重跑验证解决
- 最终 UI 自审：TR-018 已完成真正非模态显示、迟到 Turn 诊断归属和无 Thread 清理
- 最终 diagnostics 自审：TR-019、TR-023 已完成 warning、脱敏、场景变化、活动 Turn 恢复和跨 Panel 同 Turn 复用边界
- 终审定向测试：TR-022 导入遗漏已解决，最终三模块 72 项全部通过
- 完整测试：`python -m unittest discover -s tests -t . -v` 仅运行一次，553 项全部通过，无 failure、error、超时或跳过
- 最终 Git 核对：分支 `feature/panel-ui`，HEAD `5b09bf895a41763c025d624f86de29883ae956b7`，暂存区为空；本任务未提交、未推送，并保留 `.agents/` 与并行 launcher 任务的全部工作树内容
- 未解决：TR-001
- 超时或跳过：真实 Houdini GUI 验收已跳过，等待人工执行
- GUI 验收：代码完成后待真实 Houdini 验收
- 临时方案：以离线定向测试覆盖可验证行为，不声称已验证宿主焦点

## 启动器与启动前自检任务记录

以下 `LCH-*` 条目属于独立的 Windows 启动器任务。TR-017 中来源不明的 `scripts/launcher/HiaLauncher.Core.psm1` 已确认是本任务创建并持续维护的自检核心，未删除或覆盖其他任务文件。

### LCH-001：首轮仓库审计组合命令被正常无匹配和错误路径中断

- 时间：2026-07-18 14:55:00 +08:00
- 阶段：启动器只读审计
- 命令或操作：并行读取启动脚本、文档、运行时目录并执行 `rg`；另尝试读取不存在的 `houdini_package/packages/config.toml`，以及使用 PowerShell 不支持的 `Format-Hex -Count`
- 预期：一次取得全部现状证据
- 实际：`rg` 的无匹配退出码、Windows 下无效的 `launch-houdini*.ps1` 路径写法和不存在文件使组合包装返回失败；`Format-Hex -Count` 在本机 Windows PowerShell 5.1 不受支持
- 分类：命令组合 / 路径假设 / PowerShell 版本差异
- 处理：拆成独立只读命令，使用 `-Encoding utf8`、明确存在的路径和 PowerShell AST 解析器
- 临时方案：无；未修改产品文件
- 状态：已解决
- 出现次数：3 类命令差异，各 1 次

### LCH-002：Windows PowerShell 5.1 将无 BOM UTF-8 中文脚本误解码

- 时间：2026-07-18 15:00:00 +08:00
- 阶段：自检核心首次语法解析
- 命令或操作：用 PowerShell AST 解析新建 `HiaLauncher.Core.psm1`
- 预期：中文检查消息不影响脚本语法
- 实际：Windows PowerShell 5.1 按系统代码页读取无 BOM UTF-8，部分多字节字符吞并字符串引号并产生大量语法错误
- 分类：编码 / Windows PowerShell 5.1 兼容
- 处理：为包含中文的 PowerShell 文件保留 UTF-8 BOM；随后 AST 解析错误数为 0
- 临时方案：测试读取源码使用 `utf-8-sig`
- 状态：已解决
- 出现次数：1

### LCH-003：本机执行策略首次阻止直接导入自检模块

- 时间：2026-07-18 15:03:00 +08:00
- 阶段：模块烟雾验证
- 命令或操作：当前 PowerShell 进程直接 `Import-Module`
- 预期：加载模块并执行发现/脱敏函数
- 实际：系统执行策略拒绝脚本模块
- 分类：本机执行策略 / 权限环境
- 处理：仅对测试子进程使用 `-ExecutionPolicy Bypass` 或进程范围 Bypass；未修改系统策略、注册表或 AppData
- 临时方案：README 启动命令显式带进程级 `-ExecutionPolicy Bypass`
- 状态：已解决
- 出现次数：1

### LCH-004：一次多文件补丁长时间无返回并留下可识别的部分修改

- 时间：2026-07-18 15:07:00 +08:00
- 阶段：便携路径改造
- 命令或操作：同一补丁修改生命周期脚本、兼容包装、Codex 配置、pyproject 和 Houdini package
- 预期：原子完成小范围便携化
- 实际：调用持续无输出，终止后确认两个启动脚本已修改而其余配置未修改
- 分类：编辑工具超时 / 部分应用
- 处理：逐文件只读核对实际状态，再用小补丁完成剩余配置；没有回滚、覆盖或丢弃并行工作树内容
- 临时方案：后续补丁保持单文件或小范围
- 状态：已解决
- 出现次数：1

### LCH-005：ProcessStartInfo.EnvironmentVariables 在本机运行时返回 null

- 时间：2026-07-18 15:11:00 +08:00
- 阶段：Bridge 与 FXHoudini MCP import 探针
- 命令或操作：通过自检核心给子进程设置 `PYTHONPATH`、`PYTHONDONTWRITEBYTECODE` 等仅子进程环境
- 预期：两个 Python import 探针通过
- 实际：本机 Windows PowerShell 5.1/.NET 暴露的 `EnvironmentVariables` 为 null，导致进程启动前失败；直接运行同一项目 venv import 可通过
- 分类：运行时 API 差异 / 环境误判
- 处理：优先使用非空的 `ProcessStartInfo.Environment`，仅在旧 API 可用时回退 `EnvironmentVariables`；Bridge 和 FXHoudini MCP 探针随后均通过
- 临时方案：无；没有修改系统环境
- 状态：已解决
- 出现次数：1

### LCH-006：启动器定向测试首轮 2 项失败

- 时间：2026-07-18 15:18:00 +08:00
- 阶段：离线定向测试
- 命令或操作：`python -m unittest tests.unit.test_launcher_preflight -v`
- 预期：11 项全部通过
- 实际：生命周期入口源码计数把状态文案也计入；安全修复读取 `PSObject.Properties.Count` 时遇到 PowerShell 集合差异，未改写假移动项目的 package
- 分类：测试断言精度 / PowerShell 集合 API 差异
- 处理：断言缩窄到唯一 `Join-Path` 调用；属性计数改为数组化后计数；精确重跑 2 项均通过
- 临时方案：无
- 状态：已解决
- 出现次数：1 轮，2 项

### LCH-007：项目本地 Codex 当前未登录

- 时间：2026-07-18 15:10:00 +08:00
- 阶段：真实项目只读自检
- 命令或操作：项目本地 `codex.exe login status`，子进程仅设置项目本地 `CODEX_HOME`
- 预期：确认登录状态但不读取凭据文件
- 实际：版本 `0.144.3` 匹配，但状态为未登录；自检正确分级为红色
- 分类：环境阻断 / 需人工处理
- 处理：不读取或记录任何凭据；报告只保留布尔状态和修复建议
- 临时方案：用户在项目本地 `CODEX_HOME` 完成一次 Codex 登录后重新扫描
- 状态：未解决，等待人工登录
- 出现次数：1

### LCH-008：真实 Houdini GUI 与启动器按钮交互未自动验收

- 时间：2026-07-18 15:30:00 +08:00
- 阶段：交付前验证
- 命令或操作：离线发现、假命令输出、PowerShell AST、CLI 和 unittest 验证
- 预期：自动测试不启动真实 Houdini GUI，同时覆盖可离线证明的行为
- 实际：未执行 WPF 人工点击、真实多版本下拉显示、真实 `houdini.exe -version`/`hython` 与最终 Panel/Bridge 连接
- 分类：GUI / 真实宿主人工验收
- 处理：README 提供最小人工验收步骤；WPF 资产由离线解析测试覆盖，启动按钮仍只调用现有 `scripts/launch-houdini.ps1`
- 临时方案：以离线假目录和探针输出覆盖发现、版本/解释器不匹配、缺依赖、不可写和脱敏
- 状态：未解决，等待人工 GUI 验收
- 出现次数：1

### 启动器测试汇总

- 启动器定向测试：首轮 11 项中 9 项通过、2 项失败；两项精确重跑均通过；新增 CLI/设置覆盖后 13 项全部通过
- 既有启动器/配置回归：`tests.unit.test_p1_assets` 与 `tests.unit.test_b2c_codex_config` 共 29 项全部通过
- 完整套件：551 项中 550 项通过、1 项因并行 Panel 测试缺少导入而错误，详见 LCH-010
- `git diff --check`：退出码 0，无空白错误；仅报告现有工作树的 LF→CRLF 提示，未批量改写
- PowerShell AST：4 个启动器脚本/模块均为 0 个解析错误
- 未启动真实 Houdini GUI，未联网下载或安装依赖

### LCH-009：显式 Houdini 路径的单对象输出在 StrictMode 下没有 Count 属性

- 时间：2026-07-18 15:38:00 +08:00
- 阶段：CLI 回归复核
- 命令或操作：精确重跑 `-CheckOnly -Json`、显式路径和脱敏 3 项测试
- 预期：3 项通过
- 实际：PowerShell 的 `if` 管道展开了单个候选对象，后续 StrictMode 访问 `.Count` 报错；其余 2 项通过
- 分类：PowerShell 单对象/数组语义
- 处理：在整个条件表达式外使用 `@(...)` 固定数组形状，再精确重跑 CLI 测试
- 临时方案：无
- 状态：已解决
- 出现次数：1

### LCH-010：完整测试被并行 Panel 用例的缺失导入留下 1 项错误

- 时间：2026-07-18 15:42:00 +08:00
- 阶段：最终唯一一次完整测试
- 命令或操作：`python -m unittest discover -s tests -t . -v`
- 预期：当前共享工作树全部通过
- 实际：共运行 551 项，550 项通过；`tests.unit.test_panel_wiring.PanelWiringTests.test_stale_start_ack_and_notification_do_not_rebind_diagnostics` 因 `TurnStateToken` 未定义而 `NameError`
- 分类：并行任务未完成导入 / 非启动器回归
- 处理：保留真实结果，不重跑全套、不修改 Panel 或其测试；启动器自身 13 项定向测试和既有启动器/配置 29 项回归均已通过
- 临时方案：负责 Panel 的并行任务补充对应导入后再由主任务统一验证
- 状态：已由并行 Panel 任务补充导入；本次仅精确复核该 1 项并通过，本启动器任务未修改 Panel
- 出现次数：1

### LCH-011：普通 Codex 项目被 required MCP 相对命令阻断恢复

- 时间：2026-07-18 15:50:00 +08:00
- 阶段：启动器配置回归收口
- 命令或操作：普通 Codex 项目任务加载 `.codex/config.toml`，其中 MCP command 为项目相对 `.runtime\fxhoudinimcp\1.3.0\venv\Scripts\python.exe`
- 预期：Houdini 未由项目启动器拉起时，普通 Codex 项目任务仍可恢复；受控 Houdini 生命周期才强制要求实时 MCP
- 实际：配置曾为 `required=true`，相对 command 不能作为普通项目任务的强制可执行路径，任务恢复直接以 `os error 3`（找不到指定路径）失败
- 分类：配置阻断 / 普通项目与 Bridge 进程级配置边界混淆
- 根因：跟踪配置错误承担了 Bridge 生命周期的强制语义；正确双层设计应为普通项目 `enabled=true, required=false`，Bridge 再用 `--strict-config`、已验证的绝对 `mcp_python` 和 `required=true` 覆盖
- 临时阻断解除：主任务已将 `.codex/config.toml` 唯一一行紧急改为 `required=false`
- 处理：配置单元测试改为要求普通项目 `required=false`；启动器预检新增 `project.codex_config_required`，遇到 `required=true` 即报告红色任务恢复阻断；未修改 Bridge 实现
- 验证：普通配置、启动器预检、Bridge 生命周期三组定向测试共 30 项全部通过；Bridge 测试仍精确断言 `--strict-config`、绝对 command 与 `required=true`；历史 `TurnStateToken` 用例单独复核 1 项也已通过
- 临时方案：无；跟踪配置与进程级强制覆盖均已恢复既有 P2-V 双层设计
- 状态：已解决
- 出现次数：1

### LCH-012：WPF 验证命令与报告补丁首轮受到引用和上下文影响

- 时间：2026-07-18 16:12:00 +08:00
- 阶段：WPF/XAML 兼容性与静态检查
- 命令或操作：查询 `Dispatcher.Invoke` 重载、批量运行 PowerShell AST、搜索禁用 UI 技术与绝对路径
- 预期：得到只读 API、语法和源码检查结果
- 实际：5 个首轮只读命令分别遇到嵌套 PowerShell 提前展开 `$_`、嵌套引号丢失、`foreach` 后直接管道形成空管道、结尾反斜杠使正则组未闭合，以及以 `-Mta` 开头的搜索模式被 `rg` 当作选项。随后 TEST-REPORT 多处补丁因精确上下文不匹配失败一次，改用过于通用的结尾上下文又把新条目插入到首个同名状态段后；项目代码与既有报告内容未丢失
- 分类：命令引用 / 验证脚本语法 / 补丁上下文
- 处理：改为在当前 PowerShell 直接查询 API、先把 `foreach` 结果数组化、用多个 `rg -e` 或 `Select-String` 表达模式；读取准确行号后删除误插入的本轮条目，并以 LCH-011 的唯一上下文重新追加。随后 AST、源码与报告位置检查均成功
- 临时方案：无
- 状态：已解决
- 出现次数：5 个只读命令首轮、2 个报告补丁上下文问题

### LCH-013：同步 WPF 自检仍需要真实桌面人工验收

- 时间：2026-07-18 16:12:00 +08:00
- 阶段：现代深色 WPF 外壳验证
- 命令或操作：Windows PowerShell 5.1 STA `XamlReader.Load`、关键控件 `FindName`、离线 UI 资产测试、启动器 17 项定向测试
- 预期：不显示窗口、不启动 Houdini 的前提下验证 XAML、控件、标准窗口边框、自包含资源和 CLI 隔离
- 实际：XAML 成功加载为 `System.Windows.Window`，17 个关键原生控件类型正确；新增 UI 资产测试 3/3、完整启动器定向测试 17/17 通过。中文 WPF 脚本保留 UTF-8 BOM，避免 Windows PowerShell 5.1 误解码
- 分类：GUI 自动化边界 / PowerShell 5.1 兼容
- 处理：使用独立松散 XAML、标准标题栏、原生 WPF 控件和一次 Render 更新显示忙碌态；没有引入异步框架、动画、外部素材或依赖
- 临时方案：长探针仍在 UI 线程同步执行，不确定进度条动画可能在探针期间短暂停顿，但忙碌卡和禁用状态会先呈现；这符合本轮拒绝复杂异步框架的边界
- 人工待验：125%/150%/200% DPI、最小窗口、Tab/Enter/Space 焦点、文件选择器、剪贴板短提示、真实多版本选择与最终 Houdini/Bridge 连接
- 完整测试：本轮未再次运行全套；此前唯一一次完整测试已记录于 LCH-010，本轮只改 UI 外壳且启动器 17 项全部通过
- 状态：离线验证已解决；真实桌面与 Houdini 启动仍等待人工验收
- 出现次数：1

### WPF 启动器 UI 测试汇总

- 新增 UI 资产精确测试：3 项全部通过，包括真实 WPF XAML 加载、17 个关键控件、无外部依赖、无动画、标准窗口边框，以及缺失全部 WPF 资产时 MTA `-CheckOnly -Json` 仍成功返回预期红色 JSON
- 启动器完整定向测试：17 项全部通过，包含既有发现、探针、依赖缺失、移动项目、脱敏、设置、普通项目 `required=false` 回归和新增 WPF 外壳断言
- PowerShell AST：`hia-launcher.ps1`、`HiaLauncher.Wpf.ps1`、`HiaLauncher.Core.psm1`、`launch-houdini.ps1` 均为 0 个解析错误
- Windows PowerShell 5.1 STA：`PresentationFramework` 成功加载，松散 XAML 成功实例化但未调用 `Show`/`ShowDialog`
- `git diff --check`：退出码 0；仅提示 README 与 ARCHITECTURE 的现有 LF→CRLF 工作树转换规则，未批量改写行尾
- 未启动真实 Houdini GUI，未联网、未安装依赖、未修改系统配置

## HIA MCP V2

### 审计结论

- 旧自有 `services/houdini_mcp` 是历史 Gate/固定 schema 实现：生产 profile 受两工具/五工具与 Object/Sop 五节点 catalog 约束，不适合当前“Codex 生成批量 HOM、当前 Houdini 会话执行”的目标。
- 第三方 `fxhoudinimcp` 1.3.0 的真实 FastMCP `tools/list` 抓取结果为 179 个工具；源码按 22 个工具模块导入装饰器注册，包含大量 create/set/connect/get 单项工具。HIA V2 未导入或复制其代码。
- 第三方目录开始与结束只读摘要完全一致：`.runtime/fxhoudinimcp/1.3.0` 均为 4517 个文件、89537089 bytes、最新写入 `2026-07-17T22:03:31.9676361Z`；未修改、覆盖、复制或删除其中任何文件。
- 当前 Bridge/Panel 使用独立 loopback Bearer 链路；launcher 现有 live backend 为第三方 route `/api`。Native `hython` 由 launcher 暴露，但 session 指令明确仅用于用户指定的离线、独立 HIP、批处理或后台渲染，与 HIA V2 当前会话职责不重叠。

### 本轮实现与隔离

- 新增独立 distribution/import/stdio entry point/server id `hia_mcp_v2`，Houdini 侧包 `hia_mcp_runtime`，16 个 `hia_` 前缀批量语义工具；能力矩阵覆盖场景、动态节点知识、几何、材质/渲染、Solaris、动画、模拟、HOM、视觉、验证与本地帮助。
- loopback 固定 `127.0.0.1`，route `/hia-mcp-v2/v1/execute`，runtime `.runtime/hia-mcp-v2`，环境变量仅 `HIA_MCP_V2_*`；每个 runtime session 默认随机 token 与随机端口。
- `hia_execute_hom` 只在 Houdini UI 主线程执行一次批量脚本，返回 `ok/result/stdout/warnings/errors/created_or_changed_paths/revision/dirty/diff`；请求前可取消，进入 HOM 后不可强杀并在合同中如实返回。
- 未修改 Panel、launcher、Bridge session、`.codex/config.toml`、root pyproject、README、AGENTS 或现有共享测试；未注册或替换生产 MCP。

### 问题记录

- 首轮对整个第三方 runtime 计算逐文件聚合 SHA256 超过 20 秒，组合只读命令超时；改用相同的文件数、总字节数与最新写入时间做首尾摘要，没有写第三方目录。
- 首次抓取上游真实 `tools/list` 时，命令在上游 source 工作目录中仍使用项目根相对 Python 路径而失败；改为绝对 venv Python 路径，并设置 `PYTHONDONTWRITEBYTECODE=1` 后成功得到 179 个名称。
- 首轮定向测试 24 项中 23 项通过、1 项 error：timeout 用例使用 0.05 秒，但产品合同下限为 0.1 秒；用合法下限和更长 fake 延迟修正后通过。
- 修正 timeout 用例后，客户端断开曾让后台 HTTP handler 输出一次 Windows `ConnectionAbortedError`，虽然测试结果为通过；server 写回现已处理 broken/reset/aborted/OSError，最终运行无异常堆栈。
- 并行任务持续写入 launcher、Panel 和本报告；本任务只新增独立目录/测试，并在交付前重新读取本文件后追加本节，没有覆盖或重排已有内容。

### 测试结果与未验证项

- 最终定向命令：`python -B -m unittest -v tests.unit.test_hia_mcp_v2_protocol tests.unit.test_hia_mcp_v2_isolation tests.unit.test_hia_mcp_v2_runtime tests.unit.test_hia_mcp_v2_transport`
- 结果：25 项全部通过；覆盖进程内和真实 `python -m hia_mcp_v2` 子进程 stdio initialize/tools/list/tools/call、一次批量 dispatch、16 工具/能力矩阵一致、与上游 179 名称交集为 0、无节点白名单、401/403、脱敏、loopback、随机端口/token、请求/响应大小、timeout 和取消边界。
- `git diff --check` 退出码 0；仅报告共享工作树已有 LF→CRLF 提示。新增 16 个 HIA V2 文件的尾随空白检查为 0。
- 按并行边界未重复运行完整测试套件，未启动真实 Houdini GUI。
- 待真实 Houdini 21.x 验证：`hdefereval` 主线程派发、各节点 context 的动态 catalog/参数模板、geometry/MaterialX/Solaris/USD/Karma/动画/模拟摘要、执行前后 diff、viewport/flipbook 图像、真实长 render/cache 的超时行为。确认真实长任务需求后再决定是否增加简单 job/status/cancel。

### HIA MCP V2 最小生产接入（2026-07-18）

#### 接入行为

- WPF launcher 新增一个互斥 backend 下拉：默认/推荐 `hia_v2`，兼容回退 `fxhoudini`；三项选择只写入 `.runtime/launcher/settings.json`。
- `scripts/launch-houdini.ps1` 仍是唯一生命周期入口。它在创建 Bridge 与 Houdini 子进程前清除继承的两套 backend 环境，再只注入所选 backend 的 PYTHONPATH、随机端口和随机 token。
- HIA 分支只使用 `HIA_MCP_V2_*`、`.runtime/hia-mcp-v2` 和 `/hia-mcp-v2/v1/*`；不会探测、导入或启动第三方 runtime。fallback 分支保留锁定的 `.runtime/fxhoudinimcp/1.3.0` 与原 `/api` 行为。
- Bridge 以 `--strict-config` 注册 `hia_mcp_v2`：command 为当前已验证的绝对 Bridge Python，args 为 `-B -m hia_mcp_v2`，`required=true`，approval 为 `approve`，并显式设置 `mcp_servers.houdini_intelligence.enabled=false`。fallback 不注册 HIA server。
- Houdini UI-ready 只启动选择的 runtime；HIA runtime 仍通过 `hdefereval.executeInMainThreadWithResult` 执行所有 `hou` 调用。Bridge 的 HIA health 使用认证 GET `/hia-mcp-v2/v1/health` 并校验 protocol/server id/revision。
- Panel 复用单一状态标签：`HIA MCP V2：可用` 或 `FXHoudiniMCP：回退`；未知 backend、health 失败和 app-server 退出均 fail-closed 为不可用。
- 普通项目 `.codex/config.toml` 保持 `required=false`；受控 Houdini 生命周期的强制语义只由 Bridge 的进程级 strict config 注入。

#### 本轮问题与处理

- 固定 Codex 0.144.3 的真实 app-server 首次 `thread/start` 失败，required server 报 `tools/list parameters are invalid`。捕获到 Codex 实际发送 `{"_meta": {...}}`，而首版 adapter 只允许 cursor；现已接受标准 mapping `_meta`，未增加工具或放宽工具参数合同。修复后真实离线 `thread/start` 成功。
- 新增生产接入测试首次为 9/10：Windows Python 子进程用默认 GBK 解码 PowerShell UTF-8 输出。测试显式指定 UTF-8 后为 10/10；产品代码不受影响。
- 合并定向测试首次运行 175 项，173 项通过、2 项失败：旧 P1 资产断言仍假定 Houdini package 中不存在新的受控 HIA UI runtime。断言改为只允许 `hia_mcp_runtime/executor.py` 导入 `hou`，并只认可其显式 `cook` 调用；P1 23/23 精确复核通过。
- 并行工作期间收到一条误发的“停止按钮/统一缓存”要求；两个对应子任务在写文件前被中止，搜索确认没有 `HIA_CACHE_DIR`、STOPPING 文案或相关产品改动。本节不把误发任务纳入实现。

#### 验证结果

- launcher/preflight/WPF/uiready 定向测试：20/20；Panel 状态定向测试：72/72；Bridge/strict-config/session/health 与 MCP 协议定向测试：51/51；独立生产接入测试：10/10。
- 固定 Codex 0.144.3 真实 strict-config 离线握手：`thread/start` 成功；`mcpServerStatus/list` 中 `hia_mcp_v2` 为 16 个工具且全部 `hia_` 前缀；没有 `create_node`、`set_parameter`/`set_parameters`；禁用的 `houdini_intelligence` 为 0 个工具。
- 合并后的本轮唯一一次完整测试：`python -B -m unittest discover -s tests -t . -v`，602/602 通过，用时 27.133 秒。
- PowerShell AST：5 个 launcher/lifecycle 文件 0 个解析错误；`git diff --check` 退出码 0，仅有工作树既有 LF→CRLF 提示。
- 第三方目录首尾只读摘要完全一致：4517 个文件、89537089 bytes、最新写入 `2026-07-17T22:03:31.9676361Z`、聚合树 SHA256 `f68d08363c43dc90d40de73816b73312be119bf7b90a876646c09e018ee69ef5`。未修改、覆盖、复制或删除 `.runtime/fxhoudinimcp/1.3.0` 中任何文件。
- 未启动真实 Houdini GUI。待人工验证 launcher 选择、Panel 在线状态、`hia_context`、box/vellum/mtlx/karma 动态查询、当前选择 `hia_inspect`、一次 `hia_execute_hom` 小资产、`hia_scene_diff`、`hia_capture_viewport`，以及工具列表无上游 179 工具。

## Panel 停止收口与统一运行时缓存（2026-07-18）

### 问题一：停止 ACK 后仍长期显示运行中

- 根因：Panel 发送 `turn/interrupt` 后只有 transport pending 标记；ACK 到达便清除此标记，但远端 Turn 仍由 `turn/completed` 或 session `turn_active=false` 才能权威结束。原实现没有本地 STOPPING 表达、流冻结或 ACK 后对账，因此迟到 delta、流刷新与自动滚动继续，且 Stop/追加指令可能重新可用。
- 修复：停止点击立即冻结已收到文本、停止 stream/scroll timer、清除该 Turn 的可见流关联并显示“正在停止…”；以精确 `TurnStateToken` 保证同一 Turn 只发一次 interrupt。ACK 不结束 Turn，只启动一次 2500 ms QTimer 对账；仍 active 时静态显示“Codex/Houdini 工具仍在结束”，idle 时按“已停止”收口。停止期间输入与附件可编辑，但发送被产品逻辑与控件双重阻止。
- 竞态：匹配 completion 继续正常权威收口；completion 到达时取消在途 stop reconciliation；迟到 delta、旧 ACK、旧 session 响应和上一 Turn completion 不再影响下一 Turn。`NO_ACTIVE_TURN + turn_active=false` 即使未返回 turn id，也用精确停止 token 自愈为空闲。
- 诚实边界：已进入 Houdini UI 主线程的 HOM 无法安全强杀；停止只阻止后续 Codex 步骤，并在 HOM 返回或 session 变 idle 后收口，Panel 不再继续伪装流式思考。

### 问题二：自动图片与中间文件没有统一缓存根

- 根因：launcher、Bridge/app-server 与 Houdini runtime 之间没有 `HIA_CACHE_DIR`；首方 `hia_capture_viewport` 仍写 `.runtime/hia-mcp-v2/captures`，而截图、预览和短期文件缺少统一便携目录约定。
- 修复：从当前项目根动态派生 `.runtime/cache` 及 `screenshots`、`previews`、`tmp`，launcher 按需创建并同时传给 Bridge 与 Houdini；Bridge 校验精确项目内路径后传给 Codex app-server、HIA MCP V2 或 FX fallback。保留原 `TEMP`、`TMP`、`HOUDINI_TEMP_DIR` 和 `.runtime/attachments` 行为。
- 首方 viewport 截图默认写 `HIA_CACHE_DIR/screenshots`，名称为 UTC 时间戳加 8 位随机后缀；环境根逃逸与截图 `output_path` 注入在写入/transport 前拒绝。developerInstructions 要求自行生成的截图、预览和中间图分别写入三个缓存子目录，并显式传受支持的输出路径。
- `.gitignore` 现有 `.runtime/` 已覆盖缓存目录；未增加清理器、容量限制、索引、数据库、缓存设置页、守护进程或第二套执行架构。

### 测试结果

- Panel 首轮定向：`test_panel_wiring + test_conversation_tool_activity`，64/64 通过。
- 缓存、launcher、Bridge、HIA MCP V2 与 fallback 首轮定向：118 项中 117 通过；唯一 error 是旧 P1 源码字符串断言不再匹配 STOPPING 条件换行，更新为相同语义断言后 118/118 通过，产品运行测试无失败。
- 最终合并定向：Panel/Conversation、Bridge lifecycle/session、Codex config、launcher、HIA MCP V2 protocol/runtime/production 和 P1 资产共 170/170 通过。
- 本轮唯一一次完整测试：`python -B -m unittest discover -s tests -t . -v`，614/614 通过，用时 29.796 秒。
- 完整测试后的最后一处竞态复核补充了“停止后迟到 turn/started 不覆盖 STOPPING 状态”，随后 Panel/Conversation/P1 精确定向 88/88 通过；未重复运行完整套件。
- `scripts/launch-houdini.ps1` PowerShell AST 为 0 个解析错误；`git diff --check` 退出码 0，仅显示共享工作树既有 LF→CRLF 提示。
- 未启动真实 Houdini GUI。待人工验收：在真实活动 Turn 中点击一次停止，观察立即静态 STOPPING、约 2.5 秒后 active/idle 两种收口；再执行一次 `hia_capture_viewport`，确认返回路径位于 `.runtime/cache/screenshots`。同时确认正在执行的长 HOM 只在返回后终止 Turn。

## HIA MCP V2 并发 hotfix（2026-07-18）

### 真实问题与根因

- 真实 Houdini GUI 验收中，同一个 Turn 并发发出 16 次 `hia_search_node_types`。stdio 原实现用 `MAX_CALL_WORKERS=2` 的 active worker 数直接作为接收上限，第 3 次及以后立即返回 `QUEUE_FULL`，引发大量失败与重试；这不是 Houdini runtime 或 UI 主线程需要 16 路并发，而是缺少等待队列。
- app-server 下观察到的 4 个 `hia_mcp_v2` 子进程均有正常父进程，数量与主任务/多代理上下文相符，不是孤儿或生命周期泄漏。本轮未修改进程管理，也未启动、关闭或干预现有进程。
- Houdini Python Shell 中每个成功请求都会出现 `POST /hia-mcp-v2/v1/execute ... 200`，来源是 `BaseHTTPRequestHandler` 默认 access log，不是执行错误，但会污染交互界面。

### 最小修复

- `MAX_CALL_WORKERS` 保持 2；stdio 新增容量 32 的 pending call queue 和两个长驻 worker。reader 只接收/入队 `tools/call`，因此阻塞调用期间仍可处理 `notifications/cancelled`、`ping` 等消息。
- active 与 queued request id 统一登记；重复 id 返回 `DUPLICATE_REQUEST_ID`。2 个 worker 忙且 32 个 pending 全满后，下一次调用才返回 `QUEUE_FULL`，details 包含 `pending_capacity`、`pending_count` 和 `active_worker_limit`。
- queued request 若在真正 transport/Houdini dispatch 前收到取消，沿用现有结构化 `CANCELLED_BEFORE_EXECUTION`，不会进入 fake/真实 transport。EOF 会先 drain 已接收调用，再发送 worker sentinel、join、关闭 transport；输出流失败只记录一次固定 `OUTPUT_WRITE_FAILED` 并继续 drain，避免 `queue.join()` 卡死。
- `hia_search_node_types` 描述改为使用高信号 `query/contexts/limit`、等待前次结果，禁止重复并发扇出和盲目重试；工具名称、schema 与总数仍为精确 16。
- HIA runtime 覆盖默认 access logging：成功 2xx 静默，401/403/507 等非 2xx 仍保留必要诊断，调用方继续获得原有结构化错误。
- Bridge developerInstructions 明确只有主代理调用当前会话的 `hia_*` 工具；子代理可做研究、方案、代码审查和规划，但不得并行访问当前场景。同类 search/help 由主代理串行或少量调用，`hia_execute_hom` 等场景写入始终由主代理执行。保留多代理能力，不禁用 subagents。

### 问题记录与验证

- 新增主代理约束后，首个精确 Bridge 测试因既有 developerInstructions 640 字符上限失败（实际 794）；仅把该 HIA 指令紧凑性回归上限调整为 850，随后 Bridge session 18/18 通过，产品行为未回退。
- stdio/protocol/transport 精确定向 24/24 通过；合并 MCP runtime/isolation/production、Bridge、launcher、backend 互斥、普通配置与 `HIA_CACHE_DIR` 定向测试 102/102 通过，用时 10.281 秒。
- 本轮唯一一次完整测试：`python -B -m unittest discover -s tests -t . -v`，621/621 通过，用时 27.261 秒。
- 覆盖：慢 fake transport 下 16-call burst 全部完成且 transport 并发峰值不超过 2、queued cancel 不 dispatch、真实容量溢出才 `QUEUE_FULL`、active/queued 重复 id、ping 响应、超过旧 0.25 秒窗口的 EOF 完整响应、输出失败无挂起/worker 泄漏、成功 200 无 access log、非 2xx 结构化错误、16 工具/backend 互斥/cache 回归和主代理指令。
- 第三方目录结束只读摘要仍为 4517 个文件、89537089 bytes、最新写入 `2026-07-17T22:03:31.9676361Z`，与开始摘要一致；未修改 `.runtime/fxhoudinimcp/1.3.0`。
- 未启动真实 Houdini GUI。待复验：同一 Turn 发起 16 次合法搜索应全部完成且不出现 `QUEUE_FULL`；Python Shell 不再刷成功 200；queued cancel 在执行前返回取消；工具列表仍只有 16 个 `hia_*`，不出现上游 179 工具。已进入 Houdini UI 主线程的 HOM 仍不可安全强杀。

## Alpha 提交前整合检查（2026-07-18）

- 提交清单审计发现 `AGENTS.md`、`src/hia_core/path_policy.py` 与 Panel 附件默认根仍固定为开发机 E 盘，与便携 launcher 的行为不一致。现改为优先读取 `HIA_PROJECT_ROOT`，否则从仓库文件位置推导；安全边界仍限制为解析后项目根的严格子路径，并继续拒绝 UNC、设备路径、盘符根、ADS、AppData、路径逃逸与 reparse point。
- 两项第三方隔离审计原先直接依赖被 Git 忽略的 `.runtime/fxhoudinimcp`。当前安装存在时仍执行 179 工具交集检查；干净 clone 缺少可选第三方审计 fixture 时只跳过对应检查，不要求提交第三方源码。
- 第一次从 Codex 只读沙箱运行完整测试在 120 秒超时；`-v` 定位后确认测试需要向项目 `.runtime` 写临时 fixture，但沙箱返回 `WinError 5`。允许仅在项目 `.runtime` 写入后，完整套件为 621/621、26.910 秒，证明不是产品死锁；临时 fixture 均被 Git 忽略。
- `skill-creator/scripts/quick_validate.py` 首次因当前 Python 缺少 PyYAML 无法启动。PyYAML 仅安装到被忽略的 `.runtime/skill-validation` 后，`houdini-visual-research` 返回 `Skill is valid!`；该依赖与验证目录均未进入提交。
- 便携路径、附件与 Bridge 生命周期定向测试 31/31 通过；`git diff --check` 通过，仅有既有 LF→CRLF 提示。尚待真实 Houdini GUI 验收 WPF 启动、Panel 焦点/停止、16-call 排队与成功 200 日志静默。

## Panel 系统盘审批降噪与 `skills/changed`（2026-07-18）

### 真实问题与根因

- `item/commandExecution/requestApproval` 原先直接把整段协议 JSON 放进 Panel，目的、目标和影响无法快速判断；同时纯时间格式转换、公开网页 GET、项目内正常读写也逐次等待人工决定。
- ShaderToy 首屏资料读取与 SideFX COP Wrangle 文档查询都属于公开网页只读 GET，却被拆成逐页 PowerShell 审批；developerInstructions 没有要求优先原生 web/search、同一研究阶段批量读取和复用已取内容。
- app-server 的 `skills/changed` 是稳定的被动列表变化通知，但不在精确接收集合中，因此产生 `UNKNOWN_NOTIFICATION_IGNORED` 提示。
- 原始 server request 在进入 Bridge pending/EventBuffer 后才由 Panel 展示层脱敏，任意 Authorization、Cookie 或 API key 文本仍可能先留在内存事件中。

### 最小修复行为

- Bridge 在既有 approval request 入口做一次无状态判定：纯计算/格式转换、只读本地查询、公开网页 GET、项目根内正常读写以及既有 HIA/FX MCP 自动批准路径不弹窗，并继续使用 app-server 原有一次性 `accept` 响应。只有命令明确在当前系统盘创建、修改、移动或删除文件时才发布给 Panel；未设置全局 `approval=never`，未自动批准任意 PowerShell，也未修改 HIA MCP 的 `default_tools_approval_mode="approve"`。
- 判定优先使用 `params.commandActions[].command`，只有缺失时才回退到序列化 `params.command`。覆盖 PowerShell 内容/项目目标与系统目标的区分、Copy 只判断写入目标、Move 同时判断源删除和目标写入、系统环境路径、重定向、常见 PowerShell/.NET/Python 写法及 curl/wget 显式输出；无法可靠确认系统盘变更时不伪造风险结论。
- Panel 只为真实系统盘变更显示可读卡片：客观目的、操作类型、实际系统目标、cwd/URL 和中性影响提示；主按钮固定为“允许一次”“拒绝”。原始 command、availableDecisions 和完整 JSON 默认折叠到“高级详情”。只有协议同时提供有效 amendment 且没有从 availableDecisions 排除时，才在高级区显示“以后允许相同命令规则（持续授权）”；不会自动选择。既有 `allow → accept` 与 `deny → decline` 不变，持续规则使用 app-server 原生 `acceptWithExecpolicyAmendment` 响应。
- Authorization/Bearer、Cookie、API key、token/password/secret、curl header/cookie、URL credential 与 userinfo 在 Bridge 写入 pending/EventBuffer 前递归脱敏；Panel formatter 再做一次显示防护，完整 JSON 中不保留原值。
- developerInstructions 要求外部研究先确定本阶段必需 URL，优先原生 web/search；没有网页工具时才把同阶段公开页面合并为一次 PowerShell 只读读取，复用已有内容，不逐页审批。HIA MCP 当前场景仍只由主代理串行调用。
- `skills/changed` 仅精确加入稳定的被动接收集合并由 Panel no-op；其余未知通知仍由协议层拒绝，只是不再把 `UNKNOWN_NOTIFICATION_IGNORED` 刷到对话中。
- 没有新增 Agent、Planner、审批策略引擎、数据库、重试器、状态机、全局 event filter、窗口抢焦或模态对话框；未修改 launcher、第三方 FXHoudiniMCP、HIA MCP 工具集合或 Houdini 运行时。

### 两个真实网页样例

- `[DateTimeOffset]::FromUnixTimeSeconds(...).ToString(...)`、ShaderToy 页面前 5000 字符读取、SideFX `sidefx.com/docs/houdini/nodes/cop/wrangle.html` 的 VEX/kernel/pixel 筛选均直接一次性允许，不显示审批卡。
- 若命令改为把网页输出显式写到系统盘，例如 `Invoke-RestMethod ... -OutFile C:\...` 或 `curl.exe ... -o C:\...`，则显示系统盘写入卡；项目目录内输出仍不弹窗。

### 验证结果与并行阻断

- 审批/Panel/协议/stdio/HTTP 合并定向：130/130 通过；后续 Bridge + Panel 精确回归 83/83 通过；HIA MCP V2、Bridge strict config、launcher/backend 互斥与 P1 相邻回归 86/86 通过。
- 最终边界复核覆盖项目内 value 含 `$env:USERPROFILE` 不误弹，以及 `Set-Item`、IRM `-OutFile`、curl `-o`、`AppendAllText`、`shutil.copyfile(E,C)`、`Tee-Object -FilePath C:`、`${env:SystemDrive}` 等明确系统盘写入；10/10 精确定向通过。Copy/Move 卡片目的也指向实际系统目标。
- 本轮唯一一次完整测试：`python -B -m unittest discover -s tests -t . -v`，637 项中 636 通过、1 失败，用时 28.651 秒。失败为 `LauncherBackendIntegrationTests.test_wpf_has_one_backend_picker_and_passes_one_selected_value`：完整测试运行期间并行任务把 `scripts/launcher/HiaLauncher.xaml` 的旧 `DisplayMemberPath="display"` 改为 `ItemTemplate`，但对应旧断言尚未同步；该精确用例随后单独复核仍失败。审批任务未修改或回退这些并行 launcher 文件，也未重复运行完整套件。
- 未启动真实 Houdini GUI。人工验收：在真实 Panel 依次提交时间转换、ShaderToy/SideFX 只读研究和一个明确 `C:\Users\Public\HIA-Approval-Test.txt` 写入；前两类不应弹窗，第三类应显示折叠且已脱敏的系统盘审批卡，并可分别验证“允许一次”“拒绝”和协议实际提供时的持续规则。确认过程中 Panel 不抢 Houdini 焦点。

## Launcher EXE 与高对比选择器收口（2026-07-18）

### 实际工具链与发布结果

- 开始时系统 `dotnet --info` 只有 x64 .NET/WindowsDesktop Runtime 8.0.13，明确显示 `No SDKs were found`。按任务要求没有安装全局 SDK；构建进程把 `DOTNET_CLI_HOME`、`NUGET_PACKAGES`、NuGet HTTP/plugin cache、`TEMP`、`TMP`、bin、obj 和 publish 全部定向到项目 `.runtime`。
- Microsoft 官方 .NET 8 发布元数据 `https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/8.0/releases.json` 给出最新 SDK `8.0.423`；实际下载地址为官方 `https://builds.dotnet.microsoft.com/dotnet/Sdk/8.0.423/dotnet-sdk-8.0.423-win-x64.zip`。构建脚本在解压前用元数据中的 SHA-512 校验归档。
- SDK 解压到 `.runtime/toolchains/dotnet`；真实 `dotnet publish` 目标为 win-x64、self-contained、managed single-file、非 trimmed。为避免 native self-extraction 写到项目外，五个 WPF native sidecar 与 EXE 一起留在 `.runtime/dist/launcher`。
- 最终 `HoudiniIntelligenceLauncher.exe` 为 153,402,752 bytes（146.30 MiB），发布目录共 6 个文件、161,617,720 bytes；SHA-256 为 `cfcbbb5ec78706947c117af50ff3a429ca303d15c96f596c39dab070043b1547`。构建脚本与独立复核均以 `--smoke-test` 退出 0，未启动 PowerShell GUI 或真实 Houdini。
- `.runtime/toolchains`、downloads、cache、build 与 dist 均由既有 `.runtime/` 规则忽略；源码、SDK 和产物均未暂存、提交或推送。

### 下载、权限与构建过程中遇到的问题

- 首次在受限 shell 用 `Invoke-WebRequest` 获取官方安装脚本时返回“基础连接已经关闭：接收时发生错误”；允许本任务的官方网络请求后安装脚本成功下载，但其内部解析/下载数分钟无输出且工具链目录没有字节进展。已解决：终止该构建，改为解析 Microsoft 官方 release metadata 并使用明确 SDK ZIP，不安装全局组件。
- PowerShell `Invoke-WebRequest` 下载大 ZIP 初期吞吐很低；终止父构建后遗留三个本轮子 PowerShell 进程持有 ZIP。普通 `Stop-Process` 返回 Access denied。已解决：按 PID 与启动时间精确核对后，仅提权终止 33424、28528、25812；构建脚本改用 Windows 自带 `curl.exe --continue-at -` 续传，保留完整 SHA-512 校验。未结束其他共享进程，未删除部分归档。
- 第一次 publish 出现一条 `ProjectRootLocator.cs` 的 CS8600 nullable warning。已解决：用显式父目录空值分支替代可空赋值，移动项目根测试继续通过；第二次 publish 无 warning。
- 一次跨文件 `apply_patch` 因上下文放错文件未应用；已用两个精确上下文重新应用。一次精确 unittest 命令误用不存在的测试类名，随后读取实际 `LauncherBackendIntegrationTests` 后重跑。两项均已解决，没有产品改动丢失。
- 一次 WPF 命令用 Windows PowerShell 5.1 默认 ANSI `Get-Content` 误读无 BOM UTF-8 中文，产生伪 XML 错误。已解决：按生产路径改用 `File.ReadAllText`，XAML 成功加载为 `System.Windows.Window`，`HoudiniComboBox` 与 `DarkPickerComboBoxStyle` 可解析。
- 最终只读交付扫描把 Windows 通配符直接放入 `rg` 路径，返回 os error 123。已解决：改用 `rg -g '*.cs'` 重跑并取得完整文件定位；产品与测试结果不受影响。

### UI 与并行回归收口

- Houdini、Bridge 与 backend 选择器改用显式暗色 `ComboBox`、`ComboBoxItem` 和 Popup 模板；选中、Hover、键盘焦点、禁用状态均有独立背景、文字和边框。Houdini/build 或 Bridge source 为主行，路径为可省略副行并保留完整 Tooltip；没有外部图片、AI 图片、第三方 UI/图标或固定安装盘路径。
- 并行完整测试先前唯一失败是旧用例仍要求 `DisplayMemberPath="display"`。本任务没有回退 XAML，而是把该用例改为验证 `BackendPickerItemTemplate`、`HoudiniPickerItemTemplate`、版本/路径/Tooltip、五类高对比资源以及不存在旧 `DisplayMemberPath`。首次新断言把 Hover/Selected 错放在外层 ComboBox 范围，精确测试失败；已把范围扩到协作的 ComboBoxItem 样式，随后通过。

### 自动验证与人工边界

- 新增三个 EXE/UI 精确回归 3/3；最终合并 launcher/backend/普通 Codex 配置定向 33/33；五个 PowerShell 文件 AST 0 错误；真实 Windows PowerShell 5.1 WPF XAML 加载通过；便携假项目根测试通过；真实 publish 与 smoke-test 通过。`git diff --check` 退出码 0，仅报告共享工作树既有 LF→CRLF 提示。
- 没有再次运行完整测试：共享任务已按“完整测试最多一次”执行 637 项，本节只收口其唯一 launcher 旧断言并运行 launcher 定向测试。
- 仍需人工验证：双击 EXE、真实多 Houdini 版本选择、100%/125%/150%/200% DPI、最小窗口长路径、鼠标与键盘 Hover/Focus/Popup、黄色可启动/红色禁用、剪贴板提示，以及最终真实 Houdini/Panel 生命周期。自动测试从未启动 Houdini GUI。

## Panel 发送后 Houdini 视窗失焦（2026-07-18）

### 真实问题与代码路径定位

- 用户观察到 Panel 交互后，其他 Houdini 视窗有时无法拖动或操作，必须最小化并恢复 Houdini 才恢复。真实 Houdini GUI 本轮未启动，因此离线测试不能冒充宿主级复现；该边界继续保留为人工验收项。
- 可确定的代码级风险路径是从输入框用 `Ctrl+Enter` 启动或追加 Turn：快捷键回调尚未返回时，`_refresh_controls()` 会禁用当前持有焦点的输入框，随后代码又主动调用 `clearFocus()`。这会在按键释放前强制产生 focus-out，可能让 Houdini 宿主输入状态失配；最小化/恢复后恢复的现象与该机制一致，但仍需真实 GUI 确认。
- 四条相邻路径已逐项排查：附件选择器已经是 `DontUseNativeDialog + NonModal + show()`；审批卡只是 Panel 内嵌控件；流式刷新只更新消息与滚动条；`closeEvent()` 会停止 Panel/ConversationView 自有计时器并释放本地客户端。它们均没有模态 `exec`、全局 event filter、鼠标/键盘 grab、`activateWindow()` 或 `raise_()`，没有证据支持把审批卡当成模态根因。

### 最小修复行为

- 删除 Turn start/steer 成功发出后的两次 `input_edit.clearFocus()`，不再主动清空 Houdini 主窗口内的当前 focus widget。
- 仅把文本编辑器的 enabled 状态与发送按钮、附件控件的请求锁分开：start、steer 或限定 session 对账 pending 时输入框保持启用，确保 `Ctrl+Enter` 的 key-release 仍由原控件收完，也允许继续编辑下一条草稿；发送按钮仍禁用，`_send()` 的既有 pending guard 仍阻止重复 Turn。
- 附件选择、审批卡、流式刷新和 Panel 关闭生命周期未增加任何焦点接管或窗口激活代码，也未新增 event filter、计时器、状态机或模态 UI。

### 验证结果与人工验收

- 定向运行 `tests.unit.test_panel_wiring`、`tests.unit.test_conversation_tool_activity`、`tests.unit.test_p1_assets`，92/92 通过，用时 0.952 秒。
- 回归覆盖 start/steer pending 时输入框保持启用、发送按钮禁用、重复 `_send()` 不产生第二个请求、全 Panel 源码不再调用 `clearFocus()`；既有附件非模态、审批卡、流式计时器和 closeEvent 生命周期测试同时通过。
- 未运行完整测试套件，也未启动 Houdini。人工验收需完整退出 Houdini 后由 launcher 重启：分别用 `Ctrl+Enter` 和发送按钮启动/追加 Turn，并在发送中、持续流式输出、附件选择器打开/取消、审批卡显示/允许/拒绝、Panel 关闭再重开后立即拖动 Scene View 与 Network View；预期不再需要最小化 Houdini 才能恢复操作。

## Houdini Visual Research Skill 规则同步（2026-07-18）

### 实际验证不顺

- 默认 Python 直接运行 `skill-creator/scripts/quick_validate.py` 时，在进入 Skill 内容检查前因缺少 PyYAML 失败：`ModuleNotFoundError: No module named 'yaml'`。
- 改用项目根下的一次性依赖目录后完成原校验脚本；下载 PyYAML 时发生一次 `files.pythonhosted.org` 连接超时重试，但随后自动恢复。首次清理保护因 `E:/` 与 `E:\` 的文本形式不同而拒绝删除，规范化并核对父目录及固定临时目录名后安全清理；未影响用户文件。

### 验证结果

- `quick_validate.py` 最终返回 `Skill is valid!`，临时验证目录已清理。
- 既有四个静态案例 4/4 通过；新增 `hia_node_help` 调用、交付物路径语义、Panel/Bridge 职责边界检查 3/3 通过。复杂视觉研究深度与简单 Box 直达行为均保持。
- 未运行完整项目测试，未启动 Houdini 或修改场景。

### 最终审计补充

- 合并 `rg` 正则首次使用 PowerShell 双引号，内部的 `node_type="Category/name"` 被错误拆成路径参数并返回 os error；该命令只读且没有写入。改用单引号正则后重跑通过。

## HIA MCP V2 `hia_node_help` 限定名兼容（2026-07-18）

### 真实根因与最小修复

- 真实失败请求为 `node_type="Cop/wrangle"` 且未传 `category`，旧 runtime 只接受 `node_path` 或 `category + node_type`，因此在动态 Houdini 目录查询前返回 `INVALID_ARGUMENTS`。改成 `category="Cop", node_type="wrangle"` 后成功；当日 132 次 `hia_node_help` 中 129 次成功，问题不是 `QUEUE_FULL`、动态帮助或参数模板故障。
- `_node_help` 保留现有 `node_path` 与 `category + 裸 node_type` 路径；仅在没有 `node_path` 时，把 `node_type="Category/name"` 按第一个 `/` 拆分并 trim，再进入原有 `nodeTypeCategories()/nodeTypes()` 动态查询。已给 category 且前缀大小写不敏感一致时也兼容；空段或前缀冲突返回结构化 `INVALID_ARGUMENTS`。
- `hia_node_help` 工具描述明确列出三种输入形式。没有增加别名数据库、节点白名单、工具、队列或帮助缓存，也未修改 FXHoudiniMCP。
- 根据用户纠正，本轮没有写入任何最终输出目录限制或 project-root confinement。`hia_capture_viewport` 仍只写 `.runtime/cache/screenshots`；最终渲染、EXR、视频、USD、模拟缓存和导出仍由 `hia_execute_hom` 使用用户明确路径或 launcher 提供的 `HIA_RENDER_OUTPUT_DIR`，可位于项目外普通本地目录；MCP 不新增路径验证器或输出管理器。

### 验证

- 定向命令：`python -B -m unittest -v tests.unit.test_hia_mcp_v2_runtime tests.unit.test_hia_mcp_v2_protocol`
- 结果：17/17 通过。新增回归证明 `Cop/wrangle`、`category="Cop" + node_type="wrangle"` 以及相同冗余前缀返回完全一致；`Cop/`、`/wrangle` 和冲突的 `Sop + Cop/wrangle` 均返回 `INVALID_ARGUMENTS`。既有 viewport cache 路径、16 工具协议、批量 HOM 和取消边界同时通过。
- 最终只读审计首次使用了当前 Windows PowerShell 不支持的 `|| $true` 分隔写法，命令在执行任何子命令前解析失败；随后一次含 Markdown 反引号的双引号 `rg` 定位命令也因 PowerShell 字符串未终止而在解析期失败。两者均改为顺序执行和单引号模式后成功，未写文件。期间并行 launcher 任务新增了自己的 `HIA_RENDER_OUTPUT_DIR` 传递改动，本任务完整保留且未修改这些文件。
- 未运行完整测试套件，未启动 Houdini GUI，未暂存、提交或推送。

## 启动器最终交付输出目录（2026-07-18）

### 实现边界

- WPF 环境卡新增可编辑的“最终渲染输出目录”和 Windows Shell 原生文件夹选择按钮，文案明确覆盖最终 EXR、图片、视频、USD、导出和模拟缓存，并说明它与内部截图、预览、临时缓存不同。旧设置缺少 `render_output_dir` 时按空值读取；成功启动前仍只写项目本地 `.runtime/launcher/settings.json`。
- 空值解析为项目 `.runtime/cache`。非空值可位于插件项目外任意普通、用户可写的本地绝对目录；拒绝相对、UNC/设备、ADS、盘符根、不可用盘、现有文件、Windows 目录和所选/明显 Houdini 安装目录。不存在的目录只在用户浏览新建或点击启动时调用一次 `Directory.CreateDirectory`，不枚举、删除或清理已有内容；可写性使用 `DeleteOnClose` 自有探针。
- WPF 启动 `scripts/launch-houdini.ps1` 子进程时只在该 `ProcessStartInfo` 环境设置解析后的 `HIA_RENDER_OUTPUT_DIR`，不修改当前/系统环境，也不增加生命周期命令行参数。唯一生命周期再次复用同一解析器并分别注入 Bridge 与 Houdini。既有两处 `HIA_CACHE_DIR = $cacheRoot` 及 screenshots/previews/tmp 目录保持不变，两变量不互相赋值。
- 本轮没有修改 Panel、Bridge、MCP、HOM、Skill、会话历史、Fast 模式或断线 supervisor。

### 实际问题与处理

- 首次 XAML 补丁的两个新 `RowDefinition` 因上下文过宽误加到 Houdini picker `DataTemplate`。在运行测试前通过局部 diff 发现并移回环境卡；真实 WPF XAML 随后成功解析，两个新控件类型正确。
- 实现中一度把自定义最终目录误收紧为项目根后代，与最终交付物需求冲突。收到纠正后立即删除该限制，并把回归改为证明“位于假项目根外、但仍是普通本地可写目录”的路径能够解析和创建；项目内部缓存边界未改变。
- Windows PowerShell 5.1 没有可靠的原生 WPF `OpenFolderDialog`，且现有边界禁止重新引入 WinForms。处理为使用零依赖 Windows Shell `BrowseForFolder`，同时保留可键盘编辑的 TextBox；Server Core/禁用 Explorer Shell 与真实文件夹对话框仍需人工验收。

### 验证

- 首轮新增/相邻精确测试 8/8，通过后最终 launcher 定向与两项 backend/settings 相邻回归 27/27 通过，用时 8.700 秒。
- PowerShell AST：`hia-launcher.ps1`、Core、WPF、`launch-houdini.ps1`、`build-launcher.ps1` 共 5/5 通过；Windows PowerShell 5.1 `XamlReader.Load` 与 `RenderOutputTextBox`/`BrowseRenderOutputButton` 控件查找通过。
- 覆盖空值默认、项目外普通本地输出、按需创建/可写、保留已有文件、相对/设备/Windows/Houdini 路径拒绝、旧设置兼容、设置四字段、WPF 文案、纯子进程环境传递及 `HIA_CACHE_DIR`/`HIA_RENDER_OUTPUT_DIR` 静态隔离。
- 未运行完整测试套件，未启动 EXE 或 Houdini GUI。人工待验收：真实 Shell 文件夹选择/新建、长路径、清空后默认目录、黄色缺目录允许启动、红色非法目录禁用启动，以及 Houdini 子进程看到两个独立环境变量。

## Codex 快速模式、历史会话与最小断线恢复（2026-07-18）

### 真实根因与最小修复

- Codex 0.144.3 的实时 `model/list` 已提供 `serviceTiers/defaultServiceTier`，固定协议也已支持 `thread/start`、`thread/resume`、`turn/start` 的 `serviceTier`；旧 Bridge sanitizer 丢弃这些字段，三条请求也未转发，因此过去只能调 reasoning effort，并不是真正的 Fast。现在速度选择器只显示当前模型实时公布的档位与说明；“标准”显式传 `null`，实际 tier ID 不写死，reasoning effort 仍独立。
- 固定协议已有稳定的 `thread/list`、`thread/name/set` 与 `thread/name/updated`，但严格 allowlist 和 Panel 都未接入，只能手填 UUID。现在 Bridge 只请求当前项目 cwd、未归档、按 `recency_at desc` 的首 20 条，再二次过滤 cwd 和最小展示字段；Panel 显示 `name || preview || 短 ID` 与更新时间，点击恢复，名称由 Codex 持久化，完整 UUID 只用于复制/高级提示，没有本地聊天或别名数据库。无当前会话时仅自动尝试恢复最近一条一次；重开 Panel 时只读当前会话一次，历史只创建最近 100 条可读消息控件，避免长 Thread 卡住 Houdini UI 主线程。
- Panel 原先初始 health 只请求一次，event 网络失败只固定 1500ms 继续 poll，不做 health/session/read 对账。现在仅对真实网络错误用 Panel 自有单次 QTimer 按 0.5/1/2/4/8 秒有限重连；保留草稿、附件、thread 和 event cursor，成功后只同步权威 session/read，绝不重放状态不明的 Turn、HOM 或场景写入。现有 stdio client 不能安全原地重启 app-server，因此进程退出只明确要求重启 launcher，不新增 supervisor。
- 输出规则明确区分内部数据与用户交付物：插件源码、内部缓存、自动截图/预览/附件/临时/诊断留项目内；用户明确指定的最终 render/EXR/video/USD/模拟缓存/导出可写所选普通本地项目外目录，未指定才用 `HIA_RENDER_OUTPUT_DIR`（默认 `.runtime/cache`），并必须报告实际最终路径。`hia_capture_viewport` 的项目缓存边界未改变。

### 实际不顺与处理

- 合并渲染规则后，四个旧 developerInstructions 回归仍要求旧截图措辞和 900 字符上限；生产指令已变为 970 字符。测试改为验证新的项目内缓存/项目外交付物边界和 1000 字符上限，随后 Bridge session 28/28 通过。
- Panel 首次定向为 62/63：新增重连测试的 `_BridgeClientShim.get_session` 仍要求显式 context，而生产客户端已有默认值；只同步测试 shim 后 63/63 通过。
- P1 静态回归首次 33/34：旧断言要求单行 `start_thread(model=...)`，与新增独立 `service_tier` 参数后的多行调用不兼容；改为验证模型、effort、service tier 三项动态接线后 34/34 通过。
- launcher 并行测试曾短暂出现“项目外最终目录必须报错”的冲突断言，定向一度 36/37；没有回退并行文件。该断言由 launcher 任务按用户交付物规则移除后，launcher/Bridge 生命周期 37/37 通过。
- 最终只读状态审查发现三处可复现竞态：session reconcile 网络失败会遗留 UI 锁；重连 health 后的冗余 session 请求可能晚到污染新 Turn；threads 先于 model/list 返回会让自动恢复意外使用标准速度。已分别改为在重连前释放关联 token、只信任 health 内嵌 session 并只读 thread/read、等待模型目录成功或明确失败后再自动恢复；活动 Turn 遇到 app-server 退出也会立即冻结为静态“状态待确认”，不伪装完成。

### 定向验证与人工边界

- service tier Bridge/session/HTTP/client：54/54；Panel wiring 最终：69/69；conversation/P1 静态回归：34/34；Bridge session：28/28；Bridge HTTP/client/stdio/协议契约：65/65；launcher/Bridge 生命周期/HIA MCP V2 生产接入：47/47，均通过。`git diff --check` 退出 0，仅有共享工作树既有 LF→CRLF 提示。
- launcher 安全截图缓存清理收口且所有并行 Agent 停止写入后，唯一一次完整套件 `python -B -m unittest discover -s tests -t . -v` 为 661/661 通过，用时 32.936 秒；覆盖当前共享工作树全部 35 个修改文件，无 failure、error、超时或跳过。
- 未启动真实 Houdini GUI。人工验收需由 launcher 全新启动 Houdini：确认速度选择器来自实时 model/list 且标准/快速分别作用于 start/resume/turn；历史列表、重命名、关闭重开与最近会话一次恢复正常；临时中断 Bridge 时草稿/附件保留且只读对账、不重复建节点；app-server 退出提示重启 launcher；显式项目外最终渲染实际写入所选目录并在回复中报告路径。

## 启动器手动截图缓存清理（2026-07-18）

### 安全边界与已解决问题

- 首方 `hia_capture_viewport` 的 viewport 与 flipbook 实现均只生成 `.png`，因此清理白名单只有大小写不敏感的 PNG。目标每次只从启动器解析出的 project root 重新计算为 `.runtime/cache/screenshots`，确认计划中的目标必须与该规范绝对路径精确相等；不使用前缀判断、用户目录变量、固定盘符、通配枚举或递归删除。
- 初始实现草案在确认后重新枚举目录，存在把确认后新出现截图纳入删除范围的语义风险。已解决：预览固定记录候选的完整路径、大小和 UTC 修改时间；确认后仅逐项复核并删除这份计划中的未变化普通 PNG，新出现、变化、子目录、reparse 或其他扩展对象全部跳过，不转向其他目录重试。
- project root、`.runtime`、`cache`、`screenshots` 四级在预览、执行及每个文件删除前检查 reparse-point 属性；任一级 junction/symlink 都立即拒绝。删除使用逐文件 `System.IO.File.Delete`，不删除 `screenshots` 目录本身，也不触碰 previews、tmp、attachments、diagnostics、源码或 `HIA_RENDER_OUTPUT_DIR`/最终交付目录。

### 定向验证与人工边界

- 新增正常清理回归证明：只删除 screenshots 第一层 `.png`/`.PNG`；项目外同名诱饵、JPG/TXT、嵌套 PNG、previews、tmp、attachments、diagnostics 和项目外最终输出全部保留；路径不匹配、逃逸计划与盘符根均零写入拒绝。四个层级的真实 Windows junction 测试均 fail-closed，junction 目标文件保持不变，并在测试 `finally` 中仅解除测试自有链接。
- 单项安全回归 3/3 通过；最终 `python -m unittest tests.unit.test_launcher_preflight -v` 为 28/28，通过时间 11.186 秒。五个 launcher/lifecycle/build PowerShell 文件 AST 均 0 错误；XAML XML 解析为 `Window` 且能找到 `CleanupScreenshotsButton`，定向测试中的 Windows PowerShell 5.1 `XamlReader.Load` 也通过。
- `git diff --check` 退出码 0，仅显示共享工作树既有 LF→CRLF 提示。本轮未运行完整测试套件，未启动 EXE、Houdini 或真实 GUI。仍需人工点击验证确认框默认“否”、取消零写入、长目标路径显示，以及真实缓存有文件时的删除/释放/跳过计数状态卡。

## Houdini 崩溃后历史会话不可见（2026-07-19）

### 真实根因与最小修复

- 用户的主会话 `019f7895-ab3c-7ac0-a15b-4bb48474452d` 仍未归档，rollout 文件约 101 MB，最后一行 JSON 有效；聊天内容没有因 Houdini 崩溃丢失。
- Codex 状态库把该会话 cwd 记录为 Windows 扩展路径 `\\?\E:\houdini-intelligence-agent`，Bridge 却向 `thread/list` 传普通路径 `E:\houdini-intelligence-agent`。app-server 在数据库层精确筛选后返回空数组，所以 Panel 只能显示“暂无历史会话”。
- Bridge 现在让 `thread/list` 同时精确匹配普通 cwd 与 Windows `\\?\`/`\\?\UNC\` 等价形式，并保留本地规范化二次过滤；`modelProviders: []` 保留同项目不同 provider 的主会话，`useStateDbOnly: true` 避免刷新时扫描或修复约 101 MB rollout。`sourceKinds` 继续省略，沿用 0.144.3 的 interactive 默认值，不把 subagent 混入历史。没有修改会话文件、状态库、Thread ID、聊天存储或 Panel UI。
- 完整 `thread/read(includeTurns=true)` 的真实 JSON 约 48 MB，而 Panel HTTP 的固定响应上限是 4 MiB；旧 `resume_thread()` 还会先收一份完整 `thread/resume`、再读一份完整 `thread/read`，最后把两份都塞进 HTTP 响应，接近 96 MB，必然无法在 Panel 打开。现在 Bridge 直接使用 0.144.3 明确保证含完整 `turns` 的 `thread/resume`，只按原顺序投影 Panel 实际消费的全部 `userMessage.content(text/localImage)` 与 `agentMessage.text`；工具执行等巨量内部项不穿过 HTTP，但 app-server 已恢复的完整上下文和真实 Thread ID 不变。普通 `thread/read` 使用同一无持久化投影，Panel 也取消旧的最近 100 条截断，不建立本地数据库、索引器或 rollout 解析器。

### 实际不顺与处理

- 最初候选取消服务端 cwd 后只取全局最近 20 条再本地过滤；审查发现其他项目若占满首屏，本项目仍会再次显示为空。固定 0.144.3 schema 证明 cwd 支持字符串数组，因此改为一次传普通/扩展两种精确形式，没有增加分页器、索引器或恢复框架。
- 首次裸 app-server 烟雾沿用默认 model provider，只返回 9 条 `openai` 历史，目标 `hia_chatgpt_http` Thread 未出现；`thread/read` 当时已能读取目标，说明会话本身有效。按 0.144.3 稳定协议补 `modelProviders: []` 后重测，双 cwd 数组、普通 cwd、扩展 cwd 和无 cwd 均把目标列为第 1 条。最终产品采用双 cwd 数组，继续由服务端排除其他项目。
- 一次精确烟雾脚本因漏掉右括号在 Python 解析阶段退出；最终投影烟雾的首次临时脚本又因未把项目 `src` 加入 `sys.path`，在导入 Bridge 前退出。两次都发生在 app-server 启动和数据库访问之前；修正脚本后复用项目内隔离状态库副本完成验证，没有修改原会话数据库。

### 验证与人工边界

- 真实 Codex app-server 0.144.3 列表烟雾：最终列表 20 条，目标 `019f7895-ab3c-7ac0-a15b-4bb48474452d` 位于第 1 条，subagent 为 0；目标数据库行烟雾前后完全一致。
- 使用 Panel 当前生产 `thread/resume` 参数的隔离烟雾成功恢复同一 Thread：原始 resume 含 6 个完整 Turn，耗时 5.750 秒；Bridge 投影后是 172 条可见聊天（27 条用户、145 条 Codex），JSON 仅 59,688 字节，低于 4 MiB 上限，且 session 中选中的仍是原 Thread ID。此前单独的完整 resume/read 原始响应分别为 48,089,328/48,088,824 字节、耗时 5.640/5.657 秒，证明问题是 Panel 传输与旧重复响应，不是聊天内容损坏。烟雾没有启动 Turn、重放 HOM 或启动 Houdini。
- 首轮历史定向 10/10 通过；最终相关模块 `test_bridge_session`、`test_panel_wiring`、`test_bridge_http`、`test_bridge_client_reply`、`test_codex_stdio_client`、`test_codex_protocol_contract` 合计 166/166 通过。新增回归覆盖一次 resume、剔除超过 4 MiB 的工具明细后投影仍低于上限、全部正文及顺序、105 条历史不截断、继续 Turn 使用同一 Thread ID，以及既有 Bridge/Panel/协议行为。
- 尚需完全退出当前 Houdini/Bridge 后由 launcher 重启，点击历史会话“刷新”，确认标题“我需要你做一个复杂的木屋”出现；点击“打开”后应从最早问题到最新回复完整显示，并可直接继续该会话。运行中的旧 Bridge 不会热加载本次修复。

### GUI 复验失败后的二次根因与修正

- 用户于 18:13:06 完整重启后，Panel 仍显示“暂无历史会话”，并重复给出通用“历史会话暂不可用”提示。取证时该次 Houdini、Bridge 和项目 Codex 进程均已退出，随机 loopback endpoint/token 从不落盘，session 目录也没有 Bridge 日志，因此没有把 connection refused 冒充原业务错误。项目本地 app-server trace 只读证明 pid 15188 在 18:14:42 收到一次 `thread/list`，18:14:44–48 又收到四次，说明当时请求确实到达 app-server；trace 不保留对应 Bridge HTTP structured_error。代码审查同时确认 Panel 的 `threads` 失败分支会丢弃已有的结构化 code/field。
- 使用 launcher 的 HIA MCP V2 strict-config、真实 `BridgeSession.list_threads()` 和隔离状态库副本复现到准确错误：`INVALID_THREAD_LIST_RESPONSE`，`field=preview`。app-server 实际已返回 20 条，木屋 Thread 是第 1 条且 preview 仅 12 字；第 2/3 条 preview 各约 17.2K，第 4/5 条约 10.1K，多条还含 LF、TAB 等控制字符。旧 Bridge 在处理第 2 条时把展示摘要当身份字段拒绝，导致整个成功列表变成 HTTP 502。
- preview 现在仅要求 app-server 返回 string，然后对前 8192 字符做控制字符空格化、空白归一和最终有界截断；一条异常摘要不再拖垮整批。Thread ID、cwd、updatedAt/recencyAt 等身份字段仍严格验证，没有改写数据库、建立索引或解析 rollout。Panel 若再次收到 `threads` 失败，只显示脱敏、短小的 code 与 field，不再吞掉根因，也不显示 RPC message、token 或正文。
- 第一次 strict-config 复现副本缺少真实 CODEX_HOME 中的项目 trusted 条目，导致禁用的兼容 MCP 表缺少项目配置基底并在 initialize 前报 `invalid transport`。把隔离 config 对齐真实 launcher 的 trusted 状态后，未经删改的生产 strict-config 正常启动；该不顺只影响隔离烟雾，没有修改产品配置或原会话数据库。

### 二次验证

- 真实 strict-config + Bridge HTTP + Panel 固定 4 MiB `HttpTransport` 本地闭环：`GET /v1/threads` 为 HTTP 200，返回 20 条、36,642 字节，木屋 Thread 索引为 0；等价于点击“打开”的 `POST /v1/session` 为 HTTP 200、59,698 字节，恢复 6 Turn/172 条投影消息。无 Houdini 的 Panel 生产 renderer 实际创建 172 条用户/Codex 消息，session 仍选择原 Thread ID。
- 修复后首轮精确测试 10/10；最终相关模块 `test_bridge_session`、`test_panel_wiring`、`test_bridge_http`、`test_bridge_client_reply`、`test_codex_stdio_client`、`test_codex_protocol_contract`、`test_bridge_main_lifecycle`、`test_hia_mcp_v2_production_integration` 合计 190/190 通过。未运行完整套件，未启动 Houdini、Turn 或 HOM，未修改原会话数据库。
- 仍需由 launcher 再启动一次真实 Houdini 做最后 GUI 验收；新 Panel 若列表仍失败，会直接显示可继续定位的脱敏 code/field，而不是通用提示。

## 长历史会话自动恢复先于 Bridge 超时（2026-07-20）

### 真实根因与最小修复

- 真实 GUI 中 Bridge 的 loopback health 仍能快速返回 401，但 Panel 的 `session_auto_resume/session_resume` 在 15 秒后先产生 `NETWORK_TIMEOUT`。恢复请求并非普通网络探测：Bridge 会同步等待 Codex `thread/resume` 并完成完整聊天投影后才返回。
- 当前 launcher 路径在 `main.py` 显式把 Codex request timeout 设为 45 秒；`codex_stdio.py` 的 30 秒只是未使用的构造默认值。因此旧契约是 Panel 15 秒小于真实 Bridge/Codex 45 秒，35 秒也仍会留下同一竞态。现在只有 `POST /v1/session` 的 `start/resume/read` 使用 50 秒 Panel 上限，给 45 秒结构化 `CODEX_REQUEST_TIMEOUT` 留出返回余量；health、interrupt 仍为 15 秒，events 为 20 秒，session reconcile 为 5 秒。
- 会话上下文的 `NETWORK_TIMEOUT/CODEX_REQUEST_TIMEOUT` 现在显示“会话恢复（或启动）超时，会话服务暂未完成”，不再把长恢复误称为 Bridge 断线；auto resume 与手动 start/resume 失败都会立即释放 session action 锁，可直接手动重试。真正的 `NETWORK_ERROR` 仍走原有网络错误显示。
- 本轮没有改动 `BridgeSession.resume_thread/read_thread`、4 MiB HTTP 上限或历史投影规则。同一 Thread ID、全部既有可渲染用户/Codex 正文、localImage、原顺序及继续同一 Thread 的行为保持不变；没有新增截断、摘要、数据库、索引器或恢复协议。

### 完整性、纯只读验证与测试

- 既有真实 strict-config + Bridge HTTP 闭环基线仍为 Thread `019f7895-ab3c-7ac0-a15b-4bb48474452d`、6 Turn、172 条 Panel 可见消息、59,698 字节且低于 4 MiB。当前回归把 Bridge 投影与 Panel renderer 的无截断覆盖统一到 172 条，并继续断言同一 Thread ID、消息首尾/顺序、用户文本、localImage、Codex 回复及后续 Turn 仍使用该 ID。
- 按本轮“真实历史闭环必须纯只读”的硬条件，没有启动可能更新 SQLite WAL/SHM 的真实 app-server，也没有备份替换、迁移、恢复或覆盖 state DB。只读检查确认目标 rollout 当前为 353,495,389 字节且末行 JSON 有效；测试前后 `state_5.sqlite`、`state_5.sqlite-wal`、`state_5.sqlite-shm` 与目标 rollout 的长度、mtime、SHA-256 完全相同。对应 SHA-256 为 `C83179F8FDED7CB70AAB6D182EB14E584EB5CB55418D292F8404215BA3917A61`、`099EC83B087DE897868E43AAFB3A1134E5E69E0DAF0F97B9937BF46CCAC20CA8`、`5D694C1311DA057919D278C26FE03E07B14FCF5DE4E19ED4769FC3DF0DA7A172`、`E57E3C1A329655942BFA9806A6803FBFB922E4F241EC2DDE10A663854402C5DB`。
- 精确回归使用可控单调时钟，不真实等待 50 秒：推进到旧 15 秒边界之后的成功响应仍被接受；推进到 45 秒的结构化 app-server 超时仍在 Panel deadline 前准确返回；auto resume 失败后可手动打开；短请求上限不变。相关模块 161/161 通过；本轮唯一一次完整套件 670/670 通过，用时 31.302 秒。
- 未启动 Houdini、未执行 Turn/HOM、未修改场景、会话数据库或聊天记录。真实 GUI 尚需完整退出 Houdini/Bridge 后由 launcher 启动一次：等待历史自动恢复；若未自动完成，在历史列表选择木屋会话并点击“打开”，确认最早到最新内容完整出现且可继续同一 Thread。

## 原生 Goal、子任务团队与三段 Turn 用时（2026-07-20）

### 根因与最小接入

- Codex 0.144.3 已稳定提供 `thread/goal/set|get|clear` 与 `updated/cleared`，但严格 allowlist、Bridge 和 Panel 尚未接入。现在 Goal 只保存在真实 Codex Thread；Bridge 仅转发当前选中 Thread，Panel 可读取、保存、清除目标与状态，没有本地 Planner 或 Goal 数据库。
- 原生子任务活动本来已通过既有 `item/started|completed` 中的 `collabAgentToolCall/subAgentActivity` 到达 Bridge，Panel 过去未识别，且其他 Thread 的 turn 事件可能误触发主任务对账。现在右栏只展示协议可观察的任务、状态、工具/错误和公开回复；选择子任务才展开详情，不进入主聊天、不切换主 Thread，也不显示或推断隐藏思维。协议没有“已采纳”字段时明确显示由主任务公开回复决定。
- Panel 在保留现有输入、附件、审批、停止、流式消息和完整历史 renderer 的前提下改为左侧历史任务、中间聊天、右侧 Goal/团队/本 Turn 用时三栏。用单调时钟记录发送→ACK、ACK→首个文本、首个文本→完成；事件仍按既有 Thread/Turn generation 隔离。
- HIA MCP V2 生产模式现在不会构造或启动旧 B2 heartbeat/catalog/五节点轮询；历史 B2 代码仍仅供显式 FX 兼容路径。B4B 历史验收 Panel 文件保留，但移除默认 Toolbar 菜单入口。
- developerInstructions 明确主任务是 lead，只有主代理可调用当前会话 `hia_*`/HOM 并写当前 HIP；子任务只做研究、脚本草案和审阅建议，主任务只保留原生 Goal、决定和短摘要，继续只用 Codex 自动 compaction。
- 根 `AGENTS.md` 新增 10 条通用网络编排规则：新图采用纵向语义主干、同阶段横向分支并向下汇入，MaterialX 同样适用；已有网络继承原方向和风格。未加入固定坐标、自动交叉检测或布局引擎。

### 删减与验证

- 初版曾为团队详情增加一次额外 child `thread/read`、pending 集合、投影解析和失败分支；删减审查确认实时原生事件已经覆盖本轮要求后整层移除，避免重复网络读取和未来恢复框架。另删除两个只写不读的 Goal 镜像状态、重复 completion 计时、与 dict 插入顺序重复的 `_team_order`、一个未读取字段及两项证明性重复测试，并压缩 developerInstructions；现有 helper/state 均有直接调用。
- 首轮 42/42 通过；相关回归第一次仅因 HIA V2 developerInstructions 为 1052 字符、超过既有 1000 字符上限而失败，删减重复措辞后通过。相关协议、stdio、Bridge session/HTTP/client、Panel、P1 与 B4B 回归曾 214/214 通过；最终删减后的直接回归 45/45、AGENTS 10 条规则的 12 项语义检查全部通过，且没有残留“主干固定左到右”规则。`git diff --check` 退出码 0，仅有工作树既有 LF→CRLF 提示。未运行完整套件，未启动 Houdini GUI、Turn 或 HOM。
- 真实 GUI 仍需 launcher 全新启动 Houdini：确认三栏可调整宽度；Goal 对当前 Thread 读写/清除；一次原生子任务的状态、工具错误和最终回复只进入团队栏；完成一个 Turn 后三段用时出现；HIA V2 下不再出现旧 Catalog/Schema/B4B 默认入口。

## Goal 与原生子任务事件竞态收口（2026-07-20）

- `thread/started` 同时用于主 Thread 和带 `parentThreadId` 的原生子 Thread；Bridge 过去会对两者无条件改写当前 `_thread_id`，使主任务 completion 被当成旧 Thread 丢弃并可能永久保持 active。现在 Thread 选择只由显式 start/resume 成功响应更新；子 Thread 启动仍作为团队可观察事件发布，但不再改变主 Thread、Turn 或 Goal。
- Goal GET/SET/CLEAR 现在从 Panel 到 Bridge 显式携带 expected Thread ID；Bridge 在调用 Codex 前校验当前选择，不一致返回 `THREAD_SELECTION_CHANGED`。Goal 动作在途时禁用新建/恢复/历史切换，切换完成后失效旧响应并为新 Thread 重新读取 Goal。Goal 专用等待上限由普通 15 秒改为 50 秒，覆盖 Bridge 45 秒 RPC 上限；health、interrupt、events 等短请求未放大。
- 团队记录绑定创建它的 root Thread，只接受当前 root 发出的 `subAgentActivity` 或其已知 descendant 的后续事件；切换主 Thread 后旧 root/child 的迟到事件不会污染新团队栏。本 Turn 三段用时同时清空。每个 child 的可见回复采用简单 64 KiB 上限，避免原先 1 MiB × 32 的最坏增长。
- “仅主代理负责当前 HIP 写入”继续作为 developerInstructions 的明确行为约束；HIA MCP V2 与 FX fallback 当前都没有 caller lineage，因此本文档和指令不把它表述为代码级隔离或强制安全边界。
- 精确竞态回归 15/15 通过；随后 `test_bridge_session`、`test_bridge_http`、`test_bridge_client_reply`、`test_panel_wiring` 相关模块合计 147/147 通过，用时 9.895 秒。两次中间失败均为测试期望未同步新契约（Turn 返回值形状、Goal 拉取期间控件应保持禁用），修正测试后产品回归全绿。本项未运行完整套件，未启动 Houdini GUI、Turn 或 HOM。
- 仍需真实 GUI 验收：主 Turn active 时启动一个原生子任务并确认主 completion 正常回到 idle；在两个历史 Thread 间切换并确认 Goal 不串线、旧子任务不进入新团队栏、用时显示清空。

## 整仓外部超时停在 Bridge steer 附近的顺序诊断（2026-07-20）

- 两次整仓命令被外部 120/360 秒时限终止时，最后可见位置在 `BridgeHTTPTests.test_steer_endpoint_appends_to_the_same_active_turn` 附近；但当前磁盘状态下无法把它复现为 unittest 顺序依赖。目标单测、完整 HTTP 模块、紧邻前序模块以及真实 discover 前缀均正常结束。
- 精确验证：HTTP 模块 16/16；`bridge_client_reply` + steer 19/19；全部六个 discover 前序模块 + steer 99/99；相同前序 + 完整 HTTP 114/114；按 discover 真实顺序从第 0 项执行到目标 index 109 为 110/110；先导入全部 708 项后单跑目标通过；同一进程连续 steer 40/40。完整 HTTP 模块结束后 `threading.enumerate()` 仅剩 MainThread，没有 HTTP、stdout 或 stderr reader 线程残留。
- 每个 HTTP 用例使用独立 `LoopbackHTTPServer`、`BridgeSession` 和 fake app-server 子进程；正常 teardown 会 shutdown/close/join HTTP server，再由 `session.close()` 关闭 stdin 并回收子进程。fake 的 turn/approval/goal 模块状态只存在于该独立子进程，没有跨用例共享。当前没有证据支持修改 Bridge 业务语义或增加 watchdog。
- 诊断期间短暂观察到外部整仓调用遗留的测试父 Python 与一个 fake app-server 子进程；它们没有监听 socket，随后由原调用方退出。由于当前权限无法读取命令行，不能把它定性为仓库泄漏；现象更符合外部 timeout/输出管道或并行测试调用留下的现场。下一次若再发生，应在终止前抓取活进程 Python 栈，确认是否卡在唯一两个理论无界点：`server.shutdown()` 或 `CodexStdioClient.close()` 获取 `_write_lock`。在没有该证据前不猜改 teardown。
- 本项未重跑完整套件、未修改测试或生产代码，仅追加本诊断记录；没有启动 Houdini、Turn 或 HOM。

### 正确 E 盘写权限下的最终结论

- 独立现场进一步确认：根线程的审核沙箱把 E 盘项目视为只读，`test_steer_endpoint_appends_to_the_same_active_turn` 在 `.runtime/attachments` 创建 `TemporaryDirectory` 时收到 `WinError 5`，而 Python `tempfile` 会继续尝试随机目录名，所以外部观察表现为长时间停在该测试附近。前述两次 120/360 秒 timeout 是环境权限假象，不是 Bridge、steer、HTTP teardown 或产品顺序依赖。
- 在允许按既有测试行为写入项目 `.runtime` 的正确权限环境中，仅运行一次最终完整命令 `python -B -m unittest discover -s tests -t .`：708/708 通过，用时 32.414 秒，无 failure、error 或 timeout。
- 测试结束后 `.runtime/attachments` 中没有 `bridge-http-test-*` 或 `bridge-steer-test-*`，`.runtime/tmp`、`.runtime/cache/tmp` 与 `.runtime` 下也没有本次创建的近期测试临时目录；临时内容均由各测试的 `TemporaryDirectory`/cleanup 自行清理。本次没有手工删除任何文件。

## 目标专注模式与最小崩溃恢复（2026-07-21）

- “目标专注模式”默认关闭：普通聊天不创建自动恢复 checkpoint，Houdini 异常退出也不自动重启或续做。开启时必须绑定当前 exact Thread 且原生 Goal 为 active；状态以项目内最小 JSON 随 Thread 持久化，关闭开关不会删除 Goal。
- 连续异常退出的恢复优先级为：第一次 AI 阶段 checkpoint，第二次当前 launcher session/当前 Houdini PID 的 crash HIP，第三次稳定 checkpoint 并要求改用替代或降级方案；下一次停止。每个候选都复制到当前 session 的 `recovery` 后用所选 Houdini 的 hython 有界 load probe；总自动重启另有 6 次上限，退出码 0 不重启。
- AI checkpoint 仅在专注开启且有意义阶段成功时创建；同目录 sidecar 只记录版本、exact Thread ID、Goal 绑定哈希和 HIP 文件名。`focus-mode.json` 与 sidecar 都不保存 Goal objective、HOM 或参数。Goal 绑定只对规范化后的 `objective + tokenBudget` 计算 SHA-256，status/usage 不参与；这两个稳定字段变化会立即关闭专注并要求用户重新开启，旧 Goal 的 marker 不能再作为专注恢复候选。sidecar 写入失败不会重试已完成的场景写入。异常退出后先 interrupt，再在启动恢复 Houdini 前有界等待同一 Thread 权威 idle；未确认 idle、Goal 非 active、Thread/Goal 不匹配或候选无效均停止并保留文件。只有通过 probe 的新 checkpoint 才重置连续崩溃计数，恢复 Turn 每次只发送一次且不保存或重放 HOM/参数。
- 首次相关回归的唯一失败是 HIA V2 developerInstructions 增至 1136 字符、超过既有 1000 字符上限；删去重复措辞并保留专注规则后为 991 字符。最终 Bridge/Panel/HIA runtime/launcher 回归 224/224 通过，用时 22.405 秒；覆盖 OFF/ON 持久化、active Goal 门控、exact Thread/Goal sidecar、Goal 内容变化失效、status/usage 更新不误关、零字节拒绝、正常退出、未 idle 停止、连续失败有限停止和 MCP-only last-tool 记录。未运行完整套件，未启动真实 Houdini GUI。仍需人工验证真实崩溃、checkpoint/crash HIP 实际加载、同一 Thread/Goal 续做、Goal 修改后专注立即关闭，以及正常关闭绝不重启；第一版不支持进程仍存活的界面假死、断电或 launcher 自身死亡。

## Stop 后状态长期未确认（2026-07-21）

- 真实 GUI 中点击停止后会长期保留“停止请求未确认；正在同步 Turn 状态”。根因是该文字被永久追加为普通 System 行、2.5 秒对账要等 interrupt 返回后才启动、固定 reason 每代只能查询一次，而且 Panel 的 interrupt 15 秒上限早于 Bridge/Codex 45 秒 RPC 上限。
- Stop 现在立即冻结可见流并启动现有 2.5 秒 QTimer；interrupt 单独使用 50 秒上限。session 对账在约 30 秒/最多 12 次内使用唯一 reason 有限重试，覆盖占用、active、失败和 timeout。权威 idle/completion 才解锁；达到上限仍 active 时停止轮询、保留安全锁，并静态显示“停止已请求；Houdini 工具仍在结束”。过程状态不再永久写入聊天历史，真实终态只追加一次“Turn 已停止/已结束”。
- 定向运行 BridgeClient、Panel wiring 与 ConversationView 回归 110/110 通过，用时 0.076 秒；未运行完整套件、未启动 Houdini。真实 GUI 仍需验证长时间 HOM 返回、Bridge 重连与 completion 丢失后的最终收口。

## Stop 单一路径、本地 Houdini 状态与手动历史打开（2026-07-21）

- 本节取代上一节的多次 Stop 对账方案。真实卡住的直接原因是 Panel 最多 12 次读取同一份 Bridge 内存快照，而快照仍只能由 `turn/completed` 改变；轮询耗尽后又永久保留 stopping token 和发送锁。点击 Stop 现在立即冻结文本/工具流并显示“已停止”，同一 Turn 只发一次 interrupt，不再显示持续思考、同步或重复轮询。
- Bridge 给 interrupt 固定 1 秒宽限，并把关闭、重建、初始化和 resume 原 exact Thread 全部限制在同一个 6 秒总 deadline 内；Panel 的 `/v1/interrupt` 单独使用 7 秒 HTTP 上限。预算内成功则恢复同一 Thread；超时则快速返回结构化可恢复错误、清除 stopping token 并显示未连接，不再用 42/50 秒占住发送锁。全过程不启动新 Turn、不重放 HOM，也不终止 Houdini；迟到 ACK、delta、工具进度和旧 completion 由原 Turn token/stream 归属隔离。已进入 Houdini UI 主线程的操作无法安全强杀，Panel 只显示一次“Codex 已停止；已发出的 Houdini 操作可能仍在收尾”。
- HIA MCP V2 过去仍被 FX/B2 门槛挡住本地刷新：选择和 dirty 只在构造时读取，revision 也没有进入 Panel。现在复用唯一的 Houdini UI QTimer，在 UI 主线程直接刷新 `selectedNodes()` 与 `hipFile.hasUnsavedChanges()`；场景版本复用现有 `/v1/health` 中的真实 `scene_revision`，未新增 endpoint、线程或缓存。Goal 变为 blocked/complete 等非 active 状态时，Bridge 持久化和 Panel checkbox 都关闭专注；重新变回 active 不会自动开启。
- Panel 新建或重开后对话区保持空白：历史列表可以自动刷新，但下拉保持“未选择历史会话”；只有用户选择记录并点击“打开”才 resume、读取并渲染，同一 Thread 的历史数据没有删除或改写。
- 最终定向运行 Bridge session、Codex stdio、BridgeClient 和 Panel wiring 回归 153/153 通过，用时 0.777 秒；覆盖 1/6/7 秒常量、deadline 贯穿、恢复卡到 deadline 后解锁并转为未连接，以及既有 Stop/本地 Houdini 状态/Goal/手动历史行为。未运行完整套件，未启动真实 Houdini GUI。仍需人工验证真实 Stop 丢 ACK/长 HOM 时的 6 秒内 app-server 恢复或明确断线、选择 none/one/many 与 dirty/revision 实时刷新、blocked Goal 关闭专注，以及重开 Panel 空白且手动“打开”才载入历史。
- 启动验收补充：新建或重开 Panel 时，Bridge 当前 Thread、后台专注 Turn、Goal、团队事件、协议提示和断网重连都不再改变“Thread：未选择”或向空白聊天区写入内容；历史刷新与下拉选择只处理索引，点击“打开”后才恢复、渲染并请求该 Thread 的 Goal。Panel/Conversation/BridgeClient/Bridge session 定向回归 150/150 通过，用时 0.100 秒；真实 Houdini 中关闭再重开 Panel、后台专注任务并行和手动打开仍需人工验收。

## steer 终态竞态与 Goal 只读状态（2026-07-21）

- 真实 GUI 的 `no active turn to steer` 发生在旧 Turn 已结束但 `turn/steer` 尚未返回的窗口；旧实现把 Codex `-32600` 泄漏为 `CODEX_RPC_ERROR/502`，Panel 又丢弃已点击的文字/图片快照。Bridge 现在只把 code=`-32600` 且规范化文案精确为 `no active turn to steer` 的响应转换为 `409/NO_ACTIVE_TURN`，不透明创建 Turn；匹配的正 ACK 即视为输入已接收，completion 先到不再误报失败。Panel 以同一 generation/thread/turn 的结构化 terminal 证据收口旧 Turn，并把原 request text、图片和当时的 Houdini 选择上下文作为普通新 Turn 至多发送一次。Stop、超时、近似 RPC 文案、ID 不匹配和普通错误均不触发 fallback；迟到响应不重发，另一调用者抢先开始 Turn 时保留 composer 且清理已终止 pending context。
- Goal 的 `blocked` 经现场数据库与 rollout 证明是原生 Goal 在连续等待用户保存 HIP 后作出的权威更新，与 steer 竞态无关，因此没有自动改回 active。Goal 状态下拉已改为只读文本：未设置、正在跟进、已完成、等待你处理；blocked 原因只取权威 payload，缺失时显示“未提供原因”。blocked 时按钮显示“继续跟进”，只有用户主动保存才显式发送 `status=active`；关闭专注、Stop、steer、网络或 RPC 失败都不修改 Goal。原生 Goal chain 在 selected Thread 的 goal-set/active Goal 范围内关联其 Turn，活动摘要优先显示 in-progress、再显示 pending；全部完成或 Turn 结束后恢复为等待下一轮，不接纳 child、其他 Thread 或普通聊天 Turn。
- 定向运行 Bridge session、Bridge HTTP、BridgeClient、Panel wiring 与 ConversationView 回归 180/180 通过，用时 9.823 秒；另有精确 Bridge steer/Turn lifecycle + Panel 回归 106/106 通过，用时 0.028 秒。未运行完整套件、未启动 Houdini。仍需在真实 GUI 验证：旧 Turn 恰好结束时点击“追加指令”只生成一个新 Turn且图片/选择上下文不丢；Stop 与迟到错误竞争不复活 Turn；Goal blocked 的原因、继续跟进按钮及原生 Goal 多轮活动文案符合实际通知顺序。

## Codex 空白消息与 stale Turn 追加同步（2026-07-21）

- 真实 GUI 中，Stop 后继续 Goal 时出现空白 Codex 卡，但计划、工具与团队仍更新。只读日志确认该轮只有 reasoning、工具和 item 生命周期事件，没有 `output_text`/`item/agentMessage/delta`；旧 UI 在开始时先创建空正文卡，且原生 Goal Turn 不属于普通可见 stream，所以无文字终态会留下空卡，未来真实 Goal delta 也会被忽略。现在卡片在首段文字前显示低调占位，首 delta 在同一卡原位替换；无文字正常终态显示“本轮未返回文字回复”，首 delta 前 Stop 则移除占位卡并继续隔离迟到事件。精确匹配当前 Thread/Goal Turn 的 agent delta 与 completion 可进入并收口主对话，child 仍只进入团队栏；reasoning 和工具原始 JSON 不进入聊天。
- 另一个真实错误是 Panel 仍用旧 Turn ID 追加，而 app-server 已报告 `expected active turn id ... but found ...`。Bridge 仅把 RPC code=`-32600` 且完整匹配该结构的响应规范化为 `409/STALE_ACTIVE_TURN`，用 generation/thread/turn CAS 更新权威快照；近似文案或其他错误码不触发恢复。Panel 随后只做一次既有 `/v1/session` 同步：同一 Thread 仍有新的 active Turn 时改绑并重试 steer 一次；已 idle 时复用现有新 Turn fallback 一次；再次冲突或同步失败即停止，保留文字与图片。原 Turn 的迟到 ACK/delta/completion 由 source token 和新 generation 隔离，Goal Turn 不会被普通追加抢绑。
- 定向运行 `test_bridge_session`、`test_bridge_http`、`test_bridge_client_reply`、`test_conversation_tool_activity` 与 `test_panel_wiring`：187/187 通过，用时 10.445 秒；Markdown/IME/焦点静态回归 `test_p1_assets`：23/23 通过，用时 1.176 秒。未运行完整套件，也未启动、停止或重启 Houdini、Bridge、Codex。仍需真实 GUI 验证无文本 Goal 的占位/终态、首 delta 原位替换，以及 stale old ID→同步到新 active ID→恰好一次 steer 重试时草稿和图片不丢。
- 后续只读审查发现两项迟到竞态：Goal 正文已出现后，stale-steer session 响应仍会无条件冻结并新建占位卡；同步失败或单次 retry 再冲突时，代码又会直接丢弃 pending 快照，若用户期间改写 composer，原文字或图片可能实际丢失。修正后只在 Goal 对账成功且可见流确实需要切换时换卡；失败收口把尚未包含的原文字置于当前新草稿之前，并只补回缺失附件，Stop/cancel 明确不恢复。相关 Panel/Bridge/ConversationView 回归 189/189 通过，用时 9.754 秒；仍需真实 GUI 验证 Goal 已输出正文时的迟到对账，以及同步失败/二次冲突期间继续编辑草稿的结果。

## Stop 后 Codex 后台恢复（2026-07-21）

- 真实 GUI 中 Stop 的 6 秒同步 restart/init/resume 预算不足以恢复长 Thread；Bridge 超时后把会话永久置为 `stopRecoveryFailed`，Panel 又无条件清除连接与认证，因此模型、推理强度、速度和发送全部锁死。Goal Turn 关联未清理，右栏同时错误显示“正在推进”；超时移除的 `thread/resume` 请求若稍后返回，还会产生 `UNKNOWN_RESPONSE_ID`。
- 本节取代前述“6 秒内成功，否则永久断线”的 Stop 收口。Stop 仍只发送一次 interrupt，并在约 1 秒宽限内接受正常 completion；超过宽限后立即把旧 Turn 本地终结并隔离，HTTP 返回 `stopRecovering`。Bridge 同时最多启动一个后台 worker，在 50 秒总上限内只执行 Codex app-server restart、initialize 和原 exact Thread resume，不调用 `turn/start`，不重放 Turn、HOM 或 Houdini 操作。成功或最终失败都只通过既有 `session_state` 发布；旧进程 reader、旧 Turn 通知和已知恢复请求的迟到响应不能污染新 generation。
- Panel 点击 Stop 后立即冻结可见流、显示“已停止”并保持 composer 可编辑；恢复期间发送/新建/切会话禁用，但模型、推理强度和速度可为下一 Turn 本地调整。active Goal 只显示恢复暂停，不改 Goal 或专注模式；恢复成功后同一 Thread 自动恢复连接与发送，最终失败则保留草稿/附件并只提示一次重启 launcher。普通未知 response 仍保留协议警告。
- 定向运行 Bridge session、Codex stdio、Bridge HTTP、BridgeClient、ConversationView 与 Panel wiring 回归 210/210 通过，用时 10.849 秒；未运行完整套件，也未启动、停止或重启真实 Houdini、Bridge 或 Codex。仍需真实 GUI 验证长 Thread Stop 后恢复中状态、自动恢复模型控件与发送、Goal 暂停文案，以及已进入 Houdini UI 主线程的 HOM 最终收尾行为。

## Goal 专注模式自动续轮（2026-07-21）

- 真实 GUI 中 Goal 仍显示“正在跟进/正在推进”，但当前 Turn 已经 idle，底部按钮退回“发送”，用户必须手动发消息才能继续。根因是既有逻辑只收口 completion 和保留 active Goal 元数据，没有把“active Goal + 专注开启 + 权威 idle”连接到下一轮 `turn/start`。
- Panel 现在用单一 completion boundary 在全部安全条件满足时为同一 Thread 恰好启动一次内部续轮，继续沿用当前模型、推理强度和速度，不重放上一轮文字、工具或 Houdini 操作。内部短指令不显示为用户气泡，历史恢复也精确隐藏；Stop、断线、审批、Goal 非 active 或无有效文字/工具进展会暂停续轮，用户明确继续后才恢复。Goal 更新也不能把正在运行的普通/自动 Turn 误绑定成原生 Goal Turn。
- Stop、stale steer、历史、Goal、IME/composer、附件与 Bridge Turn 相邻回归 215/215 通过；最终完整套件 763/763 通过。仍需真实 Houdini GUI 验证：手动打开一个 active 且专注开启的 Goal Thread，确认每轮完成后仅续一次、按钮进入“追加指令”、内部续轮不产生用户气泡或 System 刷屏；Stop 后保持暂停，明确继续后再恢复。

## 消息气泡实际宽度回归（2026-07-21）

- 真实 GUI 中约 800px 的会话 viewport 仍把长 Codex 与用户消息卡压在约 280px，造成严重窄列换行。根因不是 0.82/0.68 上限计算，而是 `QHBoxLayout.addWidget(..., alignment)` 的水平 alignment 让 Expanding 卡按窄 `sizeHint` 留在已分配槽内；旧测试只检查 `maximumWidth`，因此未发现实际几何错误。
- 最小修复仅移除消息行两处 `addWidget` 的水平 alignment 参数，继续用原左右 stretch 对齐：Codex 实际约占可用宽度 80%，用户卡受既有 68% maximum 限制；未改 ratio、composer、Goal、Bridge 或其他布局。新增近真实布局回归直接验证 actual width、viewport resize、长 Markdown 高度重排和短用户消息不越界。
- ConversationView 精确回归 13/13、Panel/IME/composer/附件相邻回归 145/145 通过；本轮唯一一次完整套件 764/764 通过，用时 34.283 秒。仍需在真实 Houdini GUI 验证宽/窄 Pane 拖动时两类气泡实际比例、长 Markdown 重排高度及短消息视觉效果。

## 专注模式崩溃恢复后的 Panel 精确接管（2026-07-22）

- 真实问题是 launcher 已能在 Houdini 异常退出后恢复 HIP 并为原 Thread 发送一次恢复 Turn，但重启后的 Panel 按正常规则保持未选择、空白，因而收不到该 Turn 的 completion 边界，既有 Goal 自动续轮无法继续第二轮。普通启动空白规则本身没有错误。
- launcher 现在只给本次已验证的恢复 Houdini 子进程传入一次性 exact Thread、Goal binding 与恢复 prompt 标记；Panel 仅在 `/v1/health`、两次实时 Goal 校验和 `thread/read` 全部证明 exact Thread、focus=true、同一 active Goal 后本地绑定，不调用 resume、不猜最近历史，也不重复 launcher 的恢复 Turn。绑定后用 fresh session/事件接回既有 Goal 续轮；读取期间 Goal/focus 改变会拒绝绑定，旧 session 不回灌，内部恢复指令不显示成用户气泡。普通子进程没有标记，标记消费后普通重开仍为空白。
- Panel/launcher 精确回归 146/146 通过；Panel、launcher 与相邻 Bridge 回归 233/233 通过；最终完整套件 773/773 通过，用时 33.749 秒。PowerShell AST 与 Python 编译检查通过。尚需真实 Houdini 人工验证同一 launcher 内异常退出后：原 Thread 自动显示、launcher 恢复 Turn 完成后连续两轮各只续一次；正常关闭或普通重开 Panel 仍为空白。本轮不覆盖 launcher 自身退出、断电或进程仍存活但界面假死。

## 复杂视觉任务自动低分辨率审阅闭环（2026-07-24）

- HIA V2 的会话指令和现有 Houdini skills 现在共同定义同一闭环：Box、单参数操作、普通 HOM 报错等简单任务不截图；复杂视觉任务只在主要结构完成、任务范围内材质/灯光完成、最终交付前等产生有意义可见变化的里程碑自动预览，相邻或无变化阶段合并或跳过。动画和模拟只抽代表帧或关键帧。
- 阶段证据完全复用 `hia_capture_viewport`、`hia_validate`、`hia_scene_diff` 与只读 `houdini-artifact-review`。默认同帧 flipbook 为 `640 x 360`、`return_image=true`；reviewer 检查比例/轮廓、浮空/穿插、支撑/接触、构图、材质、曝光、透明度和参考一致性，只返回证据及最低修复建议。主任务仍是唯一 HIP writer，每轮只修最高影响区域，按任务设小范围迭代预算并在达标时立即停止。
- runtime 在进入 Scene Viewer 或创建文件前校验 flipbook `frame_range`：必须是两个有限数字、结束帧不得早于开始帧、跨度不得超过 240 帧；省略范围时使用当前同一帧。工具协议同步公开 640×360 默认值、关键帧建议、跨度上限，以及相机/锁定/帧恢复承诺。
- 截图仍只写 `HIA_CACHE_DIR/screenshots`；没有修改任何清理实现或范围，也没有触碰 `previews`、`tmp`、附件和最终输出。没有新增 MCP 工具、服务、调度器、评分系统或 Agent，也没有修改 launcher、Panel 或知识库。

### 自动验证

- `python -m unittest discover -s tests\unit -p test_hia_mcp_v2*.py -v`：共 75 项，73 通过，2 个未安装的可选 FXHoudiniMCP fixture 跳过。
- `python -m unittest tests.unit.test_bridge_session -v`：53/53 通过；覆盖 HIA V2 指令的复杂/简单触发边界、640×360 同帧预览、关键帧、只读 reviewer、最高偏差有限迭代以及 1000 字符上限。
- viewport/protocol/Bridge policy 精确定向回归：21/21 通过。新增 viewport fake-Houdini 覆盖默认同帧分辨率、成功路径的相机/锁定/帧恢复、不打开 MPlay/对话框/焦点，以及倒序、非有限和超过 240 帧跨度在捕获前拒绝；协议回归覆盖默认尺寸、范围说明、只读 annotation 和错误形状不进入 transport。
- `python -m compileall -q` 覆盖 HIA runtime、HIA MCP V2、Bridge 及修改过的测试文件，通过；`git diff --check` 通过。
- 完整 `python -m unittest discover -s tests\unit -v` 共 792 项：787 通过、2 跳过、3 失败。失败为本任务未修改且用户明确排除的 launcher/Panel 范围：`test_bridge_python_rejects_unsafe_paths_and_failed_probe` 的 UNC `Test-Path` 权限错误、`test_current_session_crash_hip_is_pid_bound_and_copied_read_only` 的恢复目标目录缺失、`test_approval_purpose_prefers_the_actual_system_target` 的既有审批目的文案断言。本轮未越界修复。

### 真实 Houdini 未验证项

- 本轮没有启动真实 Houdini GUI。仍需在 Houdini 21.x 验证真实 `SceneViewer.flipbook` 的 640×360 输出、同帧/关键帧路径、PNG 实际数量、成功与失败后的相机/自由视图/锁定/当前帧恢复，以及 MPlay、对话框和焦点保持不变。
- 仍需用一个真实复杂视觉 Turn 验证 Codex 在有意义里程碑自动触发预览、`houdini-artifact-review` 保持只读、主任务只修最高影响区域并在达标后停止；同时用简单 Box 或单参数操作确认零截图。未做提交或推送。

## SQLite FTS5 本地知识库核心（2026-07-24）

- `hia_local_help_search` 保持原工具和旧 `query/sources/offset/limit` 形状，新增可选 `sources=["user"]` 与 `refresh`。正文增量写入项目内 `.runtime/knowledge/knowledge.sqlite3`；查询只读 FTS5，不再每次读取源文件。索引覆盖完整 live `hou` node catalog、`$HH/help` 支持文本、发布 Skill/refs、五份当前项目 docs，以及用户授权目录中的 TXT/Markdown/HTML/SRT/VTT；PDF 只尝试已有 `pypdf`，缺失或失败会明确 warning。
- 数据库按文件 size/mtime 避免无效读取，对变化正文计算 SHA-256 并切成约 1200 字符 chunk。WAL 允许两个 worker 并发读，`BEGIN IMMEDIATE` 保证单写事务；当前 Houdini 版本优先但保留其他版本资料。结果包含 path、URL、author、accessed_at、Houdini version、license、SHA-256、verification、evidence。只有当前真实 `hou` catalog 标为 `verified`；帮助、项目与用户资料统一为 `unverified`，sidecar 不能提升验证等级。
- 自动刷新窗口为 10 分钟：第 599 秒不刷新，第 600 秒到期；窗口内 `refresh=true` 仍立即增量刷新。实现没有 watcher、后台线程、服务、调度器、向量、embedding 或第二模型/Agent。
- 单测覆盖旧参数与空 `sources` 兼容、首次索引后查询不再读取文件、允许来源与历史文档排除、用户 sidecar、HTML/VTT、增量 SHA-256、当前版本优先、PDF 明确降级、WAL、两个独立索引实例的并发读/串行写，以及精确刷新时间边界。集成轮的最终测试结果见后续章节。
- 尚未用真实 Houdini 的 `$HH/help` 规模、完整 node catalog 或双 MCP 进程现场验收；需确认首次索引时 UI 主线程 catalog 快照耗时、随后查询不扫描正文、两个 worker 同时查询，以及升级 Houdini 后当前版本排序。

## 视觉闭环、研究契约与本地知识库集成轮（2026-07-24）

- 本轮以当前 worktree 为基线做字段和段落级合并，没有整文件覆盖。`executor.py` 与 `tools.py` 同时保留 SQLite FTS5 `hia_local_help_search`、10 分钟自动刷新和 `refresh=true`，以及 flipbook 默认 `640 x 360`、最大 240 帧跨度、相机/锁定/帧恢复和协议说明。未增加 MCP 工具、服务、watcher、调度器、评分系统、向量、embedding 或第二模型/Agent。
- `houdini-visual-research` 保留复杂视觉任务的有意义里程碑、动画/模拟关键帧、最大可见偏差和有限迭代，并合入自动多轮多来源研究、完整来源 ledger、原创 memo 与 `draft`→`verified` 规则。`houdini-artifact-review` 继续只读审阅构图、曝光、透明度、参考一致性和最高影响区域；只有真实 Houdini build 与 live node/cook/frame/viewport/render 证据可以支持 `verified`，reviewer 不写研究草稿或知识索引。`visual-validation` 前半执行低分辨率里程碑闭环，后半只做 bounded in-scope recovery，并保证每个 Thread/Turn 最多一个最终失败报告。
- 研究 memo 仍位于 `.runtime/cache/research/<thread-or-turn-id>/`；自动截图仍位于 `.runtime/cache/screenshots`。未扩张截图清理范围，未触碰 `previews`、`tmp`、附件或最终输出。契约测试同时断言 Skill 不包含资产专用配方。

### 自动验证

- 集成核心定向测试：`python -B -m unittest tests.unit.test_hia_mcp_v2_local_help tests.unit.test_hia_mcp_v2_protocol tests.unit.test_hia_mcp_v2_viewport_state tests.unit.test_houdini_skill_contracts tests.unit.test_bridge_session.BridgeSessionNativeToolPolicyTests -v`，33/33 通过。
- HIA MCP V2 全组：`python -B -m unittest discover -s tests\unit -p "test_hia_mcp_v2*.py" -v`，共 79 项，77 通过、2 个可选 FXHoudiniMCP fixture 未安装而跳过。
- `python -B -m compileall -q` 覆盖 HIA runtime、HIA MCP V2、Bridge 与相关测试文件，通过；`git diff --check` 通过。Ruff 对本轮新增/合并路径仅暴露已有 `services/bridge/hia_bridge/session.py:364` 的 `system_drive` 未定义，该行不在本轮差异内，未越界修改。
- 默认 Python 缺少 PyYAML，改用机器上已有且带 PyYAML 的隔离解释器执行 Skill Creator `quick_validate.py`，没有安装或修改依赖；`houdini-visual-research`、`houdini-artifact-review`、`houdini-material-lookdev` 与 `houdini-procedural-modeling` 四个 Skill 均返回 `Skill is valid!`。仓库内 `test_houdini_skill_contracts.py` 的 6 项触发边界、研究 ledger、验证状态、诊断报告和资产无关契约测试全部通过。
- 本轮唯一一次完整 `python -B -m unittest discover -s tests\unit -v` 共 802 项：797 通过、2 跳过、3 失败。失败均为本轮明确排除且没有修改的 launcher/Panel 既有问题：
  - `test_launcher_preflight.LauncherPreflightTests.test_bridge_python_rejects_unsafe_paths_and_failed_probe`：UNC `Test-Path` 权限错误；
  - `test_launcher_preflight.LauncherPreflightTests.test_current_session_crash_hip_is_pid_bound_and_copied_read_only`：恢复副本目标目录缺失；
  - `test_panel_wiring.PanelWiringTests.test_approval_purpose_prefers_the_actual_system_target`：审批目的仍优先显示项目内源路径。

### 真实 Houdini 与 GUI 未验证项

- 未启动真实 Houdini GUI。仍需验证真实 `$HH/help` 与完整 node catalog 首次索引耗时、双 MCP worker 的现场并发查询、Houdini 升级后的版本排序，以及查询阶段不重扫正文。
- 仍需验证真实 `SceneViewer.flipbook` 的 640×360 同帧/关键帧输出、PNG 数量、240 帧拒绝边界，以及成功和失败后的相机、自由视图、相机锁定、当前帧、MPlay、对话框与焦点状态。
- 仍需用一个真实复杂视觉 Turn 验证有意义里程碑自动预览、只读 reviewer、只修最高影响区域和达标即停；再用简单确定性任务确认零截图。真实 build/live evidence 驱动 memo 从 `draft` 晋升 `verified` 的路径也尚未现场验收。本轮未提交、推送或清理。

## 集成后 launcher/审批根因回归（2026-07-24）

- 只从回归 worktree 逐段合入两个指定业务文件，没有复制其余文件。Bridge Python 现在先完成普通本地绝对路径、UNC、ADS、WindowsApps 与 AppData 例外策略判断，只有安全路径才执行 `Test-Path`；因此恶意或不可访问 UNC 不再触发权限异常。python.org 的每用户默认安装路径仍按既有规则允许。
- launcher recovery 副本名中的随机 GUID 从 32 个十六进制字符缩为 16 个，保留 attempt 与 HIP 后缀，避免深层便携项目在传统 Windows 路径上超过限制；源文件仍只读复制，既有目录与 reparse 安全边界不变。
- 审批卡优先从 `-Destination`、`-Dest`、`-OutFile`、`-Output` 或 `-o` 的明确写入 flag 提取目的目标，再回退到既有系统盘路径选择。修改只影响人类可读目的文案，不改变审批事件识别、触发、允许、拒绝、关闭或可选持久授权边界。

### 自动验证

- 前一轮完整测试的 3 个失败用例精确重跑：3/3 通过。
- `test_launcher_preflight` 全模块加 3 个 approval 相邻用例：43/43 通过；覆盖不安全路径先拒绝、python.org 每用户安装、失败 probe、长路径 recovery、审批脱敏、真实写入目标优先、deny 值与持久授权提示不变。
- 补丁后的唯一一次完整 `python -B -m unittest discover -s tests\unit -v`：共 802 项，800 通过、2 个可选 FXHoudiniMCP fixture 跳过，0 失败。没有出现 `.runtime/tmp` 首轮顺序问题，因此没有创建补救目录，也没有重跑完整套件。
- 未启动真实 launcher、Panel 或 Houdini GUI；仍需人工验证选择不可访问 UNC 时立即显示安全路径错误、深目录崩溃恢复复制，以及包含源路径和 `-Destination`/`-OutFile` 目标的真实审批卡文案。本轮未提交、推送或清理。

## HIA MCP V2 性能、吞吐与有界收口总集成（2026-07-24）

### 根因与最小实现

- 当前基线在本轮前已经具有 2 worker、32 pending 的 hotfix，因此 16 个分离查询已不会产生 `QUEUE_FULL`；剩余浪费来自调用形状：`hia_search_node_types`、`hia_node_help` 与 `hia_local_help_search` 只有单项输入时，16 个关键词会形成 16 个 MCP frame 和 16 次 transport/HTTP POST，并重复读取 catalog 或索引源。
- 三类读取工具现在都保留旧单项形状，并增加最多 16 项的批量形状：node-type 批量只构建一次 installed catalog；node-help 把单项失败封装在对应 result 中；local-help 每批只做一次 Houdini UI snapshot 和一次现有 `LocalKnowledgeIndex` 增量 refresh，再为各 query 执行 FTS 查询并返回带 `matched_queries` 的合并结果。`sources=["user"]`、10 分钟刷新、`refresh=true`、provenance、verification、index 元数据和 phase timings 全部保留，没有回退为逐文件扫描。
- stdio 分为两个只读 worker/32 pending 的 read lane，以及一个 worker/8 pending 的 write lane。`hia_execute_hom` 只进入串行 write lane；read burst 不再被等待中的写调用占满容量。stdin EOF 先等待 0.5 秒正常 drain，未结束则 latch 全部 queued/active request cancellation、关闭 transport，再给 0.25 秒有界收口；不会强杀已经进入 Houdini UI 主线程的 HOM。
- Bridge 与 `houdini-procedural-modeling`、`houdini-visual-research` 仅增加“多个关键词或帮助目标一次批量查询、复用结果、不并发扇出”的通用规则；HIA developerInstructions 为 990/1000 字符。原多轮研究、完整 ledger、原创 memo `draft`→`verified`、640×360 里程碑预览、关键帧、只读 artifact review、最大偏差和有限迭代规则均保留。
- 没有新增 TTL cache、request coalescing、Redis、服务、watcher、调度器、向量、embedding 或第二 Agent。成功 POST access log 继续默认静默，401/403/507 等非 2xx 日志继续保留。

### 专项性能数据

- 性能专项 worktree 的同一 `RecordingTransport(delay=0.02)`、7 次重复 JSONL 微基准数据为：修改前 16 个分离查询产生 16 次 transport/HTTP 调用，中位 0.250 秒；批量路径 16 个关键词产生 1 次调用，中位 0.031 秒。调用数下降 93.75%，中位耗时下降 87.6%，两条路径的 `QUEUE_FULL` 均为 0。
- 本集成轮以协议和压力回归保持上述调用形状，没有把微基准冒充真实 Houdini cook 时间。协议测试确认三类 batch 各自只提交一次 transport；16-read burst、独立 write lane 和连续压力测试均为 `QUEUE_FULL=0`。

### 自动验证

- 五个直接相关模块 `stdio_queue/runtime/protocol/local_help/bridge_session`：90/90 通过。
- HIA MCP V2 全组：共 86 项，84 通过、2 个可选 FXHoudiniMCP fixture 跳过；与完整 Bridge session 合并计算为 139 个唯一专项用例，137 通过、2 跳过，超过原专项 130+2 skip 覆盖。
- `test_hia_mcp_v2_stdio_queue` 连续运行 5 轮：每轮 8/8，共 40/40 通过；没有时序性 `QUEUE_FULL`、worker 泄漏或 shutdown 挂起。
- 本轮唯一一次完整 `python -B -m unittest discover -s tests\unit -v`：共 809 项，807 通过、2 个可选 fixture 跳过、0 失败，用时 47.416 秒。此前修复的 launcher/Panel 三项保持通过；没有出现 `.runtime/tmp` 首轮顺序问题，因此没有创建补救目录或重跑完整套件。
- `houdini-procedural-modeling` 与 `houdini-visual-research` 的 Skill Creator `quick_validate.py` 均返回 `Skill is valid!`。性能生产/测试文件 Ruff、Python compileall 和 `git diff --check` 均通过。
- launcher Core、approval card 与 `knowledge_index.py` 的 SHA-256 在性能合并前后完全一致；视觉 capture 的 640×360、240 帧、相机/锁定/帧恢复回归也保持通过。

### 真实 Houdini 未验证项

- 未启动真实 Houdini GUI。仍需现场验证一个长 `hia_execute_hom` 与 read burst 并存时的 UI 主线程行为、Stop/EOF 后已进入主线程的 HOM 自行完成、多个独立 MCP stdio 进程同时写入时的实际串行化，以及批量 node search/help/local-help 与 installed Houdini 的结果一致性。
- 本轮没有提交、推送或清理；保持当前 worktree 等待 launcher 新 UI 集成。

## 冻结 launcher UI 最终集成（2026-07-24）

### 合并范围与保护项

- 冻结源为 `E:\houdini-intelligence-agent`。合并前确认 4f08 的 `HiaLauncher.xaml` 相对自身基线无本地改动；源 XAML 已通过 XML 解析，Wpf 的 33 个 `Get-RequiredControl` 名称全部存在且无重复，12 个 click/selection/size 事件绑定全部存在。按授权只对这一精确大文件做一次整体机械同步；同步后源与目标 SHA-256 均为 `72D1B3D6EA778453294369F301449B98A4B88D0CCB991A0A570AAFEDC401EECA`。
- `HiaLauncher.Wpf.ps1` 只逐段合入完整产品标题，以及紧凑/宽屏时 `RightVisualRail` 的宽度和边距；规范化换行后与冻结源内容一致。没有启动、显示或继续美化 UI。
- `test_launcher_preflight.py` 只合入五个 UI 区域：新品牌和必需控件、五种窗口尺寸下启动按钮可达、固定底栏和暗色滚动条、禁用主按钮高对比样式，以及非季节性内置节点图形；既有 recovery、截图清理、portable launcher 与 Core 根因测试保持不变。
- 4f08 的 `HiaLauncher.Core.psm1` 始终为权威版本，合并前后 SHA-256 都是 `23EA4E99E5D0405571055238A2EB542E54814BC9DFC305956856422748824371`；没有从 E 覆盖。完整回归首次暴露 `test_release_packaging.py` 仍要求已删除的旧文案 `CREATIVE WORKSPACE`，只把这一条陈旧契约更新为冻结 UI 的 `BIG-CHICKEN`，没有修改打包逻辑或视觉。

### 自动验证

- UI 精确契约 5/5 通过，用时 0.830 秒。`test_wpf_xaml_loads_and_exposes_required_controls` 通过 `powershell -Sta` 实际执行 `XamlReader.Load`，对 1180×820、944×656、820×600、787×547、640×480 逐一 Measure/Arrange/UpdateLayout，并从 PowerShell AST 提取和执行 `Update-ResponsiveLayout`；没有 `Show` 或 `ShowDialog`。
- `python -B -m unittest tests.unit.test_launcher_preflight -v`：41/41 通过，用时 13.150 秒；Core 的不安全路径先拒绝、16 字符 recovery GUID、截图清理、恢复和便携性测试均保持通过。
- 首次完整回归共 810 项，因上述唯一旧品牌断言得到 1 failure、2 skip；最小更新后 `test_release_packaging` 7/7 通过。最终原样重跑 `python -B -m unittest discover -s tests\unit -v`：810 项中 808 通过、2 个可选 FXHoudiniMCP fixture 跳过、0 failure，用时 35.906 秒。没有 `.runtime/tmp` 首轮顺序问题，也没有创建补救目录。
- 最终 XML 解析、PowerShell AST、33 个必需控件、12 个事件绑定、冻结源内容对照、Core 哈希保护与 `git diff --check` 均通过。

### GUI 未验证项

- 本轮没有显示真实 launcher 窗口，也没有启动 Panel 或 Houdini GUI。仍需人工确认系统缩放、实际工作区尺寸、键盘导航、滚动手感、禁用按钮视觉和真实点击流程；自动化已经覆盖 XAML 真加载、五种布局尺寸、控件可达性、AST、对比度和事件契约。
- 未提交、推送或清理；当前 4f08 保持等待同步回 E。

## 本地 hybrid 检索、显式项目记忆与双模型 contract（2026-07-24）

### 最小设计与兼容边界

- HIA MCP V2 registry 当前为 17 个工具。`hia_local_help_search` 保留既有单项/批量、`sources`、分页和 `refresh` 兼容形状，新增 `lexical|vector|hybrid`，默认 hybrid；SQLite FTS5 始终是硬基线。唯一新增持久记忆入口为 `hia_project_memory`，actions 为 `record/search/list/delete/supersede`，types 为 `decision/preference/asset/lesson/workflow`。只有显式 record/supersede/delete 改写记忆，不保存聊天、不从 compaction 或诊断自动总结。
- SQLite `knowledge.sqlite3` 同时保存正文、FTS5、显式 memory 和向量行；向量行保留 `model_id` 与 `dim`。刷新按 chunk SHA-256 增量处理，来源删除同步删除正文/FTS/向量；切换 profile/revision/dimension 只重建向量层，不重建正文或 FTS5。结果保留 provenance/verification，并公开 requested/active profile、status、degraded、fallback reason 和 repair。
- 普通 hybrid 每次最多渐进补齐 256 chunks，优先本次 lexical candidates，再处理 backlog；在 `retrieval.vector.index` 返回 `complete/vector_chunks/total_chunks/pending_chunks/chunks_indexed_this_call`，不宣称首次查询完成全量向量化。向量排名使用 SQLite 流式游标与 bounded heap，不把全库向量物化到内存。
- encoder 是独立、持久、串行 JSONL stdio worker，不会进入 Houdini Python/UI 主线程。该轮当时使用 `.runtime/toolchains/hia-embedding/venv/Scripts/python.exe`；当前权威环境是项目根 `<project-root>/.venv`，旧路径仅作为 legacy migration source。协议仍为 `hia-embedding-stdio/1`。MRL 通过构造 `SentenceTransformer` 时的 `truncate_dim=profile.dim` 实现，并在截断维度 normalize，不在 Python 返回后 slice。Codex 仍是唯一推理、规划、记忆正文和 HOM 生成主体；Qwen 只编码文本。搜索/import 不下载，不增加量化、reranker、第三模型、Agent、watcher、网络服务或 scheduler。

### 双 profile 与 launcher contract

- `src/hia_core/embedding_contract.py` 是轻量、无 I/O、可导入的稳定权威。默认 `qwen3-embedding-0.6b` 对应官方 `Qwen/Qwen3-Embedding-0.6B`，约 1.21 GB，默认/最大 1024 维；高质量 `qwen3-embedding-8b` 对应 `Qwen/Qwen3-Embedding-8B`，约 15.2 GB BF16 分片，默认 1024、MRL 最大 4096。两者均 Apache-2.0、32K、100+ 语言，并支持 MRL 与 query instruction。
- 同一时刻只加载一个 profile。8B 在 16 GB 显存上可能因运行时开销无法稳定全 GPU 加载；缺失、内存/显存不足或损坏时，只降级到已安装 0.6B，再降级到 FTS5，并返回原因。0.6B 失败直接降级 FTS5。没有维度 launcher UI；高级环境可显式选择 8B 的 4096 维。
- contract 固定 settings `embedding_profile/embedding_dimension/embedding_device`，完整 `.runtime` venv/model/cache/DB 路径，worker distribution/module/entry point，`HIA_EMBEDDING_*` 与 profile-specific model/revision 环境变量，以及 `installed/ready/degraded` 等 health 字段和 `install/repair/repair_toolchain` 动作。当前共享工作树的 launcher 已按该 contract 接入 profile 选择、项目内安装/修复、preflight 与子进程环境；不声称任一模型或 venv 已实际安装。
- 发行边界保持严格：Release 不包含模型权重、encoder venv、缓存、SQLite DB、索引正文或向量。官方依据为 [0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[0.6B files](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/main)、[8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B) 与 [8B files](https://huggingface.co/Qwen/Qwen3-Embedding-8B/tree/main)。

### 当前定向验证

- embedding contract、worker 与 stdio client：22/22 通过。
- HIA MCP V2 protocol 与第三方隔离：19/19 通过。
- local help、渐进 hybrid/vector 排名与 project memory：18/18 通过。
- public release packaging 与 hygiene：12/12 通过。
- 合并定向验证共 71/71 通过，用时 3.557 秒；删减后的受影响回归 51/51 通过，用时 3.312 秒。最终完整 `tests/unit` 仅运行一次，858/858 通过，用时 40.639 秒。本轮未启动 launcher UI、Panel 或真实 Houdini GUI，也未下载/安装模型、venv 或依赖。

### 真实 0.6B 安装阻断修复（2026-07-24，已解决）

- 真实下载完成 12/12 后，installer 返回 `downloaded embedding model payload is incomplete`。根因是 Hugging Face snapshot 的 `config.json` 与 `*.safetensors` 通常是指向同一项目缓存 `blobs` 的 symlink，而下载源错误复用了 canonical/staging 的“必须为普通文件”校验。
- 修复仅增加下载源 snapshot 校验：链接必须可严格解析为 Hugging Face cache 内的普通文件，并仍要求 `config.json` 与至少一个顶层 `*.safetensors`；canonical/staging 的普通文件与原子发布校验没有放宽。现有 `.runtime/cache/embedding/huggingface` 保持完整，可供下次安装直接复用，没有重下模型或粘贴下载日志。
- 真实缓存 symlink 结构只读探针通过；installer 测试 5 项中 3 项通过，2 项因当前 Windows 会话缺少文件 symlink 创建权限（WinError 1314）明确跳过，测试代码会在具备该权限的平台验证 cache 内链接通过和逃逸链接拒绝；launcher 定向测试 47/47 通过。

### 最终真实 0.6B 验收（2026-07-24，通过）

- 修复后真实安装复用 `E:\houdini-intelligence-agent\.runtime\cache\embedding\huggingface`，成功原子发布 canonical 目录与 manifest；模型共 13 个普通文件、1,207,489,174 bytes。
- 独立 worker 在 CPU/offline 下真实加载 `Qwen/Qwen3-Embedding-0.6B@main`；中文 document/query 均返回 1024 维归一化向量。
- 临时 SQLite 知识库真实完成 project memory record → hybrid search（vector ready，1/1 chunk）→ delete；删除后不可检索，临时目录已清理。
- 启动器真实 `-CheckOnly -Json`：选择 0.6B 时 overall 与 embedding 均为 green；选择未安装 8B 时为 yellow，明确降级到已安装 0.6B，8B 目录不存在且不阻断启动。
- 定向 installer + launcher 52/52、最终完整 unittest 863/863，`git diff --check` exit 0；暂存区为空，`.runtime` 无跟踪文件。

## Hybrid 查询延迟、partial 偏置与一次性索引 CLI（2026-07-24）

- 真实问题是普通 hybrid 首次查询在 lexical candidates 之后还同步处理最多 256 个 backlog chunk，约耗时 46 秒；同时仅有 256/8722 向量时，全局扫描“当前已编码子集”会把摄入顺序偏置的 CHOP Warp 当成 `velocity advected ripple field` 的全库 semantic 结果。
- 普通查询现只为本轮 lexical 命中的精确 chunk 补向量，总上限 32，不处理 backlog；批量 query 以 round-robin 选取待编码候选，并在排名时保留每个 query 独立的 candidate set。partial 索引只在相应 lexical candidate 文档内重排；无 lexical candidate 返回 lexical/空结果与明确 `partial_reason`；只有 `complete=true` 才恢复全局流式向量排名。
- `HybridKnowledgeStore.status()` 与 `build_batch()` 提供有界、可恢复的回填核心。launcher 可用 Bridge Python 运行 `python -B -m hia_mcp_runtime.knowledge_index_cli --project-root <root> status` 或 `... build --batch-size 32`；JSONL 协议 `hia-knowledge-index-jsonl/1` 输出 `start/progress/completed/error`，每批提交后可在 Ctrl+C/进程结束后从缺失或 hash 变化的 chunk 继续。未新增 MCP 工具、服务、scheduler、外部向量库或模型。
- 最终定向验证：hybrid/FTS5/memory 13/13，CLI 5/5，embedding contract 5/5，17-tool/第三方隔离 2/2，release allowlist/PowerShell 2/2，共 27/27 通过；核心优先落盘后曾先行完成 12/12。覆盖查询最多 32、无 backlog、partial 候选隔离、无候选不做偏置全局召回、完整索引恢复全局 semantic、增量 hash、模型/维度切换仅重建向量层、真实 SQLite 批次提交后中断续建、CLI status/build/resume/Ctrl+C/结构化错误、fresh subprocess 的 `-m` 启动、FTS5 降级与 memory CRUD。按任务要求未运行完整套件，未启动 Houdini GUI，也未下载或加载模型。
- 当前默认 Python 没有安装 `pytest`；未为此下载依赖，改用仓库 README 规定的标准库 `unittest` 定向入口完成上述验证。

## Launcher 本地知识索引进度与手动续建（2026-07-24）

### 最小实现与安全边界

- WPF 启动器在既有环境区增加紧凑的“本地知识索引”状态行，显示当前模型、已索引/总数、百分比、剩余量和状态；只有用户点击才运行 `build --batch-size 32`，运行中可取消，完成后按钮变为不可用的“索引已完成”。未安装模型时继续明确使用 FTS5 lexical，不阻断 Houdini。
- status/build 均由所选 Bridge Python 以 contract 提供的模块、参数与项目内 `PYTHONPATH` 启动；stdout 按 `hia-knowledge-index-jsonl/1` 异步读取并逐批刷新。取消后不删除数据库或已提交批次，只重新读取 status，因此下次可继续。
- 初版取消仅终止 CLI parent，真实审查指出 embedding worker 可能成为孤儿进程。现已统一使用 `%SystemRoot%\System32\taskkill.exe /PID <精确数值 PID> /T /F` 终止本次进程树；JSONL 异常、timer 异常、启动后初始化异常、用户取消、完成清理和窗口关闭复用同一 helper。没有 `/IM`、模糊进程名、Job Object、服务或通用进程清理器；taskkill 以 5 秒有界等待和退出码确认结果。

### 实际问题与验证

- 一次当前 PowerShell 会话中的直接 `Import-Module` 探针受执行策略阻止；产品未改用全局策略，后续按现有测试入口的 `-ExecutionPolicy Bypass` 子进程完成验证，已解决。
- 新 helper 名称是旧 Stop 函数名的前缀，首个静态回归切片误命中 helper 而得到空片段；测试改为匹配含 `{` 的完整函数签名后通过，产品代码无需回退，已解决。
- 最终 launcher + knowledge-index CLI 定向测试 55/55 通过；三个 PowerShell 文件 AST 与 XAML XML 解析通过。launcher 定向集还通过 `XamlReader.Load` 和既有多尺寸布局检查。本轮未运行完整测试，未显示真实 WPF 窗口，也未启动 Houdini。
- 仍需人工验证真实模型已加载时的 build/progress/取消/继续、窗口关闭后 CLI 与 embedding worker 均退出，以及 100%/125%/150%/200% DPI 下文字和按钮状态。终止确认通常很快，但异常慢的进程树可能使取消或关闭最多等待 5 秒；这是有界终止的首版取舍。

### 主任务真实验证（2026-07-24）

- 使用 `Qwen/Qwen3-Embedding-0.6B`、1024 维进行真实检索。部分索引状态下首次查询用时 9.5 秒；无 lexical candidate 时明确返回 fallback，且不再错误返回 CHOP Warp。有 lexical candidate 的查询用时 6.477 秒，只补齐 1 个 chunk。
- CLI 以 `batch=64` 从 1027/8724 继续构建至 8724/8724，共 121 批，约 20 分 03 秒。完整索引后，同一查询的前三项结果为 AdvectByFilaments、pyro_buildadvectionmap、shallowfields；冷查询用时 5.078 秒，热查询用时 1.268 秒。
- 最终完整测试 875/875 通过；HIA MCP 工具集合仍为 17 个，`.runtime` 保持 Git ignored。尚未执行真实 WPF 点击验收。

### Windows PowerShell 5.1 启动修复（2026-07-24）

- 真实 WPF 首次状态读取显示 `0/0` 和“无法启动知识索引命令”。索引数据库、模型和命令计划均完整；根因是 Windows PowerShell 5.1 会把从 `if` 表达式返回的 `ProcessStartInfo.EnvironmentVariables` 展开成固定长度 `Object[]`，随后 `.Remove()` 抛出 `Collection was of a fixed size`，CLI 尚未启动。
- 修复仅把两个环境集合分支改为直接赋值，未改变 CLI、数据库、模型、索引协议或构建行为，并增加防止恢复 `$processEnvironment = if` 写法的回归断言。
- 管理员环境下以与 WPF 相同的 `ProcessStartInfo` 路径执行真实 `status` 成功，返回 `8724/8724`、`complete=true`、`pending=0`；launcher 定向测试 50/50 通过。真实窗口重新打开后的显示仍需人工确认。

### 真实 WPF 二次复验诊断（2026-07-24）

- 用户在重新打开启动器后仍看到旧的通用“无法启动知识索引命令”文案。相同 Bridge Python、模型 profile、环境变量和 `ProcessStartInfo` 在 Windows PowerShell 5.1 下再次独立执行成功；真实 WPF 控件刷新、`ReadLineAsync`、`ReadToEndAsync` 和 `DispatcherTimer.Start()` 也均未复现异常。
- 原 catch 会吞掉具体异常，无法区分临时状态与第二个兼容问题。现保留简短降级说明，同时在同一状态行显示经换行压平并限制为 300 字符的真实异常消息；没有新增服务、数据库或后台日志系统。
- 新增的异常可见性回归通过，PowerShell AST 与 `git diff --check` 通过。需要完全关闭旧启动器并重新打开；若仍失败，界面将直接给出可用于精确修复的原因，而不再只显示通用红字。
- 真实异常显示后确认第二个根因：Windows PowerShell 5.1 不接受把 `if` 语句直接写在命令参数的括号表达式中，`Set-HiaKnowledgeIndexDisplay -Message (if (...))` 会尝试把 `if` 解析为待执行命令。现改为先把分支结果赋给 `$startMessage`，再以普通字符串参数传入；同时增加回归断言，禁止该写法重新出现。

## 随 Release 分发的知识卡与本机 Houdini 全文索引（2026-07-24）

- 公开仓库与 Release 新增 `knowledge/sidefx-official`：10 张项目原创工作流卡、来源 URL、适用版本和许可证元数据。卡片正文采用 Apache-2.0，不复制 SideFX 帮助正文。
- 运行时从用户自己的 Houdini 安装目录直接读取 15 个选定帮助 ZIP，不解包、不联网，并排除 `examples`、`files`、`licenses` 和 `videos` 噪声目录。本机 Houdini 21.0.440 实测索引 7,479 篇 ZIP 文档、25,716 个 chunks；清理后的散装帮助为 190 篇、817 个 chunks。
- 实际数据库共有 12,830 篇文档、31,794 个 chunks；随包知识卡为 10 篇、14 个 chunks。只读 FTS5 实测 `setWorldTransform`、MaterialX、Vellum substeps 和 Karma XPU 均命中对应 HOM、Solaris、Vellum 或节点帮助。
- `knowledge.sqlite3`、SideFX 帮助 ZIP/正文、向量和模型继续只存在于 `.runtime`，不会进入 Git 或 Release。其他用户下载后先获得原创知识卡，再从其本机 Houdini 版本建立全文索引。
- 新增 ZIP、manifest、CLI source refresh 与 Release allowlist 回归；完整测试 `879/879` 通过，`git diff --check` 通过。未启动 Houdini GUI，未删除或清理任何运行时文件。

## 项目本地 CUDA embedding 修复（2026-07-24，待 GUI 验收）

- 真实诊断确认 NVIDIA RTX 5080 与驱动可见，但 embedding venv 为
  `torch 2.13.0+cpu`、`torch.version.cuda=null`、
  `torch.cuda.is_available()=false`，因此 `auto` 只能使用 CPU。
- 启动器在模型选择旁新增“自动（优先 NVIDIA GPU）/NVIDIA GPU（CUDA）/CPU”
  紧凑选择并持久化到 `embedding_device`。`Install-HiaEmbedding.ps1` 支持同样的
  `auto/cuda/cpu`。安装仍必须由用户主动点击
  或显式运行脚本；`auto` 复用已有 CUDA-capable torch，否则仅在检测到 NVIDIA
  GPU 时通过项目本地 Astral uv 的 `--torch-backend=cu128` 准备 CUDA torch。
  通用依赖仍走 PyPI；项目外 Python 与 PATH 保持原样。
- 安装后必须重新探针 `torch.cuda.is_available()` 与设备名；请求 CUDA 但验证
  失败时安装返回错误，不把 CPU 回退伪装成 GPU 成功。预检显示真实 torch
  版本、CUDA 可用性与设备名；CPU 可用时明确提示可通过现有安装/修复入口升级。
- `launch-houdini.ps1` 接受 `-EmbeddingDevice auto|cuda|cpu` 并只把选择传给
  项目 embedding worker。默认仍为 `auto`，没有新增服务、模型或全局设置。
- 定向测试：installer transaction、contract/settings、preflight/fallback、
  lifecycle/source 与真实 WPF XAML load 共 `12/12` 通过；5 个 PowerShell
  文件 AST 与 XAML XML 解析无错误。
  本轮按要求未下载 PyTorch、未加载模型、未运行真实索引、未启动 Houdini GUI。

## Codex Thread 永久删除（2026-07-24，待 GUI 验收）

- 固定的 Codex app-server 0.144.3 协议明确提供稳定的 `thread/delete` 请求和
  `thread/deleted` 通知，因此本功能使用真实删除而不是本地隐藏或归档。
- 历史列表只删除用户当前选择的一个空闲 Thread。首次点击把同一按钮改为
  “再次点击删除”，5 秒内第二次点击才执行；未使用模态对话框。活动 Turn 会被
  拒绝并提示先停止、等待结束。
- 删除当前打开的 Thread 后，Panel 清除当前会话、Goal、团队、对话流、草稿及
  附件控件中的路径引用并回到空白状态；不会删除附件文件，也不会批量删除、
  猜测路径或操作系统盘文件。
- 定向回归覆盖协议、Bridge session、HTTP、Panel client、Panel wiring 和
  fake app-server，共 `226/226` 通过。`git diff --check` 通过。
- 尚未在真实 Houdini GUI 中人工验证同按钮二次确认、焦点行为与历史下拉即时
  刷新；未暂存、未提交、未推送。

## CUDA embedding 安装改用项目本地 uv（2026-07-24，代码已解决，待真实重试）

- 真实失败发生在项目 embedding venv 的 `pip 23.0.1`：它读取 PyTorch `cu128`
  索引时把 `typing_extensions`/`typing-extensions` 与 Jinja2 名称大小写误判为
  元数据不一致，最后返回 `ResolutionImpossible`。RTX 5080 与驱动并非根因。
- 安装按钮现使用固定的官方 Astral uv 0.11.29。缺少时只在用户点击后从
  `https://astral.sh/uv/0.11.29/install.ps1` 引导项目本地安装；
  `UV_UNMANAGED_INSTALL` 指向
  `.runtime/toolchains/hia-embedding/uv/0.11.29`，`UV_CACHE_DIR` 指向
  `.runtime/cache/embedding/uv`，不会修改全局 PATH。CUDA torch 使用
  `uv pip install --python <workerPython> --torch-backend=cu128`，worker 也由
  同一个 uv 安装；通用依赖继续使用 PyPI，没有保留第二套旧 pip 安装链。
- GUI 失败时直接显示脱敏后的真实原因，并把完整项目本地日志路径显示为
  `.runtime/launcher/embedding-install-*.log`，可复用现有“复制报告路径”按钮。
  下载/安装提示收敛为一条“母鸡啄米中…”，其余文案保持简短。
- launcher 与 installer 定向回归共 59 项：57 通过，2 项因当前 Windows 会话
  无 symlink 创建权限而按既有条件跳过；3 个 PowerShell 文件 AST 与真实 WPF
  XAML 加载通过，`git diff --check` exit 0。未实际下载 uv/CUDA torch，也未
  启动 Houdini；仍需用真实安装按钮复验 uv bootstrap、RTX 5080 CUDA torch
  和失败日志显示。

## CUDA embedding 安装安全收口（2026-07-24，已解决）

- `.runtime\launcher` 的 project root、`.runtime`、`launcher` 现逐级拒绝
  reparse point；既有 settings、安装日志与锁文件必须是普通文件。junction
  诱饵测试确认没有向链接目标写入，文件 symlink 条件测试保持 fail-closed。
- uv 子进程会清除继承的 UV/PIP index、offline、config 与 find-links 污染；
  两次依赖安装都显式使用 `https://pypi.org/simple`，CUDA torch 只通过
  `--torch-backend=cu128` 选择官方后端，没有把 PyTorch index 设为通用源。
- GUI 仅在安装进程 exit 0、刷新自检成功且唯一 `embedding.runtime` 为 green
  时显示完成；yellow、red、缺失或重复结果均显示验证失败。日志与界面脱敏新增
  Authorization Basic/Bearer、URL userinfo 及 UV/PIP index 环境值。只读复审
  曾发现空格分隔的多值 `UV_INDEX` 只遮住首个 URL；现改为整段值脱敏并补双 URL
  回归。第二次复审又发现规则顺序会过度吞掉 JSON 转义换行后的普通诊断文字；
  交换规则顺序后，JSON 可解析且下一行原文保留。post-fix 复核还发现无冒号的
  `https://token-only@host` 未被旧 userinfo 模式覆盖；Core 与 installer 现对
  任意 `http(s)://userinfo@` 统一脱敏，直接反例与两层日志回归通过，均标记已解决。
- 新增项目本地 `embedding-install.lock` FileStream 独占锁；第二个窗口只提示
  已有安装和持有者日志。离线锁回归首次发现 `File.ReadAllText()` 的共享模式
  无法读取正在持有的写句柄；已改为显式 Read + FileShare.ReadWrite 的窄读取，
  互斥、日志可见和释放后重试均通过，标记已解决。
- launcher + installer 定向回归共 63 项，60 项通过；3 项因当前 Windows 会话
  缺少文件 symlink 创建权限（WinError 1314）按既有条件跳过。junction 回归、
  PowerShell AST、真实 WPF XAML load 与 `git diff --check` 均通过。
- 用户报告的 PID 33448 `uv` 仍视为现存首次安装。本轮没有启动第二次安装、
  没有终止该进程、没有清理运行时内容，也没有重试真实 CUDA 下载或启动 Houdini。
  仍需主任务等待该安装自然结束后，在真实 WPF 中复验成功/失败文案与日志路径。

## CUDA embedding 最终真实验收（2026-07-24，通过）

- 项目本地 uv 为 `0.11.29`。更新后的完整 `Install-HiaEmbedding.ps1` 真实运行
  22 秒成功，返回 `status=already_installed`、profile
  `qwen3-embedding-0.6b`、requested/resolved device 均为 `cuda`。
- 运行时探针确认 `torch 2.11.0+cu128`、CUDA build `12.8`、
  `cuda_available=true`，设备为 `NVIDIA GeForce RTX 5080`。脱敏安装日志位于
  `.runtime/launcher/embedding-install-20260724-150200-b5716ed9dac2430da46bdcb9a45d28a1.log`。
- 项目 uv 执行 `uv --no-config pip check`，确认 43 个 packages compatible。
  首次手工验证没有注入项目 `UV_CACHE_DIR`，误尝试用户 AppData cache 并被拒；
  改为 `.runtime/cache/embedding/uv` 且使用管理员项目权限后通过。该问题仅属于
  手工验证命令环境，不是生产 installer 缺陷，标记已解决。
- launcher `-CheckOnly -Json` 返回 overall green；`embedding.runtime` 为 green
  并明确识别 CUDA RTX 5080，期间没有启动 Houdini GUI。
- 真实独立 worker 在 offline 模式下使用 CUDA 加载
  `Qwen3-Embedding-0.6B`，返回 1024 维、L2 norm `1.0`、`loaded=true`，
  7.4 秒内完成推理。
- 最终相关 unittest `64/64` 通过；独立审计五项的 P0、P1、P2 均为 `0`。

## 启动器可选露娜溶图背景（2026-07-25，通过，待真实 DPI 人工验收）

- 用户提供的露娜插画已先做深靛紫夜景溶图，再保存为项目本地忽略资源
  `.runtime/launcher/artwork/sakurakouji-luna.png`；图片不进入 Git。概览页使用
  右侧渐显、左侧深色遮罩与裁切融合，缺失、损坏或 reparse 路径会安静回退到
  原有渐变背景，不影响自检或 Houdini 启动。
- 初次静态测试把 `<Image.OpacityMask>` 也计入 `<Image>` 数量而失败；改为只匹配
  实际 `Image` 元素后通过，标记已解决。
- 1180×820、820×656、640×480 三档离屏渲染均成功，主按钮保持可达，深色
  ScrollBar 无白色轨道；预览保存在 `.runtime/launcher/ui-luna-blended-*.png`。
- launcher 定向 unittest 共 59 项：58 项通过、1 项按环境条件跳过；PowerShell
  AST、真实 `XamlReader.Load` 均通过。`-CheckOnly -Json` 返回 overall green、
  24 项检查，未显示窗口、未启动 Houdini。
- 尚未在真实显示器上人工验证 125%/150%/200% DPI、窗口最大化及实际交互焦点。

## Panel 复杂任务信息架构第一批（2026-07-26，离线通过）

- 根因是历史、对话和 Goal/团队长期固定并排，复杂任务的公开计划又混入聊天
  System 行；侧栏内容会争夺中央宽度，蓝图、阶段和审阅也没有稳定展示位置。
- Panel 现以中央对话为最高伸缩优先级：右栏先自动收起、更窄时再收起历史栏，
  用户仍可手动展开；中央列忽略复合控件的横向 minimumSizeHint，避免反向锁住
  Houdini Pane。右栏收敛为“任务蓝图 / 阶段进度 / 审阅 / 团队”四个标签。
- Build Brief 只显示首次公开用户请求或显式 `taskInsight`/Goal 公共字段；阶段只
  显示匹配当前 Turn 的公开 plan；审阅统一为 domain、severity、对象/路径、
  evidence 和 suggested next action。跨 Thread、迟到旧 Turn、reasoning、未知
  事件和原始 JSON 不进入这些视图，公开文本复用现有凭据脱敏。
- 历史会话仍默认空白并需手动打开；精确崩溃恢复、中文 IME、非模态附件、
  Stop/steer 和现有工具活动路径未改。Panel 相邻定向回归 `184/184` 通过，
  `git diff --check` 通过。
- 尚需在真实 Houdini GUI 人工验证窄 Pane/高 DPI 下的侧栏断点、四个标签和审阅
  卡片换行，以及中文 IME、附件焦点和 Stop 冻结在真实 Qt 事件循环中无回归。

## Panel 活动 Turn 与 Goal 专注控件回归（2026-07-26，离线通过）

- 用户实测中，Turn/Goal 运行时模型、推理强度和速度选择被 `busy` 一并禁用；
  Goal 保存启动 Turn 后，专注复选框也被同一门槛锁住，必须先 Stop 才能开启。
- 模型、推理强度和速度现仅在 turn/start 尚未 ACK 或会话状态正在切换/对账时
  暂停编辑；活动 Turn 中可为下一轮调整，当前已发请求不被改写。Goal 文本、
  保存和清除仍在活动 Turn 中保持禁用，但 Goal 保存响应完成后，专注开关可在
  该 Goal Turn 运行期间直接开启。
- 最终快速定向回归 `test_panel_wiring + test_panel_state` 共 `135/135` 通过，
  耗时 `0.031s`；`git diff --check` 通过。此前完整 discover 在外部 `240s`
  时限内未产生最终汇总，只能记为超时边界，不能宣称完整套件通过。
- 尚需真实 Houdini GUI 验证：普通 Turn ACK 后三个选择器立即可用且只影响下一
  Turn；保存 active Goal 后无需 Stop 即可勾选专注模式。

## Panel 顶栏运行设置宽度回归（2026-07-26，离线通过）

- 用户截图显示顶栏只剩“模型 / 推理 / 速度”标签，三个下拉框被压到接近零宽。
  根因是所有状态和选择器共用单个横向布局，尾部 stretch 与三个 selector 的
  `minimumWidth=0`、水平 `Ignored` 策略允许字段在空间竞争中完全让出宽度。
- 三个选择器现移入独立的“运行设置（下一轮）”表单；字段使用非零最小宽度和
  `Expanding`，Qt `WrapLongRows` 在窄 Pane 时把长字段换到标签下一行。没有隐藏
  设置、横向滚动、固定大宽度或新 resize 状态；中央 splitter 仍保持最高伸缩
  优先级和水平 `Ignored`，三个 selector 的最小宽度也不会再横向累加成外层
  Pane 的大宽度门槛。
- 快速 Panel 定向回归覆盖常规/窄宽可见性与合理最小宽、活动 Turn 下一轮
  model/effort/speed 选择、Goal Turn 中开启专注、对话卡和 composer，相邻测试
  共 `151/151` 通过，耗时 `0.037s`；未运行已知会超时的整仓 discover。
- 尚需在真实 Houdini GUI 验证：常规与窄 Pane 下三个下拉框可见可点击、窄宽
  自动换行，以及中央对话区和 Houdini 外层 splitter 的实际拖动行为。

## Panel 项目记忆可见管理（2026-07-26，离线通过）

- 旧界面没有项目记忆列表与显式维护入口，用户无法确认哪些长期记录仍在生效，
  也无法从 Panel 精确新增、取代或删除一条不再需要的记忆。
- 右侧现增加紧凑的“项目记忆”页，支持搜索、刷新、查看类型/摘要/tags/scope/
  状态/稳定 ID，并显式执行 `record`、`supersede` 和单 ID `delete`。删除采用
  非模态二次点击和最小防双击间隔；没有自动记忆、清空全部或对聊天、知识资料、
  附件和项目文件的删除副作用。
- Panel 只调用 Bridge 的固定 `/v1/project-memory` 合约；Bridge 复用实时
  `hia_project_memory` 校验与认证 HIA MCP V2 runtime，返回有界展示投影，不开放
  任意工具代理、不直接操作 SQLite。直接运行 `scripts/launch-houdini.ps1` 时也
  使用同一 Bridge/runtime 路径，不依赖 WPF；项目内 CLI 仍可独立管理显式记忆。
- Panel/Bridge 相邻定向回归共 `196/196` 通过，耗时 `11.132s`。覆盖五种动作、
  固定工具与认证、响应投影、HIA V2 不可用、窄 Pane、IME 友好编辑、状态隔离、
  单 ID 二次确认及相邻 conversation/composer/task-insights 行为。
- 尚未启动真实 Houdini GUI。仍需人工验证窄 Pane/高 DPI 下第五页滚动与焦点、
  中文输入、真实 runtime 的五种操作和长 cook 期间请求等待；本轮未运行完整套件。

## 项目本地环境与知识/缓存 CLI 文档收口（2026-07-26，问题记录）

- 审计时现有 `.runtime\toolchains\hia-embedding\venv` 的 `pyvenv.cfg` 仍把基础
  Python 指向项目外部目录，因此项目移动或换机后不能视为便携、
  同源且已验证的生产环境；当时该 venv 也缺少 PDF 解析依赖 `pypdf`。
- 旧的外部/PATH Bridge Python 路径即使设置 `PYTHONNOUSERSITE=1`，也只能排除
  user site-packages，不能证明系统 `site-packages` 没有参与。正式边界改为由
  项目本地 uv 在 `.runtime\toolchains\python` 准备 managed Python，并在同一个
  `.runtime\toolchains\hia-embedding\venv` 内安装 Bridge、解析器与可选 embedding
  依赖；最终健康检查要求 executable、prefix、base prefix 与 import path 均留在
  项目根内。Houdini Python、全局 Python、用户 site-packages 和系统 PATH 不作为
  安装目标。
- 安装与修复必须走同源项目命令：WPF 调用 `scripts\hia-knowledge.ps1`、
  `scripts\hia-cache.ps1` 及显式 embedding installer；不使用 WPF 的用户运行
  相同命令。基础修复只准备 managed Python、uv、共享 venv 与 `pypdf`，没有
  PyTorch/模型时明确降级 FTS5，且不阻断 Houdini。
- 本节是文档与已观察问题的收口记录。本次文档任务没有修改产品代码，没有运行
  unittest、PowerShell AST、XAML 或完整套件，也不新增或声称任何测试通过数量。
  上文已有数字仍分别属于其原始历史轮次。
- 真实 WPF GUI 仍未在本次任务中人工验证。待验项目包括完整 venv 路径的
  Tooltip/报告、环境四类状态、文件/文件夹导入与托管副本删除提示、索引继续、
  缓存分类/大小/快照确认/逐类结果，以及不同 DPI、项目移动、代理、磁盘不足和
  NVIDIA/AMD/CPU-only 主机上的可读降级表现。

## 启动器本地知识、受管环境与缓存安全收口（2026-07-26，定向通过）

- 系统掉线后从共享工作树当前磁盘状态原地恢复，没有回滚或重做其他模块。
  WPF 的环境、资料、索引与缓存操作均调用项目相对 CLI；资料导入、列表、删除和
  重扫委托既有 `knowledge_index_cli`，删除托管副本不会删除原文件。
- Bridge、本地知识解析器和可选 embedding 复用唯一项目受管 venv。基础安装/
  修复只准备项目本地 Python、uv、同一 venv 与固定 `pypdf`，不会使用全局 pip、
  用户 site-packages、系统 PATH 或 Houdini 安装目录；模型缺失或设备不可用仍可
  降级 FTS5，不阻断基础 Houdini 启动。
- 当前机器的既有 venv 是未带受管 marker 的旧环境，基础 Python 仍来自项目外，
  且缺少 `pypdf`。真实 `environment-status` 因而正确返回
  `repair_required`、保留已探测到的 Torch/CUDA 信息并降级 FTS5；未在本轮执行
  安装或修复。`-CheckOnly -Json` 也因没有可接受的 Bridge Python 返回 red，
  没有伪报环境可用，也没有启动 Houdini GUI。
- 缓存 CLI 只暴露六个固定分类并采用双快照、精确解析路径、reparse/逃逸拒绝和
  整批零写。新增阻断确认：任一选中分类出现普通 `.hip/.hiplc/.hipnc` 文件时，
  整组选中分类不删除；最终输出可以使用 `.runtime/cache` 根，但不能等于或位于
  `screenshots`、`previews`、`tmp`、`embedding` 或 `dotnet` 分类下。真实只读
  `list` 因 Hugging Face snapshot 的 symlink 安全阻断下载缓存分类，未删除内容。
- Release 使用显式生产文件 allowlist，包含知识/缓存 CLI、安装 helper、受管
  contract 和 Panel 当前直接依赖的 `task_insights.py`；资产示例
  `src/hia_core/vending_machine.py` 不进入 ZIP。启动图说明统一为项目自有
  `assets/launcher/launcher-hero.png`，缺图时仍可回退。
- 合并定向 unittest 共运行 132 项：128 项通过，4 项因当前 Windows 会话缺少
  symlink 创建权限而按条件跳过。9 个 PowerShell 文件 AST、XAML XML 与真实
  `XamlReader.Load`、4 个 Python 文件 AST、固定盘符扫描、缓存危险删除模式扫描
  和 `git diff --check` 均通过；暂存区为空。
- 未运行完整项目套件，也未启动真实 WPF/Houdini GUI。仍需人工执行一次
  `environment-repair`，再验证真实文件/文件夹导入、托管副本删除、索引继续、
  缓存确认界面，以及 125%/150%/200% DPI、项目移动、代理、磁盘不足和
  NVIDIA/AMD/CPU-only 主机的自适应表现。

## 主审最终回归（2026-07-26，通过）

- 初次测试超时由当前 Codex 沙箱无 E 盘测试临时目录写权限引起；
  `faulthandler` 定位到 `tempfile.mkdtemp`。获批以正确权限运行后不再超时，
  确认为测试环境权限问题，不是产品死锁。
- 跨模块定向回归 `466/466` 通过，耗时 `70.995s`。
- 该轮当时的完整 unittest `992/992` 通过，耗时 `72.028s`；同轮 Release
  hygiene 通过。该计数不覆盖 2026-07-27 及之后的变更，不能作为最新发行候选
  的当前完整测试结论。
- 9 个 PowerShell 文件 AST 为 0 错误；XAML XML 解析和真实
  `Windows.Markup.XamlReader.Load` 均成功。
- 真实 WPF/Houdini GUI、0.6B/8B 的 CPU/CUDA 组合及项目目录迁移仍需人工验收。

## 启动器一键修复旧本地知识环境（2026-07-26，定向通过）

- 真实问题：旧共享 venv 仍存在，但 `pyvenv.cfg` 的 `home` 指向项目外旧 Python，
  项目内 managed Python 不存在。严格探针正确返回 `repair_required`，但此前 WPF
  只提示运行 `environment-repair`，普通 GUI 用户无法直接完成修复。已解决。
- WPF 现在根据 `missing`、`repair_required`、`unsafe` 和复检结果显示原因与
  一键安装/修复动作；它异步调用同源 `scripts\hia-knowledge.ps1`，展示阶段、
  不确定进度、脱敏项目日志和重试入口，不在 UI 中复制安装逻辑。旧 venv 的实际
  迁移仍由 installer 先验证 staging、再在项目 `.runtime` 内备份并发布，发布失败
  时恢复旧目录。
- 没有已验证模型的新用户只准备项目 managed Python、uv、共享 venv 与 `pypdf`，
  不会被动安装 PyTorch 或下载 Qwen。若修复前已验证模型存在，同一次
  `environment-repair` 会按当前 Automatic/CUDA/CPU 选择恢复 PyTorch 与
  embedding worker，并复用模型和项目缓存，不再要求第二次修复。任何失败都保留
  FTS5、知识库和模型，且不阻断 Houdini。
- 收口中修复了两项 UI 回归：非阻断知识环境不再抢占 Codex 红色阻断的通用修复
  按钮；复用跨窗口安装锁时会立即切换并读取所属日志，不再让新路径配旧正文。
  同时补全 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 的日志/报告值
  脱敏，仍只报告代理是否存在。
- launcher 定向 unittest 共运行 `90` 项：`86` 项通过，`4` 项因当前 Windows
  会话没有文件 symlink 创建权限而条件跳过，耗时 `35.270s`。覆盖 WPF/XAML 真实加载、
  PowerShell AST、missing/legacy/unsafe 动作、异步与重试状态释放、旧 venv
  staging/backup/publish/rollback 顺序、已安装模型的 `already_installed` 无下载
  复用、项目移动路径重解析和无 GUI 的 `-CheckOnly`。
- 本轮没有执行环境下载/安装、没有启动真实 WPF 或 Houdini GUI。仍需人工点击
  验证真实旧 venv 修复、全新空 `.runtime` 首装、已装模型后的 CPU/CUDA 运行时
  补全、网络/代理/磁盘不足失败后的重试，以及项目移动后的真实 GUI 显示。

## Panel/Bridge 本地知识入口（2026-07-26，定向通过）

- 原入口缺少非 WPF 用户可用的知识环境、资料与索引管理；环境 loaded、FTS5
  fallback 和索引 complete 也没有独立展示。现于既有“知识与记忆”页增加紧凑
  本地知识区，保留项目记忆 CRUD，不新增页面、数据库或安装实现。
- Panel 通过 Bridge 固定 `/v1/knowledge` 合约调用项目相对
  `scripts\hia-knowledge.ps1`。环境状态分为可用、FTS5 降级、需修复和正在修复；
  官方包、用户资料数与索引进度独立显示。文件/文件夹选择保持非模态，精确
  source ID 删除只移除托管副本；修复无安全取消契约，索引取消保留已提交批次。
- 同源 CLI 收口后，`environment-repair` 会复用已安装 profile/device 执行完整
  一键修复；Bridge 不传 `KnowledgeParserOnly`，也不复制 Torch/安装逻辑。本轮
  未实际执行修复、导入、删除或重建。
- 真实只读状态 smoke 返回：环境 `repair_required`、FTS5、官方包
  `sidefx-official-workflows-v2 2.0.0 / 42` 张卡、用户资料 `0`、索引
  `30987 / 31812`、待处理 `825`、complete=false；未知计数保持“未报告”，不再
  伪装成 `0/0`。
- Panel/Bridge/同源 CLI 及相邻 Stop、Goal、历史、IME、附件定向回归共运行
  `241` 项：`240` 项通过，`1` 项按环境条件跳过，耗时 `15.926s`。开发中首轮
  定向测试暴露旧 Panel shim 缺少新控件；最终合并首轮又暴露旧 launcher 测试
  依赖只读 `list` 顺带初始化目录，均只修正测试夹具后通过。
- 未启动真实 Houdini GUI。仍需人工验证窄 Pane/高 DPI、中文输入与焦点、真实
  完整修复进度/日志、文件和文件夹导入、托管副本删除、索引取消后继续，以及
  直接 `launch-houdini.ps1` 路径下的同一 Bridge 入口。

## 版本化内置工作流知识包与完整正文检索（2026-07-26，定向通过）

- 原实现直接从 checkout 读取一组很短的卡片，没有版本化 runtime 安装层；
  `full` 仍只是命中 chunk，status 也不能区分内置官方 workflow、用户资料与项目
  记忆。现已消费 2.0.0 manifest/coverage/source registry，把声明卡与元文件
  离线、幂等复制到 `.runtime/knowledge/builtin/<pack-id>/<version>-<digest>`，
  并用独立 built-in collection 索引完整正文。升级不覆盖用户资料或项目记忆。
- 该轮当时的磁盘包为 42 张卡、116 条 SideFX source registry 记录。加载时不假设固定
  卡数或单 URL；每卡 `source_ids` 解析并去重为多个 SideFX HTTPS 来源。安装前
  严格验证 UTF-8、非空正文、路径、可选正文 hash，以及三份元数据中的 pack
  ID/version 一致性。
- `refresh=false` 和 CLI `status` 均走 `initialize=False` 只读路径：无数据库时
  不创建 `.runtime`，已有数据库时不执行 WAL 初始化、schema migration 或写入。
  显式 `bootstrap` 才安装包并建立 FTS5；可选向量仍由可恢复的 `build` 分批完成。
- `response_format=full|diagnostic` 会按 ordinal 重建 built-in card 全文；默认预算
  足够时返回整卡，小预算时保留 match/provenance、设置
  `content_truncated=true` 并保证分页 offset 前进。compact、batch 与 17 个 MCP
  工具集合未改变。
- 全新独立临时项目的真实离线 smoke 得到 42 documents / 296 chunks、0 个混入的
  project-reference documents。长查询“FLIP particles + liquid mesh + whitewater
  + motion blur velocity”首名为 `flip-mesh-whitewater-cache`，重建正文 5460
  字符且包含尾部 production-lighting 验证段，未截断、无 warning。
- Release checker 现在要求 `manifest.json`、`coverage.json`、`sources.json` 均为
  普通有效 JSON，并强制磁盘卡、manifest 卡和 ZIP 内普通 Markdown 卡集合相等；
  该轮定向归档验证为 42/42/42。Release builder 仍使用 `git ls-files`，所以当时
  未跟踪的新卡必须进入最终提交后才会进入正式 ZIP；若遗漏，checker 会拒绝归档。
- 合并定向 unittest 共运行 100 项，全部通过，耗时 6.400s；覆盖 runtime/index/
  hybrid/CLI/MCP protocol、严格只读、包升级隔离、深正文长查询、compact/full
  预算、source registry、Release hygiene 与六项语料结构检查。`git diff --check`
  通过，仅报告既有 LF→CRLF 提示。并行语料任务最后补充 source registry 后，
  又对最新磁盘包运行 8 项 pack/manifest/Release 集合检查，8/8 通过。
- 一次直接运行完整语料测试在耗时的 near-duplicate 对比项前超过 120s；相关的
  version、manifest/source、正文结构、coverage 与 canonical inventory 六项已
  独立通过。本轮未跑完整项目套件、未构建正式 Release ZIP，也未启动真实
  Houdini/WPF；仍需随最终提交做一次正式 ZIP 和非开发机首次 bootstrap 验收。

## 旧 CUDA venv 一次修复与知识状态零写收口（2026-07-26，定向通过）

- 主审真实状态为：旧 venv 的 `base_prefix` 位于项目外且缺 `pypdf`，但仍有可用
  CUDA PyTorch、embedding worker 与已验证模型。此前 parser-only repair 会备份
  旧 venv，却留下不含向量运行时的新 active venv，仍需第二步。现同源
  `environment-repair` 会在探针确认已有模型时，于同一次用户动作中重建 managed
  Python/venv、`pypdf`、PyTorch 与 worker，并复用模型及项目 uv/Hugging Face
  缓存；无模型的新用户仍只安装解析器基线，不会隐式下载模型。
- `hia-knowledge.ps1 status` 此前会通过默认 `initialize=True` 打开已有 SQLite，
  在只读环境可能触发 WAL/schema migration 并报
  `attempt to write a readonly database`。现 aggregate status 和 `sources list`
  均以 `initialize=False`/read-only 打开；缺数据库只报告 `not_initialized`，
  不创建目录、数据库、WAL 或迁移。
- 真实项目只读 smoke 返回 `30987 / 31812`、剩余 `825`、complete=false；
  运行前后 342 MB SQLite 的 SHA-256、bytes 与 mtime 均不变。缺 runtime/DB 的
  PowerShell `status`/`environment-status` 回归还确认不会创建 `.runtime`，
  只有变更动作才建立项目内 cache/home/tmp。修复完成后 WPF 只刷新 status，
  显示该进度并启用“构建/继续索引”；不会自动启动长时间索引。
- 最终定向 unittest 运行 `115` 项：`111` 项通过，`4` 项因当前 Windows 会话缺少
  symlink 创建权限而条件跳过，耗时 `35.708s`。覆盖 legacy CUDA/model
  fail-closed、一次完整修复路由、exit 0 后严格复检、失败回滚与重试、项目移动、
  缺库零写和 partial DB 只读。
- 6 个相关 PowerShell 文件 AST 为 0 错误；XAML XML 解析及真实
  `Windows.Markup.XamlReader.Load` 成功；`git diff --check` 退出码为 0。
  本轮未执行真实安装、未自动构建索引、未启动 WPF/Houdini。仍需人工点击验证
  真实旧 venv 的 CUDA 一键修复、安装失败后重试，以及修复后实际显示
  `30987 / 31812` 并可手动继续。

## 知识分页、内置包激活与 Release Git 完整性收口（2026-07-27，定向通过）

- batch `hia_local_help_search` 过去把各 query 的续页游标取最小值作为公共
  `next_offset`，当首屏实际消费量不同时会重复或错位。现每个 query 保留独立
  cursor；只有所有待续页 cursor 一致时才返回兼容性的顶层 cursor。7 条与 4 条
  不同体积结果的第二页回归确认无重复。
- built-in pack 过去会在 SQLite 刷新前切换 `active.json`，且 freshness 窗口可能
  跳过已变更 digest。现刷新明确读取 staged pack，新 digest 强制刷新 project
  collection；active 原子切换与 SQLite 事务配对，刷新或 commit 失败会 rollback
  数据库并恢复旧 active bytes。失败注入确认旧正文仍可检索、新正文不可检索，
  status 与 active 均保持旧版本；成功路径则同步升级。
- Release source preflight 现在在 launcher 构建与 ZIP 创建前以真实
  `git ls-files -z` 核对磁盘卡、manifest 卡、tracked 卡以及
  `manifest.json`/`coverage.json`/`sources.json`。临时 Git fixture 证明完整包通过、
  未跟踪卡或元文件被拒绝。当前共享工作树的真实预检按预期返回 1：43 张卡中
  10 张已跟踪，尚缺 33 张卡及 `coverage.json`、`sources.json`；最终提交纳入
  这些文件后才允许正式构包。
- 文档现明确 partial vector 的 `ranking_scope=lexical_candidates` 只重排本 query
  的词法候选；一次 AND→OR 放宽不等于零词汇重合的全库 semantic。`refresh=false`
  保证 corpus/index 零写，不夸大为首次 embedding worker 的绝对文件系统零写。
  full 以卡为分页单位，预算截断的卡尾没有卡内 continuation cursor。
- MCP/知识定向回归 `8/8` 通过（`0.494s`）；Release hygiene/packaging
  `25/25` 通过（`1.052s`）；PowerShell AST 随 Release 测试通过，
  `git diff --check` 退出码 0。未运行完整套件、未构建真实 ZIP、未启动 Houdini。

## 本地知识环境事务回滚与只读 list（2026-07-27，定向通过）

- 已解决：旧可用 venv 被备份、新 venv 发布后，pypdf、Torch/worker 或模型安装/
  验证失败时，原实现不会恢复旧环境。现在显式 repair 的发布带唯一事务标识；
  完整 embedding 安装即使已有健康 parser venv 也强制事务发布。发布后探针返回
  false 或直接抛异常，以及后续任一阶段失败，都会先把本次新 venv 隔离到精确
  项目内路径，再恢复精确备份；marker 不匹配、重复回滚、reparse 或非 canonical
  路径均 fail-closed。故障注入覆盖 pypdf、Torch/worker、模型验证三类及发布后
  探针异常，项目外 sentinel 保持不变。
- 已解决：`hia-knowledge.ps1 list` 过去会建立
  `.runtime/cache/knowledge-cli/{home,tmp}`。现与 `status`、
  `environment-status` 共用只读子环境；缺 `.runtime` 或数据库时不创建项目路径。
- 合并定向 unittest 共 `91` 项：`87` 项通过，`4` 项因当前 Windows 会话缺少
  symlink 权限而条件跳过，耗时 `31.756s`；两份相关 PowerShell 脚本 AST 均为
  0 错误。
  开发中首轮定向运行仅暴露一条仍断言旧 repair 分支的测试，更新为当前事务语义
  后通过。
- 本轮未运行真实安装器、未启动 WPF/Houdini、未清理 `.runtime`。仍需人工验证：
  真实旧 CUDA venv 在依赖失败时的恢复、项目跨目录/跨盘移动后的可执行性及一次
  修复重建。进程在目录改名之间被强制终止的恢复需要持久事务日志，未在本次窄
  修复中扩展实现。

## HIP-local HIA 截图与 AI stage checkpoint（2026-07-27，定向通过）

- 原行为把所有自动 viewport/flipbook 放入项目
  `.runtime/cache/screenshots`，AI Goal stage checkpoint 只写当前 launcher
  session；用户已保存 HIP 后，产物与场景交付目录分离。
- runtime 现在每次调用重新读取 `hou.hipFile.path()`。真实已保存 HIP、普通非
  reparse 且可写父目录使用同级唯一 `.hia/screenshots` 与
  `.hia/checkpoints`；untitled、不存在、相对、只读、reparse 或异常路径均回退
  原项目/session 目录。结果返回 `storage_scope` 和实际绝对路径；Save As 后同一
  executor 的下一次调用自然切换。
- HIP-local checkpoint 在实际目录和当前 session checkpoints 各写一份原子 v2
  marker，绑定 launcher session、Thread、Goal、canonical saved HIP 与
  checkpoint basename。launcher 只从 session marker 出发推导
  `<hip-parent>/.hia/checkpoints`，逐项拒绝路径逃逸、reparse、marker 不一致和旧
  session，再复制到既有 session recovery 目录；没有新增恢复数据库或守护进程。
- 定向 unittest：runtime/viewport/checkpoint/recovery 共 `45/45` 通过；既有
  launcher v1 checkpoint、crash-HIP 与 lifecycle 兼容回归 `4/4` 通过；MCP protocol/tool
  集合回归 `15/15` 通过。未运行完整套件，未启动 Houdini/WPF，未清理
  `.runtime`。仍需真实 Houdini 验证当前 build 中临时
  `hou.putenv("HOUDINI_BACKUP_DIR", ...)` 对 `saveAsBackup()` 即时生效、环境恢复
  不影响手工备份，以及实际 Save As 后截图/checkpoint/崩溃恢复闭环。

## 启动器基础知识环境空 Profile 参数（2026-07-27，定向通过）

- 真实 GUI 复验发现，根 `.venv` 尚未建立且当前没有可用模型选择时，WPF 仍把
  `-Profile` 连同空字符串拼进 `powershell.exe` 命令行。空值在进程参数序列化后
  消失，导致 `hia-knowledge.ps1` 在真正安装前以
  `MissingArgument, hia-knowledge.ps1` 退出。
- WPF 现在只在模型 profile 非空时传递 `-Profile`；未选择模型时完全省略该
  可选参数，由同源 CLI 正常执行基础 managed Python、根 `.venv`、uv 与
  `pypdf` 安装，并保留 FTS5 降级路径。没有修改模型、索引、旧 venv 或任何
  项目外路径。
- 同时修正启动器核心与知识 CLI 共用的 Windows 进程参数编码器：真正的空字符串
  现在编码为 `""`，不会在 `ProcessStartInfo.Arguments` 中消失。新增回归测试会
  启动短生命周期 PowerShell 子进程，确认两个编码器都把空参数原样交给目标脚本。
- 启动器与知识 CLI 定向 unittest 共 `87/87` 通过，耗时 `29.029s`。仍需重启
  最新启动器并点击一次“安装/修复本地知识环境”，完成人工 GUI 与真实安装验证。

## 启动器误拒绝 uv Python 别名（2026-07-27，定向通过）

- uv 为 Python 3.10 建立的标准 Junction 别名被启动器误判，导致健康的项目
  `.venv` 无法成为 Bridge Python，模型与设备下拉框随之为空。
- 修复只识别项目内 uv 的标准版本别名，并映射到同目录的普通版本化 Python；
  其他 reparse 路径仍然拒绝。没有重建 venv、重装模型或清理目录。
- 启动器相关定向测试 `95/95` 通过；真实只读探针返回 managed Python 健康，
  并能读取 0.6B/8B 与自动/CUDA/CPU 选项。WPF 显示仍需重启启动器人工确认。

## Validation / inspect / Context Pack 收敛（2026-07-27，定向通过）

- `hia_validate(cook=false)` 原先仍可能通过 geometry 读取触发待 cook SOP；OBJ
  inspect 也先尝试 `geometry()` 再靠异常降级。现在待 cook SOP 只报告
  `cook_not_requested`，非 SOP 在访问 geometry 前按类别返回；fake 合约确认没有
  cook、geometry 调用或 dirty 变化。
- `empty_output` 现在只检查显式目标或 display/render/`OUT_*` 最终输出职责；
  root-only 扫描受 `limit` 约束，部分有效 geometry 统计明确为 `partial`。
  重复 `Cooking was interrupted` 折叠为根因、代表路径和受影响数量。
- Context Pack recent evidence 保留内部路径做 scope 匹配，对外只给最多 4 条
  代表路径、真实 `path_count` 与检查摘要；64 条长路径在 4 KiB Pack 中不再导致
  evidence 被整体裁掉。Panel 记忆列表显式使用 `scope=project`，空结果说明其他
  scope 并未丢失。
- 验证/协议/runtime/执行 envelope/Panel 定向回归共 `185/185` 通过，其中新
  validation/context 契约 `11/11` 通过。未启动真实 Houdini；仍需 GUI 验证
  `cook=false` 在真实 OBJ/SOP 网络不主动 cook、HIP dirty 不变，以及 Panel
  project-scope 空态文案。

## 本地知识检索性能复核（2026-07-27，定向通过）

- 真实只读基准使用完整 `qwen3-embedding-0.6b/main/1024` 索引（12,867 documents /
  32,184 chunks/vectors）及查询 `velocity advected ripple field`。分段结果为：
  模型首次载入约 `4.733s`，首条直接 encode 约 `0.266s`、热 encode 约 `0.031s`，
  FTS 约 `0.092–0.097s`，结果序列化约 `0.00005s`。现有同 session worker 已持久复用，
  `refresh=false` 未触发索引维护，未发现重复模型载入。
- 明确瓶颈为纯 Python 全库向量扫描。修复保持候选、打分、Top-K、tie、去重及降级语义：
  第一阶段只流式读取 ID 与 vector BLOB，第二阶段仅有界 hydrate 胜出项；相同乘积顺序改用
  `math.fsum(map(operator.mul, ...))`，微基准 checksum 与 Top-80 分数逐位一致。
- 同一索引前后，热 vector scan `4.433s → 2.755s`（`-37.9%`），热端到端
  `4.643s → 2.952s`（`-36.4%`）；冷端到端 `9.588s → 7.737s`（`-19.3%`）。
  Top-5 顺序不变，SQLite bytes/mtime 不变。首次 Qwen 载入仍是不可避免的固定成本，
  本轮未增加缓存系统、服务、线程池、并发或模型。
- 定向测试 `21/21` 通过；独立复审无阻断，`git diff --check` 通过。未运行完整套件，
  未启动 Houdini。

## SideFX 官方知识高价值缺口补齐（2026-07-27，定向通过）

- 新增 8 张 documented/static 工作流卡，覆盖曲线/NURBS/Subdivision、OpenCL SOP
  与 SOP Solver 编译块、Groom/UV transfer、FEM、MPM、H21 Copernicus Pyro 与
  Legacy COP2 迁移、Python Viewer State、HDK/package distribution；覆盖统计为
  51 cards / 213 sources / 22 domains / 50 workflows（49 covered、1 partial）。
- 研究中发现 SideFX 当前节点/HDK 页面常显示 H22，卡片已用 H21 change notes、
  `Since` 信息和版本边界避免冒充 H21 live 验证；`/copernicus/pyro.html` 路由返回
  Internal Error，最终改用 H21 Pyro release note、COP 节点页和迁移页作证据。
- 知识包定向 unittest `18/18` 通过，范围 `git diff --check` 与新增卡静态扫描通过。
  未运行 Houdini；GUI、GPU、solver 与 HDK 二进制加载仍属真实环境验收边界。

## Knowledge CLI 跨项目 Python 隔离（2026-07-27，已解决）

- 全量回归中，临时项目的 user-site 隔离用例被根仓库 `.venv` 内真实 `pypdf`
  解析无效 PDF，产生 `Stream has ended unexpectedly`；测试注入的伪造
  `PYTHONPATH/pypdf` 实际从未加载。
- 根因是外层 CLI 已剔除非当前项目的 site-packages，但内层 source CLI 曾无条件
  复用 `sys.executable -I`，新进程启动时又加入该解释器所属的另一项目
  `.venv`。现在仅当解释器不属于所选项目 contract 指定的 `.venv` 时使用
  `-I -S -B`；当前项目受管 `.venv` 仍保留其受控 `pypdf`，PDF 未安装与解析失败
  的既有诊断没有被吞掉。
- 原失败用例分别由 PATH Python 和项目 `.venv` 运行均通过；同链 launcher/core
  knowledge CLI 定向回归 `8/8` 通过。未再次运行完整套件，未启动 Houdini。

## 启动器 hython 冷启动超时误报（2026-07-27，已解决）

- 真实报告中，同一 Houdini 21.0.440 的 executable、sibling hython 与
  `houdini.exe -version` build 探针均通过，只有一次 12 秒
  `import hou` 探针超时；该路径在此前和约两分钟后均返回 build 21.0.440 /
  Python 3.11，随后受控 Houdini 会话也以退出码 0 正常结束。因此不能据这次
  超时判断安装损坏。知识索引数据库在失败前不久有写入，存在资源竞争的时序
  关联，但现有证据不足以认定它是唯一原因。
- 使用同一生产探针只读复现时，第一次 `hython -B -c` 成功耗时
  `22.286s`，超过原通用 `12s` 阈值；紧接的热启动约 `1.105s`。确认的直接
  根因是冷启动所需时间可超过通用阈值，并非 stdout/stderr 管道死锁。父进程
  同时存在外部 `PYTHONPATH`，它是本探针不应继承的污染入口。
- 修复仍只创建一个 hython 进程：主窗口到期后仅等待同一进程的一段有界
  grace，默认总窗口为 24 秒；主窗口小于 30 秒时总窗口最多 30 秒，显式主
  窗口达到 30 秒后不再追加 grace。子进程单独移除 `PYTHONPATH`，父环境不变，
  marker 显式 flush。grace 内成功会标黄、显示
  实际耗时并明确 `import hou` 已验证；最终超时、非零退出或缺少 marker 仍标
  红。若 Houdini build 已可读取，超时建议改为等待索引/渲染等重负载结束后
  重扫，不再直接误导用户修复安装。
- 新增子进程测试第一次因测试子 PowerShell 未显式使用 process-local
  `ExecutionPolicy Bypass` 而失败；修正夹具后未改系统策略。hython/grace/
  环境隔离/错误分级定向 unittest `10/10` 通过，PowerShell AST 解析通过。
  修复后的同一路径只读实测中 build、`import hou`、Python identity 与 build
  match 全部绿色；未启动 Houdini GUI。
- 仍需在知识索引或渲染高负载期间通过真实 WPF 重新扫描，人工确认冷启动落入
  grace 时黄色耗时文案、最终超时红色文案及按钮状态。

## Panel“知识与记忆”界面精简（2026-07-28，定向通过）

- 原界面把资料库状态、导入与索引维护、项目记忆搜索、详情和编辑表单放在同一条
  长滚动列中，窄侧栏信息密度过高，也没有解释项目记忆的创建来源。
- 现在“本地资料库”和“项目记忆”使用页内二级标签分开。资料库默认只显示环境、
  文档/内置卡片、向量和最近更新时间，维护操作默认折叠；项目记忆默认只显示
  显式记忆说明、搜索、摘要列表和选中详情，新增/取代/删除默认折叠。页面仍只有
  一个外层滚动区，没有增加后台机制或修改知识、记忆 API。
- 记忆摘要优先显示标题、来源短标签和日期；详情显示创建方式、创建时间、
  Thread/Turn、状态和 stable ID。只有用户或 Codex 显式记录才会出现，聊天不会
  自动转成项目记忆。审计发现存储/tool 结果已有 `source_thread_id` /
  `source_turn_id`，但 Bridge 的安全投影曾丢弃它们，导致新记录也被误标成旧记录。
  现仅在上游实际提供字段时限长透传；缺字段仍保持缺失，Panel 才显示
  “旧记录：来源未记录”，不做推断或修改存储 schema。
- Bridge HTTP/client 与 Panel wiring、任务洞察、状态机、网络响应、附件和资源
  相邻定向回归 `212/212` 通过；其中来源投影到 Panel 的精确链路 `4/4` 通过，
  `panel.py` Python AST 解析通过。未启动 Houdini GUI。
- 仍需真实 Houdini 人工确认：窄侧栏中二级标签与折叠区可达、中文 IME 输入、
  资料任务运行时进度显示、真实记忆来源字段展示，以及历史/Goal/Stop 操作不回归。

## Viewport/flipbook 色彩与质量证据（2026-07-28，定向通过）

- 根因是旧捕获只检查 PNG 头、尺寸和文件存在，随后无条件返回 `ok=true`；
  响应没有记录实际 Scene Viewer、viewport、camera/free view、投影、显示选项或
  OCIO 状态，也无法区分“文件写成”与“画面可信”。`viewport` 还优先调用当前
  SideFX HOM 文档未列出的 `saveViewToImage`，无法证明保存的是用户所见显示变换。
- `viewport` 和 `flipbook` 现优先复用文档化的 `SceneViewer.flipbook`，不改变
  当前 gamma/LUT override，并继续恢复 camera/free view、相机锁和帧；旧
  `saveViewToImage` 只作为单帧、明确标注的降级路径。响应记录 capture API、
  viewer/viewport、camera、投影、分辨率/裁剪、shading/lighting/display options、
  OCIO display/view、flipbook gamma/LUT 及所有不可观察项。
- 新的标准库 PNG 检查受文件、像素和采样预算限制，区分 `capture_ok`、
  `quality_status`、`visual_match` 与 `display_match`。近全黑、严重过曝、
  分辨率/宽高比或请求相机不符返回 failed；明显单通道偏色返回 warning。
  Windows HDR/OS compositor 无 HOM 证据，始终诚实标为 unverified，不做屏幕
  接管、系统 HDR 修改或写死色彩配置。
- capture/viewport 定向 unittest `23/23` 通过；5 个相关 Python 文件 AST 与
  unstaged/cached `git diff --check` 均通过。未启动 Houdini、Bridge 或 launcher，
  未捕获或修改用户场景；仍需真实 Houdini GUI 对比 SDR、OCIO 与 Windows HDR
  下的视口和输出，并确认真实相机 mask/crop、Solaris viewport 与多帧 flipbook。

## Panel 当前任务原文索引接线（2026-07-28，定向通过）

- “本地资料库”维护区新增手动“索引当前任务原文 / 移除当前任务索引”。只有明确
  选中 Thread、知识环境可用且没有活动 Turn、待处理知识请求或运行中知识任务时
  可用；结果只刷新知识状态，不修改聊天、Goal 或显式项目记忆。
- Bridge 复用现有 `thread/read` 投影，只把公开 user text 与 assistant final text
  写入 `hia-thread-snapshot/1`；安全的 Turn/Item ID 原样保留，reasoning、
  commentary、工具输出、附件正文和本地图片路径均排除。快照正文不进入命令行、
  日志或 HTTP 响应。
- 随机快照严格限制在项目 `.runtime/cache/knowledge-cli` 的非 reparse 目录和
  4 MiB 上限内；`thread-import` 成功、失败、取消或 Bridge 关闭都会只清理本次
  精确临时文件。路径逃逸与 reparse 条件 fail-closed；清理失败仅返回不含正文的
  项目内相对路径 warning。`thread-remove` 只传 Thread ID，不读取正文。
- 文件选择器加入常见音视频后缀，并明确只托管现有 SRT/VTT/TXT sidecar、不会
  复制视频本体；`TRANSCRIPT_REQUIRED` 显示可操作的中文提示，文件夹导入规则
  未改变。
- Bridge/Panel 定向回归 `244/244` 通过；launcher knowledge CLI 相邻回归
  `24/24` 通过，另有 `1` 项因当前 Windows 无文件 symlink 创建权限按既有条件
  跳过。4 个相关生产 Python 文件 AST 解析及限定范围 `git diff --check` 通过。
- 未启动真实 Houdini GUI，也未运行真实 Qwen 向量重建。仍需人工确认窄侧栏按钮
  可达、活动 Turn 中禁用、无字幕媒体提示，以及真实 Thread 导入后 FTS/Qwen
  检索结果与移除后的状态刷新。
