# DOCX 原子生成与分页预览

基线：`2f49d4f`。复用 `deep_agent_cli@f190baa` 主 Agent、SteeringMiddleware、工具边界和 SSE journal；没有增加规划 Agent 或第二套执行循环。

## 实现与边界

| 计划范围 | 实现 | 验证 |
| --- | --- | --- |
| PR01 | 可选 DOCX 扩展内的 `atomic_docx.py`：单块操作、稳定 ID、乐观并发、幂等、SQLite 快照、原子替换和恢复；移除 `create_docx` 并迁移旧配置 | 20 次提交、重复请求、并发进程冲突、发布中断恢复、模板和图片关系 |
| PR02 | `page_review.py`：LibreOffice 实际分页、结构与 PDF 几何检查、标题孤行、表格跨页、字体和页边距检查 | 真实中文排版；溢出、缺字库、标题、表格、异常页边距、重排失效与超时测试 |
| PR03 | 原子工具同步推送事件；native 工具结束检测外部 DOCX 修改；事件与 session 的 seq 在同一个数据库事务保存 | 真实 AgentRunner 连续 10 次写入，每次后续模型调用前都能读到先前事件；重建 runtime 后不重复回放 |
| PR04 | `DocxPreview.vue`：LibreOffice 分页图、页码、纸张间距、整页/页宽/比例缩放；原位更新、预解码、滚动和关闭状态 | Chromium 中的 12 页带格式文档、无编辑界面、不抢焦点、关闭/刷新/浏览状态 |
| PR05 | steer / follow_up 携带用户看到的文档版本，服务端补充最新版本；继续使用内核的安全边界 | 生成中把第二节正文改成业务表格，移除旧段落，继续后续内容，最终 112 版 |
| PR06 | 格式基准集、真实 LibreOffice 和 Chromium 入口、时延记录 | 普通报告、表格、图片、带页眉页脚及嵌套表的模板、自然分页长报告；性能数值是测量结果，不是 SLA |

### 为什么使用排版引擎的分页图

`docx-preview` 无法可靠地把没有显式分页符的 Word 正文按真实布局自动拆页。因此当前右栏直接使用 LibreOffice 的实际页面图，和页面检查共用同一份不可变渲染结果。每页有独立纸张边界、留白和页码；无需向 DOCX 注入分页符，也不会将段落拼成一条长页面。

原子提交立即入 journal。后台最多两个渲染任务，按文档合并待渲染版本，不阻塞短操作及纠偏接收。预览保留上一份已解码页面，直到新版本页面可用；界面显示正在排版。中间版本仍全部保留，预览允许跳过来不及渲染的中间版本。刷新会恢复最近的预览和验收结果，并重新安排尚未完成的渲染。

这是只读展示层；下载的仍是包含段落、表格与图片的可编辑 DOCX。分页来自 LibreOffice，**不承诺与所有 Microsoft Word 版本、字体环境逐像素相同**。缺少 LibreOffice 时显示不可用，不能用连续长页冒充分页，也不能让检查通过。

### 数据与恢复

正式产物位于统一文件库 `<document_id>.docx`。旁边的 `.docx-state/<filename>/history.sqlite` 保存每个 revision 的完整 DOCX、请求、结果和事件；`render-000000NN/` 保存不可变 PDF、PNG 和检查结果。不要只保留正式文件而删除恢复目录。该内部目录不会出现在文件列表或下载接口中。

跨进程锁串行化单文档写入。先验证候选包，再提交 SQLite；随后通过 fsync + 原子替换发布正式文件。`pending_publish` 用于恢复数据库提交后、正式文件替换前的崩溃。失败请求不创建 revision；相同 operation ID 和请求返回原提交结果，ID 重用但请求不同会拒绝。恢复历史版本是一笔新提交，revision 永不回退。

稳定 ID 使用可忽略的 OOXML 扩展属性；初始化时给模板段落、表格、表格内段落和图片段落分配 ID。`status` 和 `inspect` 可读。原有模板关系、页眉页脚等包部件保持原样。普通段落最多 3000 字符；验收基准的每个段落最多 180 字符。表格最多 10 行 × 8 列，每格最多 300 字符，并支持固定列宽、带底色的重复表头、边框和保持同页。

通用 `execute`/文件写入保持可用。它们的 DOCX 修改在工具边界被记录为外部、未经检查的版本，不能宣称通过原子工具或页面验收。无效外部写入不会覆盖历史快照，可恢复上个有效版本。

### 页面门禁与视觉审查

每三次修改至少一个检查点，章节结束时也应检查。已进入下一页时，前页视为完成；`checkpoint(final=true)` 把末页也纳入验收。检查失败阻止增加内容，允许有界修复后复检；修复本身不会清除门禁。已检查页面的渲染指纹发生变化后，需要重新检查。

默认 `normal`：基础检查通过可继续，视觉状态保持 pending。`strict`：完成页的视觉检查也必须通过才能增加内容。最终发布在两种模式下都要求全部页面通过真实视觉审查。纯文本模型不能调用视觉审查工具；有视觉能力的模型需实际查看页面并写明依据，否则交给用户在右栏查看实际截图后确认。右栏没有编辑器或保存按钮。

几何、文本映射和字体检测属于启发式检查，不能发现所有 Word 排版问题；无法唯一映射的表格会留下警告，必须查看截图。测试里的模拟审查只用于验证门禁，基准集和浏览器验收不会因此自动把未看的页面标成 passed。

## 运行与验证

在运行服务器的环境安装 LibreOffice Writer、fontconfig、Noto CJK 和 Liberation 字体，例如 Debian/Ubuntu 的 `libreoffice-writer fontconfig fonts-noto-cjk fonts-liberation`。安装 Python/前端依赖并构建后启动原有服务，无需额外服务端口。

```sh
.venv/bin/pip install -r requirements-features.lock
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m pytest -q tests docx-ooxml-skill/tests
node --test tests/*.test.mjs
```

真实带格式基准（输出目录必须是新的目录）：

```sh
.venv/bin/python scripts/benchmark_docx.py --output /tmp/docx-formatted-corpus
```

基准明确要求 A4、四边 25.4 mm、20 pt 居中标题、16 / 13.5 pt 分级标题、11 pt 短正文与 1.5 倍行距、带表头和明确列宽的表格、12 cm 流程图及居中图注。长报告自然排版，禁止硬分页和填充段落。`results.json`、DOCX、PDF、PNG、版本和检查结果都保留在输出目录。视觉状态默认 pending。

浏览器验收使用确定性测试模型，工具、内核、steering、journal、LibreOffice 和前端均为真实实现，不消耗外部模型服务。先启动独立测试服务器，再运行 Chromium：

```sh
.venv/bin/python tests/docx_e2e_server.py
CHROMIUM_PATH=/usr/bin/chromium npm --prefix frontend run test:e2e:docx
```

每次浏览器验收使用新的测试服务器进程/数据目录。默认测试端口 8081，可通过 `DOCX_E2E_PORT` 和 `DOCX_E2E_BASE_URL` 改变。截图和结果保存在 `frontend/test-results/`。测试输入是带格式的业务建设方案；途中要求第二节改成表格，最终检查至少 10 页、版本恢复、只读状态及页面几何。页面测量记录 `docx-preview-update`；测试结果另记提交到 SSE 和实际分页预览的延迟。

## 本次测量

2026-10-29，本工作环境：LibreOffice 25.2.3.2、已安装 CJK 字体、Chromium。浏览器验收生成 **12 页、112 个修改版本**，包含运行中纠偏后的业务表格，未出现页面脚本错误。独立格式基准的长报告为 **12 页、110 个修改版本**。

初次基准：普通/表格/图片/模板/长报告的单次原子提交 P95 约 20 / 101 / 27 / 83 / 40 ms；提交到引擎事件回调 P95 约 18 / 28 / 18 / 17 / 22 ms。此项**不是 HTTP SSE 的时延**。页面检查含 LibreOffice 启动与整份文档渲染，P95 约 2.0–3.0 秒。与浏览器端到端测试并行运行时，长报告检查 P95 达到 6.4 秒。

最新 Chromium 端到端测量：提交到 SSE 的 P95 为 **109.66 ms**；页面图片解码与原位替换 P95 为 **389.30 ms**；包含后端排版等待的提交到分页预览 P95 为 **6021.70 ms**（62 次可见更新）。因此本机 SSE 目标通过，完整分页预览的 1 秒目标尚未通过。

冷启动排版是当前主要成本。分页预览更新目标 P95 ≤1 秒尚不能认定达成，需要在目标部署硬件上单独测量；后续可评估常驻排版进程。当前保留真实的页面与检查状态，不以快速但错误的分页换取指标。

独立安装与卸载方式见 [可选 Office 功能](optional-office-features.md)。引擎、平台适配器、HTTP 接口与预览组件都位于 DOCX 功能目录内，主 Agent 仅通过通用注册器使用它。

拆分为独立功能后的双端浏览器并行验收再次测得：DOCX 提交到 SSE P95 139.28 ms，页面解码与替换 P95 326.60 ms，含后台排版等待的完整分页更新 P95 9219.12 ms。并行负载下的实际分页时延仍未达到 1 秒目标。
