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
- 自动提取摘要、术语、资源、方法、数据集、实验发现、局限和最多 20 个建议标签；
- 提取结果逐项审核，只有接受后的建议标签才写入检索标签。
- Semantic Scholar 关键词搜索与候选预览；
- 展开一篇论文的引用或参考文献，并保存本地引用关系；
- 选择 2–5 篇本地种子，查找同时引用全部种子的后续研究。

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
3. 在审核窗口检查模型给出的内容和证据文本。
4. 对每项点击“接受”或“拒绝”。
5. 接受建议标签后，它会进入资料库并可用于搜索筛选。

长文当前单次最多发送配置页指定的字符数，超过部分会显示警告。分段提取与分层汇总属于下一阶段。

## Semantic Scholar 论文发现

打开顶部“论文发现”页：

1. 可以直接按标题、作者或关键词搜索外部论文。
2. 选择一篇本地论文，可查看引用它的论文或它引用的论文。
3. 在“谁同时引用了这些论文”中按住 `⌘` 选择 2–5 篇种子，再点击“查找交集”。
4. 结果只会预览；点击“导入本地库”后才保存论文元数据和引用关系。

大多数 Semantic Scholar 接口允许匿名访问，但匿名请求共享限流。遇到 429 时可稍后重试，或在页面内展开“Semantic Scholar API 设置”并填写自己的 Key。系统严格按约 1 请求/秒节流。Key 保存在 `data/private/semantic_scholar.json`，不会进入 Git。

## 数据位置与边界

- PDF：`data/files`
- LLM 配置：`data/private/llm.json`
- Semantic Scholar 配置：`data/private/semantic_scholar.json`
- PostgreSQL：Docker 命名卷 `paperlib_db`
- 向量模型：Docker 命名卷 `paperlib_models`
- 服务仅监听 `127.0.0.1`，尚未开放实验室多用户访问。
- 扫描版 PDF 会标记为 `needs_ocr`；OCR/GROBID 尚未接入。
- 自动综述、代理/MCP 接口和高级图表分析尚未接入。

## 开发者故障排查（非日常操作）

本地网页：<http://127.0.0.1:8765>

API 文档：<http://127.0.0.1:8765/docs>

自动化测试位于 `tests/`。日常导入、检索、模型配置和审核都不需要使用这些命令或 API。
