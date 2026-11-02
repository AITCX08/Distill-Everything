[← 返回 Distill-Everything](../../CLAUDE.md) > **src/reader**

# src/reader -- 文档蒸馏入口（替代阶段 1-3）

## 变更记录 (Changelog)

| 日期 | 变更 |
|---|---|
| 2026-04-21 | 初始化模块级 CLAUDE.md（架构师扫描补齐） |
| 2026-11-02 | 扩展格式：md/html/csv/json/srt/pptx/xlsx/epub 走 `extra_readers.py`；新增 `media_reader.py` 处理 22 种音视频扩展名（ffmpeg 抽音轨 + FunASR） |

---

## 模块职责

本模块是 **B 站流水线之外的第二入口**：

- 读取 `.txt` / `.docx` / `.pdf` 文档
- 输出与阶段 3 完全兼容的 `cleaned JSON`
- 因此可以**复用阶段 4-5**（知识建模 + SKILL.md 生成），不需要跑 crawl / asr / clean

使用场景：用户有人物访谈稿、书籍、演讲实录等文本材料，希望生成同样格式的 SKILL.md。

**调用方**：`main.py::distill` CLI 命令。

---

## 入口与启动

| 入口 | 用途 |
|---|---|
| `read_document(file_path) -> str` | 按扩展名分派到 `read_txt` / `read_docx` / `read_pdf` |
| `read_txt(file_path) -> str` | UTF-8 读入纯文本 |
| `read_docx(file_path) -> str` | 用 `python-docx` 抽段落 |
| `read_pdf(file_path) -> str` | 用 `PyMuPDF (fitz)` 抽页文本 |
| `split_into_chapters(text) -> list[dict]` | 按章节标题（"第 X 章" / "1. " / "一、"）拆分；降级为段落聚合（≥500 字为一段） |
| `generate_doc_id(file_path) -> str` | `DOC_{stem}_{md5_6}`，替代视频的 bvid |
| `document_to_cleaned(file_path, llm_client, doc_title) -> dict` | **主入口**：read → split → LLM 主题切分 → 组装 cleaned_doc |

---

## 对外接口（下游消费点）

`document_to_cleaned()` 返回的 dict **与 `clean/text_processor.py::process_transcript` 输出完全同 schema**（见 `src/clean/CLAUDE.md`）。

关键字段差异（与 B 站路径对比）：

| 字段 | B 站路径 | 文档路径 |
|---|---|---|
| `bvid` | `BV1xxxxxx` | `DOC_{stem}_{md5_6}` |
| `source` | `"funasr"` 或 `"bilibili_cc_subtitle"` | `"document:{filename}"` |
| `metadata` | 包含 `pubdate / duration / view_count / comment_count` | 包含 `file_path / file_format / char_count / segment_count` |

下游 `model / generate` 模块不区分两种路径，只读 `bvid / title / full_text / topics / segments`，所以兼容性良好。

---

## 关键依赖与配置

- `python-docx`（仅 docx 路径用）
- `PyMuPDF (fitz)`（仅 pdf 路径用）
- `src/clean/text_processor.py::TextProcessor` + `LLMClient`（通过 `create_llm_client` 获取）
- 全部延迟导入（根级硬规则 #4）

---

## 章节拆分策略

`split_into_chapters` 的双档回退：

1. **章节模式**：正则匹配 `第[一二三…]?章节篇 / \d+[\.、] / [一二三…][、.]`，若命中 ≥3 次则按章节边界拆。
2. **段落聚合**：否则按 `\n\n` 拆段，累积到 500+ 字就封一块。

这一步的产出进入 `segments` 字段（保留 `start/end` 为字符偏移，不是时间戳）。

---

## 常见修改模式

### 支持新文档格式（例如 `.md` / `.epub`）

1. 在 `SUPPORTED_FORMATS` 加扩展名。
2. 写 `read_md(file_path) -> str` 函数。
3. `read_document` 的 `readers` dict 加映射。
4. **不要**让新格式打破返回契约（必须是 `str`）。

### 改进章节识别（支持英文 "Chapter X"）

修改 `split_into_chapters` 的 `chapter_pattern`：

```python
chapter_pattern = re.compile(
    r"^(第[一二三...]+[章节篇]|"
    r"Chapter\s+\d+|"          # 新增
    r"\d+[\.、]\s*.+|"
    r"[一二三...]+[、\.]\s*.+)",
    re.MULTILINE,
)
```

测试：确保英文 PDF 能识别，同时旧中文文档仍然 work。

### 替换主题切分策略（不走 LLM）

当前 `document_to_cleaned` 里 `if llm_client: LLM else: 每段为一 topic`。若想离线用向量聚类：
- 新增 `segment_by_embedding` 方法（建议放 `src/clean/text_processor.py`，保持 reader 纯"读取"）。
- 在 `document_to_cleaned` 里加分支。

### 处理超长文档（> 100 页 PDF）

当前 `read_pdf` 不限制长度，全量读入内存。对 10MB+ 的 PDF 会：
- `document_to_cleaned` 返回的 `full_text` 超长 → 下游 `extract_from_video` 已经截断到前 10000 字，但 SKILL.md 画像合成会基于不完整信息
- 解决方案：分册生成（把书按章节切成多个 cleaned_doc，各自跑 extract_from_video，最后合成画像时 summaries 已经是 50 个章节级别的知识）

---

## 反模式（不要做）

- **不要**在模块顶部 `from docx import Document` 或 `import fitz`（只在对应 `read_*` 函数内导入）。
- **不要**破坏返回的 cleaned_doc schema —— 它和 B 站路径共享下游。改 schema 前先看 `src/clean/CLAUDE.md`。
- **不要**让 `generate_doc_id` 依赖绝对路径 —— 当前用 `file_path.resolve()` 的 md5，同一文件放不同目录会生成不同 ID。这是**故意行为**（允许多次调入同一文件的不同版本），但如果希望去重则改为 content hash。
- **不要**在 `read_pdf` 里忽略 `doc.close()`，PyMuPDF 的文件句柄不释放会占内存。
- **不要**把文档读取逻辑散到 `main.py`；保持本模块是"读入 → cleaned_doc"的单一职责。

---

## 测试与质量

- **未覆盖**：本模块无单元测试。
- **建议补充**：
  - `generate_doc_id` 的幂等性（同文件多次调相同 ID）
  - `split_into_chapters` 对纯散文（无章节标记）的降级行为
  - `read_pdf` / `read_docx` 用 minimal fixture 测试 text 提取

---

## FAQ

**Q1：PDF 里有表格 / 图片怎么办？**
A：PyMuPDF 的 `page.get_text()` 只抽纯文本，表格会变成按行抽出的字符串（通常质量不佳），图片忽略。需要 OCR 建议预处理，不是本模块职责。

**Q2：docx 里的加粗、标题格式会丢吗？**
A：是，只抽 `p.text`。若需要保留结构，改用 `doc.paragraphs` 的 `style.name` 判断是否 Heading 并映射为章节。

**Q3：`distill` 命令对单文档也会调 `merge_knowledge([knowledge], up_uid=0)`，有意义吗？**
A：有。`merge_knowledge` 负责合成 BloggerProfile（身份卡、心智模型、expression_dna 这些字段只能在合成阶段产生，单视频知识提取不生产）。单文档时 LLM 把整本书当"50 集视频"处理。

**Q4：文件名有特殊字符（空格、括号）能用吗？**
A：`generate_doc_id` 用 `file_path.stem` 作为 ID 一部分，`DOC_我的文章 (1)_abc123` 是合法字符串但最终落盘 `data/knowledge/DOC_我的文章 (1)_abc123.json` 可能在 Windows 有问题。建议用 ASCII 文件名。

---

## 相关文件清单

| 文件 | 用途 |
|---|---|
| `src/reader/__init__.py` | 模块标记（空） |
| `src/reader/document_reader.py` | 读取 + 拆分 + 组装 cleaned_doc |
| `main.py::distill()` | CLI 命令调用处 |
| `src/clean/text_processor.py` | 下游：`TextProcessor.segment_by_topic` |
