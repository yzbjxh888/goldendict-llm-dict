# goldendict-llm-dict
面向 GoldenDict / GoldenDict-ng 的 AI 查询脚本，支持单词、短语、中文术语/句子/问句/段落、LaTeX 公式与代码块解析。


# gd_llm.py — 桌面词典 AI 查询脚本

`gd_llm.py` 是一个面向 GoldenDict / GoldenDict-ng / 其他桌面弹窗词典的 AI 查询脚本。  
它接收单词、短语、中文术语、中文句子、中文问句、中文段落、LaTeX 公式、代码片段或命令行，调用兼容 OpenAI Chat Completions 的大模型接口，最后输出一段 HTML，供词典软件弹窗显示。

脚本只依赖 Python 标准库，不需要安装第三方包。

---

## 功能特性

- 英文单词：音标、词性释义、词源词根、常用搭配、双语例句、记忆钩子。
- 英文短语/习语：字面义、整体释义、中文对应说法、用法、近反义、常见错误、例句。
- 中文术语：语境、定义、定义方法解释、总结辨析、上位/下位概念、例句。
- 中文语句：语体、句意解释、知识结构、修辞、改写、用法提示。
- 中文疑问句：直接回答、关键概念、依据推导、易错点、可继续追问方向。
- 中文段落：段落主旨、逐句要点、句间逻辑、关键概念、要点与易错。
- LaTeX 公式：读法、类型、含义、结构拆解、符号说明、成立条件、应用、用法、相关延伸。
- 代码块：代码原文、整体解释、语言、逐段说明、核心要点、常见坑。
- 通用技术内容：摘要、核心要点、常见坑、示例。
- 内置分类器：自动判断输入类型，不需要手动选择模式。
- 内置本地 LaTeX → HTML 渲染：支持分式、根号、上下标、矩阵、cases、aligned、希腊字母、装饰符等，不依赖网络 CDN。
- 结果缓存：默认开启，改 prompt 或模型后旧缓存自动失效。
- 静默拦截：URL、邮箱、路径、文件名、英文整句、超长内容等不会发起 API 请求。
- 自测与诊断：`--selftest` 不调用 API，`--explain` 显示分类依据。

---

## 环境要求

- Python 3.7+，建议 Python 3.8 或更高版本。
- 可访问大模型 API 的网络环境。
- 一个兼容 OpenAI Chat Completions 的 API Key。
- 如果用于 GoldenDict / GoldenDict-ng，需要词典软件支持“程序词典”或“外部命令”。

脚本默认 API 配置在文件顶部：

```python
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
API_KEY = ""  # 请填写您的 API Key
MODEL_NAME = "gemini-3.5-flash-lite"
```

使用前必须填写 `API_KEY`，必要时修改 `API_URL` 和 `MODEL_NAME`。

---

## 安装

1. 保存脚本，例如：

```text
/path/to/gd_llm.py
```

2. 确认 Python 可运行：

```bash
python3 --version
```

3. 给脚本执行权限（可选）：

```bash
chmod +x /path/to/gd_llm.py
```

4. 编辑脚本顶部，填入 API Key：

```python
API_KEY = "你的 API Key"
```

5. 运行自测，确认分类器和 LaTeX 渲染正常：

```bash
python3 /path/to/gd_llm.py --selftest
```

自测不会调用 API。

---

## 配置

### API Key

当前版本需要直接编辑脚本中的 `API_KEY`。  
不要把填好 Key 的文件上传到公开仓库。

如果你希望从环境变量读取，可以自行改成：

```python
API_KEY = os.environ.get("GD_LLM_API_KEY", "")
```

然后设置：

```bash
export GD_LLM_API_KEY="你的 API Key"
```

### 模型与接口

在脚本顶部修改：

```python
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL_NAME = "gemini-3.5-flash-lite"
```

只要服务商兼容 OpenAI 的 `/chat/completions` 格式，通常可以替换为其他地址和模型名。

---

## 快速开始

```bash
# 查询英文单词
python3 gd_llm.py construct

# 查询英文短语，多个参数会自动合并
python3 gd_llm.py break the ice

# 查询中文术语
python3 gd_llm.py 机器学习

# 查询中文疑问句
python3 gd_llm.py 为什么天空是蓝色的？

# 查询 LaTeX 公式，建议用单引号避免 shell 解释反斜杠
python3 gd_llm.py '\frac{1}{2}'
python3 gd_llm.py '$E=mc^2$'

# 从 stdin 读取，推荐用于多行代码或含换行内容
echo "break the ice" | python3 gd_llm.py -
cat main.py | python3 gd_llm.py -
python3 gd_llm.py - < code.txt
```

输出为 HTML 片段，直接打印到 stdout。  
如果输入被静默拦截，stdout 为空，程序退出码为 0。

---

## 命令行用法

```text
用法：
  gd_llm.py <查询词>               查询单词/短语/代码/中文术语/中文语句/中文疑问句/中文段落/LaTeX 公式
  gd_llm.py <词1> <词2> ...        多个参数自动合并为一个查询
  gd_llm.py -                     从 stdin 读取查询内容
  gd_llm.py --selftest / -s       运行自测（分类器 + LaTeX 渲染，不调用 API）
  gd_llm.py --explain / -x <查询>  打印分类诊断（归一化/特征/规则命中与否决，不调用 API）
  gd_llm.py --cache-clear         清空结果缓存
  gd_llm.py --help / -h           显示帮助
```

### 示例

```bash
# 英文单词
python3 gd_llm.py construct

# 英文短语
python3 gd_llm.py break the ice

# 中文术语
python3 gd_llm.py 逻辑斯谛映射

# 中文语句
python3 gd_llm.py "深度学习是一种机器学习方法。"

# 中文疑问句
python3 gd_llm.py "为什么天空是蓝色的？"

# 中文段落
python3 gd_llm.py "深度学习是一种机器学习方法。它通过多层网络拟合数据。参数变化时行为会变得复杂。"

# LaTeX
python3 gd_llm.py '\frac{\partial f}{\partial x} = 2x'

# 多行代码，使用 stdin 模式
python3 gd_llm.py - <<'EOF'
def mean(xs):
    return sum(xs) / len(xs)
EOF

# 分类诊断，不调用 API
python3 gd_llm.py --explain "为什么天空是蓝色的？"

# 清空缓存
python3 gd_llm.py --cache-clear
```

---

## 与 GoldenDict / GoldenDict-ng 集成

不同版本的 GoldenDict 配置界面略有差异，核心思路是：

- 添加一个“程序词典”或“外部命令词典”；
- 让 GoldenDict 把查询词通过参数或标准输入传给脚本；
- 脚本输出 HTML，GoldenDict 在弹窗中显示。

### 方式一：标准输入模式（推荐）

命令行类似：

```bash
python3 /path/to/gd_llm.py -
```

并在 GoldenDict 中勾选“使用标准输入”或“通过 stdin 传递查询词”。  
这种方式最适合多行代码、段落、含换行的查询内容。

### 方式二：参数模式

如果 GoldenDict 支持 `%GDWORD%` 之类的占位符，可以使用：

```bash
python3 /path/to/gd_llm.py %GDWORD%
```

脚本会把多个参数自动合并为一个查询，所以 `break the ice` 这类短语通常也能处理。

### 输出说明

- 正常结果：输出 HTML 片段。
- 被拦截：不输出内容，GoldenDict 可视为“无结果”，不弹窗。
- API 错误：输出带错误信息的 HTML。

---

## 自动分类与拦截规则

脚本会根据输入内容自动选择解析类型。

| 输入类型 | 路由类型 | 显示标签 |
|---|---|---|
| 英文单词 | `english_word` | 📖 单词 |
| 英文短语/习语 | `english_phrase` | 🔤 短语 |
| 中文术语 | `chinese` | 🀄 中文 |
| 中文语句 | `chinese_sentence` | 🀄 句解 |
| 中文疑问句 | `chinese_question` | ❓ 问答 |
| 中文段落 | `chinese_paragraph` | 📄 段落 |
| LaTeX 公式 | `latex` | 📐 公式 |
| 多行代码块 | `code` | 💻 代码块 |
| 单行代码/命令/技术名词 | `general` | ⚙️ 通用 |

常见拦截类型：

- 空输入；
- URL；
- 邮箱；
- Windows 盘符路径；
- Unix 路径；
- 反斜杠路径；
- 常见文件名，如 `config.py`、`photo.jpg`；
- 完整英文句子，如 `How are you?`；
- 疑似无意义字符序列；
- 超长内容。

注意：

- 中文语句和中文段落不会被当作“完整英文句子”拦截。
- 全角问号 `？` 结尾会走中文疑问句路由。
- 半角问号 `?` 不参与中文疑问句判定。
- 英文单词默认上限 15 个字符。
- 英文短语默认最多 5 个单词。

---

## 输出格式

脚本输出 HTML 片段，主要结构包括：

- 标题与类型标签；
- 正文解析；
- 底部“用浏览器搜索”外链：百度、Bing、Google。

外部词典软件只需要把 stdout 内容作为 HTML 展示即可。

---

## 缓存

默认开启结果缓存。

- 缓存位置：`$XDG_CACHE_HOME/gd_llm` 或 `~/.cache/gd_llm`
- 默认有效期：30 天
- 默认最大条目：2000
- 缓存键包含：system prompt 哈希 + 模型名 + 查询内容
- 修改 prompt 或模型名后，旧缓存自动失效

清空缓存：

```bash
python3 gd_llm.py --cache-clear
```

关闭缓存：

```bash
export GD_LLM_CACHE=0
```

---

## 自测与诊断

### 自测

```bash
python3 gd_llm.py --selftest
# 或
python3 gd_llm.py -s
```

自测内容包括：

- 分类器路由用例；
- LaTeX 渲染用例；
- JSON 修复用例。

不会调用 API。

### 分类诊断

```bash
python3 gd_llm.py --explain "为什么天空是蓝色的？"
# 或
python3 gd_llm.py -x "为什么天空是蓝色的？"
```

会打印：

- 归一化后的文本；
- 提取到的特征；
- 规则命中与否决过程；
- 最终决策。

不会调用 API。

---

## 环境变量

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `GD_LLM_TIMEOUT` | `60` | 单次请求超时秒数 |
| `GD_LLM_ATTEMPTS` | `2` | 总尝试次数 |
| `GD_LLM_MAX_TOKENS` | `8192` | 模型最大输出 token |
| `GD_LLM_DEBUG` | `0` | 设为 `1` 时在错误页输出 traceback |
| `GD_LLM_CACHE` | `1` | 设为 `0` 关闭缓存 |
| `GD_LLM_CACHE_TTL` | `2592000` | 缓存有效期秒数，默认 30 天 |
| `GD_LLM_CACHE_DIR` | `~/.cache/gd_llm` | 缓存目录 |
| `GD_LLM_CACHE_MAX` | `2000` | 缓存条目上限 |
| `GD_LLM_CN_PURE_MAX` | `20` | 纯中文术语有效字符上限 |
| `GD_LLM_CN_MIXED_MAX` | `30` | 中英夹杂术语有效字符上限 |
| `GD_LLM_CN_SENT_MAX` | `120` | 中文语句有效字符上限 |
| `GD_LLM_CN_Q_MAX` | `120` | 中文疑问句有效字符上限 |
| `GD_LLM_CN_PARA_MAX` | `600` | 中文段落有效字符上限 |
| `GD_LLM_CN_PARA_MIN_PUNCT` | `1` | 判为段落所需最少句末标点；代码实际默认 1 |
| `GD_LLM_CN_SENT_MIN` | `4` | 以 `；！…` 结尾判句解的最小有效字符 |
| `GD_LLM_CN_PUNCT_END_ONLY` | `1` | `1` 只认结尾标点；`0` 恢复旧行为 |
| `GD_LLM_CN_DOMINANT_MIN` | `6` | 中文主导判定的汉字数阈值 |
| `GD_LLM_CODE_MAX` | `80` | 单行代码/命令有效字符上限 |
| `GD_LLM_LONG_CODE_LEN` | `80` | 长文本代码优先阈值 |
| `GD_LLM_CODE_BLOCK_MAX` | `1000` | 多行代码块/任意文本绝对上限 |
| `GD_LLM_MIXED_MAX` | `16` | 非中文未知混合串兜底上限 |

有效字符通常指中英文字母数量，标点、符号、数字一般不计入。

示例：

```bash
export GD_LLM_TIMEOUT=120
export GD_LLM_MAX_TOKENS=16384
export GD_LLM_CACHE=0
python3 gd_llm.py "为什么天空是蓝色的？"
```

---

## 常见问题

### 1. 为什么没有输出？

可能被分类器静默拦截。用诊断模式查看：

```bash
python3 gd_llm.py --explain "你的查询内容"
```

常见原因：

- 完整英文句子；
- URL、邮箱、路径、文件名；
- 超长内容；
- 英文单词超过 15 字符；
- 英文短语超过 5 个单词。

### 2. 提示 API 查询失败怎么办？

检查：

- `API_KEY` 是否填写；
- `API_URL` 是否正确；
- `MODEL_NAME` 是否可用；
- 网络是否可以访问接口；
- API 额度、限流、超时；
- 可尝试增大超时：

```bash
export GD_LLM_TIMEOUT=120
```

### 3. 多行代码换行丢失？

使用 stdin 模式：

```bash
python3 gd_llm.py - < code.txt
cat code.py | python3 gd_llm.py -
```

不要直接在命令行参数里传多行内容，否则换行可能被 shell 处理掉。

### 4. 缓存不更新？

清空缓存：

```bash
python3 gd_llm.py --cache-clear
```

修改 prompt 或模型名后，旧缓存也会自动失效。

### 5. GoldenDict 不弹窗？

如果查询被静默拦截，脚本不会输出 HTML，GoldenDict 可能视为无结果。  
用 `--explain` 确认是否被拦截。

---

## 安全与费用

- API Key 只应保存在本地，不要提交到公开仓库。
- 使用大模型 API 可能产生费用，请留意服务商价格与配额。
- 脚本默认启用缓存，可减少重复请求。
- 被静默拦截的输入不会发起 API 请求。

---

## 已知限制

- 英文完整句子默认不解析。
- 纯英文单词默认不超过 15 个字符。
- 英文短语默认不超过 5 个单词。
- 中文长文本需要以句末标点结尾才更容易进入句解或段落路由。
- 半截划选文本可能被拦截，以避免无效 API 请求。
- LaTeX 渲染是本地轻量实现，不保证覆盖全部 TeX 宏包。
- 模型输出质量受所选模型、提示词和服务商策略影响。

---

## 最短使用流程

1. 编辑 `gd_llm.py`，填写 `API_KEY`。
2. 运行自测：

```bash
python3 gd_llm.py --selftest
```

3. 测试查询：

```bash
python3 gd_llm.py construct
python3 gd_llm.py "为什么天空是蓝色的？"
```

4. 在 GoldenDict 中添加程序词典：

```bash
python3 /path/to/gd_llm.py -
```

并勾选“使用标准输入”。

5. 如无结果，用诊断模式排查：

```bash
python3 gd_llm.py --explain "你的查询内容"
```
