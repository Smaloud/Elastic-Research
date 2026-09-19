# Paperlib

个人单机、本地优先的论文与书籍知识库。日常使用全部通过网页界面完成，不需要打开终端。

## 可视化启动

前提：Mac 已安装 Docker Desktop。

1. 双击项目目录中的 `Paperlib.app`。
2. 选择“启动并打开”。启动器会等待 Docker 和数据库就绪，然后自动打开 Paperlib。
3. 需要停止时再次双击 `Paperlib.app`，选择“停止服务”。停止服务不会删除论文、模型配置或数据库。

如果 macOS 首次阻止打开，请在 Finder 中右键 `Paperlib.app` →“打开”。请保持 `Paperlib.app` 与 `compose.yaml` 位于同一目录；整个项目目录可以移动。

## 当前可用功能

- PostgreSQL + pgvector 本地存储；
- 手工录入论文、书籍、学位论文、报告和章节；
- 上传 PDF，按页面提取、切块和索引文本；
- DOI、arXiv ID 归一化与去重；
- 作者、日期、人工标签、重要度、熟悉度和阅读状态；
- 关键词、向量与 RRF 混合检索；
- 本地 embedding 模型，PDF 与模型缓存都留在本机；
- 可视化配置 Ollama 或 OpenAI 兼容 LLM；
- 按精细科研 taxonomy 自动提取研究目标、贡献、任务、术语、方法、算法、架构、数据集、基准、模型、软件、代码仓库、硬件、算力环境、基线、指标、超参数、训练与实验配置、评测协议、统计检验、结果、消融、工程技巧、局限、未来工作和伦理信息；
- 每条结构化信息保存类别、子类、实体名、类别专属属性、页码、逐字证据与置信度；
- 有原文依据的提取结果和最多 20 个标签自动采用，无需逐项审核；
- Semantic Scholar 关键词搜索与候选预览；
- 展开一篇论文的引用或参考文献，并保存本地引用关系；
- 选择 2–5 篇本地种子，查找同时引用全部种子的后续研究。
- Zotero 个人资料库非破坏式双向同步：导入已有文献，并把手工或 Semantic Scholar 新增记录自动写回专用 collection；

## 在界面中接入 LLM

打开顶部“LLM 提取”页。

### 本机 Ollama

1. 接入方式选择“本机 Ollama”。
2. 地址保留 `http://host.docker.internal:11434/v1`。
3. 填写已经在 Ollama 中安装的模型名，例如 `qwen3:8b`。
4. 勾选“启用”，保存，然后点击“测试连接”。

Ollama 的安装和模型下载暂时由外部 Ollama 应用完成；Paperlib 不会替你静默安装大模型。

### OpenRouter 或其他兼容接口

1. 接入方式选择“OpenAI 兼容接口”。
2. OpenRouter 地址使用 `https://openrouter.ai/api/v1`；其他服务填写其兼容端点。
3. 填写模型 ID 和 API Key，勾选“启用”。
4. 保存，然后点击“测试连接”。

API Key 以仅当前系统用户可读的权限保存在本项目 `data/private/llm.json`，网页永远不会返回完整密钥。它是受文件权限保护的本地明文，并未额外加密；使用云端模型时，待分析的论文文本会发送给你配置的服务商。

## LLM 提取流程

1. 在资料库添加带 PDF 的论文。
2. 在论文卡片点击“LLM 提取”。
3. 系统按页分块分析全文，并把各分块结果合并、去重。
4. 类别有效且证据能在原文中逐字对齐的结果会自动采用；无法对齐的条目会自动丢弃。
5. 最多 20 个模型标签自动进入资料库，可立即用于搜索筛选。

为控制调用成本，长文最多分析 8 个配置大小的文本分块；达到上限时界面会明确提示。重新提取会刷新该论文原有的模型结果与自动标签，不影响人工标签。

## Semantic Scholar 论文发现

打开顶部“论文发现”页：

1. 可以直接按标题、作者或关键词搜索外部论文。
2. 选择一篇本地论文，可查看引用它的论文或它引用的论文。
3. 在“谁同时引用了这些论文”中按住 `⌘` 选择 2–5 篇种子，再点击“查找交集”。
4. 结果只会预览；点击“导入本地库”后才保存论文元数据和引用关系。

大多数 Semantic Scholar 接口允许匿名访问，但匿名请求共享限流。遇到 429 时可稍后重试，或在页面内展开“Semantic Scholar API 设置”并填写自己的 Key。系统严格按约 1 请求/秒节流。Key 保存在 `data/private/semantic_scholar.json`，不会进入 Git。

## Zotero 双向同步

打开顶部“Zotero 同步”页：

1. 在 [Zotero API Keys](https://www.zotero.org/settings/keys/new) 创建一个只供 Paperlib 使用的 Key，并允许读取、写入个人资料库。
2. 在页面填写 Key、勾选启用并保存，然后点击“测试连接”。用户 ID 会自动识别，不需要手工查找。
3. 测试成功后刷新 collection 列表，只选择希望交给 Paperlib 的一个或多个 collections，然后再次保存。Paperlib 不会扫描未选择的 Zotero collections。
4. 点击“立即双向同步”。首次同步会读取所选范围，并按 DOI、arXiv、标题和年份关联或去重；Paperlib 中尚未关联的文献会写入指定的输出 collection。
5. 启用“自动写入”后，手工新增或从 Semantic Scholar 导入的论文会同时写入 Zotero；Paperlib 运行期间还会每 15 分钟执行一次增量同步，打开 Zotero 页面时也会立即同步。

同步默认不传播删除，也不会用空值或远端字段覆盖 Paperlib 已有人工信息。当前版本同步文献元数据和标签，PDF 附件双向传输将在下一阶段加入。Key 以仅当前系统用户可读的权限保存在 `data/private/zotero.json`，不会进入 Git。

## 让 Codex 使用本地论文库

项目随附并已安装 `$paperlib-research` skill。Paperlib 启动后，可以在 Codex 中直接提出这类请求：

- “用 `$paperlib-research` 比较库内 UWB 定位论文的实验数据集、指标、基线和结果”；
- “整理这些论文共同使用的 engineering tricks，并附论文、年份和原文证据”；
- “汇总作者明确写出的局限与未来工作，再区分哪些研究空白是跨论文推断”；
- “列出某个方向使用的方法、模型、软件、硬件和算力环境，不要混为一类资源”。

skill 默认通过只读本地 API 查询 PostgreSQL/pgvector，不直接写数据库，也不会读取或显示保存的密钥。新安装的 skill 可能需要在新的 Codex 任务中才能被自动发现。已有论文如仍使用旧版宽泛类别，可在论文卡片重新执行一次“LLM 提取”，以获得实验结果、工程技巧等精细类别后再做比较。

## 数据位置与边界

- PDF：`data/files`
- LLM 配置：`data/private/llm.json`
- Semantic Scholar 配置：`data/private/semantic_scholar.json`
- Zotero 配置：`data/private/zotero.json`
- PostgreSQL：Docker 命名卷 `paperlib_db`
- 向量模型：Docker 命名卷 `paperlib_models`
- 服务仅监听 `127.0.0.1`，尚未开放实验室多用户访问。
- 扫描版 PDF 会标记为 `needs_ocr`；OCR/GROBID 尚未接入。
- Codex 已可通过随附 skill 做结构化综述、实验结果对比和工程技巧整合；MCP 服务与高级图表分析尚未接入。

## 开发者故障排查（非日常操作）

本地网页：<http://127.0.0.1:8765>

API 文档：<http://127.0.0.1:8765/docs>

自动化测试位于 `tests/`。日常导入、检索、模型配置和结构化提取都不需要使用这些命令或 API。
