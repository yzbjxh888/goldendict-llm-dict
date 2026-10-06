#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import html
import io
import re
import time
import hashlib
import traceback
import urllib.request
import urllib.error
import urllib.parse


def _force_utf8():
    """强制 UTF-8 输出：优先 reconfigure（3.7+，不重建流对象），失败才回退。"""
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
            continue
        except (AttributeError, ValueError, OSError):
            pass
        try:
            buf = getattr(stream, 'buffer', None)
            if buf is not None:
                setattr(sys, name, io.TextIOWrapper(buf, encoding='utf-8', errors='replace'))
        except Exception:
            pass


_force_utf8()

# ==================== 配置大模型参数 ====================
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
API_KEY = ""  # 请填写您的 API Key
MODEL_NAME = "gemini-3.5-flash-lite"

# ---- 可调运行参数（均可用同名环境变量覆盖）----
API_TIMEOUT = int(os.environ.get('GD_LLM_TIMEOUT', '60'))        # 单次请求超时（秒）
MAX_TOKENS = int(os.environ.get('GD_LLM_MAX_TOKENS', '8192'))    # 模型最大输出 token
MAX_ATTEMPTS = int(os.environ.get('GD_LLM_ATTEMPTS', '2'))       # 总尝试次数
DEBUG = os.environ.get('GD_LLM_DEBUG', '0') not in ('0', '', 'false', 'False')


def _env_int(name, default):
    """读取整数型运行参数：缺省或非法值一律回退默认值（调参不必改代码）。"""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default

# =======================================================

# ---------- JSON 转义铁律（附加到所有可能输出反斜杠的 prompt）----------
# 背景：模型常把 LaTeX（\sum \lambda \,）或正则（\s）原样写进 JSON 字符串，
# 产生 JSON 规范不允许的转义序列，导致 json.loads 整段失败。
# 注意：此处必须是原始字符串，否则 \t \r \n 等会被 Python 解释掉。
JSON_ESCAPE_RULES = r"""

【JSON 转义铁律（最高优先级，违反会导致解析直接失败）】
1. 本响应必须是严格合法的 JSON，禁止出现 JSON 不允许的转义序列。
2. JSON 字符串中，一个"字面反斜杠"必须写成两个：`\\`。
   例：LaTeX 的 \sum 要写成 \\sum；\lambda 写成 \\lambda；\, 写成 \\,；\frac 写成 \\frac。
   例：正则的 \s 要写成 \\s；Windows 路径 C:\tmp 要写成 C:\\tmp。
3. JSON 允许的转义只有 9 种：\\ \" \/ \b \f \n \r \t \uXXXX，
   除此之外任何 `\` + 字符 的组合都非法，会让整段 JSON 无法解析。
4. 请区分两件事：上文要求的"原样保留"是指不得增删改内容的语义；
   而物理写入 JSON 时反斜杠必须翻倍，这是 JSON 语法要求，与"原样保留"并不矛盾。
"""

# ---------- 1. 英文单词专用 ----------
SYSTEM_PROMPT_EN_WORD = """你是一位英语词汇专家，服务于"桌面词典弹窗"。用户输入一个【英语单词】，读者是中文母语学习者，期望快速得到准确、分层、一眼可读的解析。严格按 JSON 输出，禁止 ```json 标记。

字段要求：
- query_type: 固定为 "english_word"
- title: 单词本身
- phonetic: 音标（斜杠包裹，如 /kənˈstrʌkt/），不确定则留空字符串
- definitions: 数组 {"pos","cn"}，至少 2 个词性；每个词性只列最常用的 1-2 个义项，按真实使用频率从高到低排列，不列生僻义项；每条 cn 不超过 15 字
- etymology_roots: 词源讲解，总长 80-150 字，只含三部分：①来源（拉丁/希腊/日耳曼等，一句）；②词根拆解（前缀/词根/后缀含义，用 → 连接）；③演变逻辑（原始义 → 现代义）与 1-2 个同源词。目标是让读者记住这个词为什么长这样，严禁堆砌学术细节
- phrases: 至少 3 个最高频、最地道的搭配，禁止生僻搭配，每个 {"phrase","cn"}
- examples: 至少 2 个双语例句，覆盖不同词性/义项；例句用词难度不超过目标词，翻译自然地道
- memory_tip: 可选，一句话记忆钩子（词源联想/画面联想/谐音），必须与真实词义严格一致，想不出可靠的则留空字符串，宁缺毋滥

输出顺序固定为：phonetic → definitions → etymology_roots → phrases → examples → memory_tip

JSON 示例（对于 "construct"）：
{
  "query_type": "english_word",
  "title": "construct",
  "phonetic": "/kənˈstrʌkt/",
  "definitions": [
    {"pos": "v.", "cn": "建造；构建"},
    {"pos": "n.", "cn": "构想；构造物"}
  ],
  "etymology_roots": "来自拉丁语 construere：con-（共同）+ struere（堆叠）→ 把东西堆到一起 → 建造。同源词：structure（结构）、destruction（反义）。",
  "phrases": [
    {"phrase": "construct a theory", "cn": "构建理论"},
    {"phrase": "well-constructed argument", "cn": "结构严谨的论证"}
  ],
  "examples": [
    {"en": "The company plans to construct a new factory.", "cn": "公司计划建一座新工厂。"},
    {"en": "This sentence is awkwardly constructed.", "cn": "这个句子结构生硬。"}
  ],
  "memory_tip": "con（一起）+ struct（堆叠）→ 把想法'堆'起来就是'构建'。"
}"""

# ---------- 2. 英文短语专用 ----------
SYSTEM_PROMPT_EN_PHRASE = """你是一位英语习语与短语专家，服务于"桌面词典弹窗"。用户输入一个【英语短语或习语】，读者是中文母语学习者，期望快速得到准确、分层、一眼可读的解析。严格按 JSON 输出，禁止 ```json 标记。

字段要求：
- query_type: 固定为 "english_phrase"
- title: 短语本身
- literal_meaning: 字面义 + 意象/典故来源，用 1-2 句解释"为什么是这个意思"（字面画面或历史典故），必须准确，不能编造来源
- meaning: 整体中文释义，不用逐词翻译
- chinese_equivalent: 最贴切的一个中文对应说法（习语/俗语优先），没有可靠对应则留空字符串
- usage: 语体标签（正式/非正式/口语/书面）+ 典型适用场景 + 一个 5-10 词的对话示例
- similar: 近义表达 1-2 个，须确为近义，无则空数组
- opposite: 反义/相对表达 1 个，无则空字符串
- common_mistakes: 常见错误用法 1-2 条（易混淆词、常见误译等），须真实常见，无则空数组
- examples: 至少 3 个双语例句，覆盖不同语境，翻译自然地道

输出顺序固定为：literal_meaning → meaning → chinese_equivalent → usage → similar → opposite → common_mistakes → examples

JSON 示例（对于 "break the ice"）：
{
  "query_type": "english_phrase",
  "title": "break the ice",
  "literal_meaning": "字面义是'打破冰层'：冰封的湖面下才有活水，破冰后才能通行，引申为打破人际间的冷漠与隔阂。",
  "meaning": "打破沉默；打破僵局",
  "chinese_equivalent": "破冰",
  "usage": "口语/中性，用于社交、会议、谈判等初次见面或气氛尴尬的场景，例如：'Go ahead, break the ice!'",
  "similar": ["get the ball rolling", "open up"],
  "opposite": "",
  "common_mistakes": ["不要误译为'打破冰块'；不要与 'break down'（崩溃、瓦解）混淆"],
  "examples": [
    {"en": "He told a joke to break the ice at the party.", "cn": "他在派对上讲了个笑话来打破沉默。"},
    {"en": "A friendly smile can break the ice between strangers.", "cn": "友好的微笑能打破陌生人之间的隔阂。"},
    {"en": "Let's play a game to break the ice.", "cn": "我们玩个游戏来活跃气氛吧。"}
  ]
}"""

# ---------- 3. 中文专用（终版：强制多学科分段） ----------
SYSTEM_PROMPT_CHINESE = """你是一位中文概念解析专家。用户输入中文词语，请按以下结构输出 JSON，禁止 ```json``` 标记。

【处理铁律】
1. 单义优先：若该词只有一个常见核心意思，只给单一定义（不加【学科】标签）。
2. 【关键】若该词在不同学科确有实质歧义，则**必须用换行分隔，并以【学科名称】作为每段开头**，例如：【物理学】...、【信息论】...。严禁将多学科内容混杂在同一段中。
3. 定义必须包含：核心特征 + 一个典型应用场景，融入定义叙述中。
4. definition_method_explanation 是独立字段，必须详细展开：采用了什么定义方法（如属加种差、功能界定、发生学等）、为什么用这个方法、具体的逻辑推导过程是什么。这是本解析的核心深度所在，绝不能敷衍或留空。

【输出字段及顺序】
{
  "query_type": "chinese",
  "title": "查询词",
  "context": "用1句话精准说明该词的主要使用领域或语体。",
  "definition": "核心定义。**如果是多学科歧义，必须用换行以【学科名称】开头分段，每段末尾紧跟“应用举例：...”**；如果是单义，直接写完整定义（含举例），不加【学科】。",
  "definition_method_explanation": "【必填，不可为空】详细的定义逻辑与依据。如果是多学科，需分别说明各学科的推导方法。",
  "summary_and_distinction": "【必填】与最相近的1-2个词语进行本质辨析（内涵范围、使用场景、褒贬色彩等）。如果是多学科，需对比各学科定义的异同。",
  "hypernym": "上位概念，没有则填空字符串。",
  "hyponyms": ["下位概念1", "下位概念2", "下位概念3"],
  "examples": [{"cn": "地道中文例句1"}, {"cn": "地道中文例句2"}]
}
"""

# ---------- 3b. 中文语句专用（科学理解为主，语言解析仅在文学类句子展开） ----------
SYSTEM_PROMPT_CHINESE_SENTENCE = r"""你是一位跨学科的句子解读专家，服务于“桌面词典弹窗”。用户输入一个【中文语句】（通常以句末标点 。？！ 结尾）。

【一句话目标】读者读完必须能做到四件事：说得出这句话的科学意思；知道句子里每个专业词指什么；明白它凭什么成立；记住别在哪儿理解错。做不到这四点的内容不要写。

【按读者的认知顺序组织】先定位领域 → 再讲清在说什么 → 把专业概念落地 → 看清论断与依据 → 最后点出易错点。

【第一步：判断句子类型】
- 学科定义/说明/论断句（如“X 是……的模型”“X 指……”“X 由……构成”）→ 走科学理解路线：meaning、structure 写满，rhetoric 从简，rewrite 只给“一句话读懂”。
- 文学、抒情、标语、习语、口语调侃句 → 走语言视角：style 记语体，structure 可拆表达结构，rhetoric 展开，rewrite 给 1-2 条不同风格的改写。

【字段要求（每个字段就是一个读者会问的问题）】
- query_type: 固定为 "chinese_sentence"
- title: 原句（与用户输入一致，不得改写；过长时可截取主干但须保持原标点）
- style: 📍【读者问：这句话是哪个学科的话？】一句话给出学科/领域，再补语体与常见出现场合（教材/论文/科普/日常）。
- meaning: 💡【读者问：这句话到底在说什么？】主力字段。用白话把句子的科学内容讲透，3-5 句，不要只做同义复述：句中出现的专业概念要当场讲明白——首次出现时给出准确定义或一条关键性质（例如它的表达式、研究对象、典型结论），最后可点一句“因此它重要在哪”。
- structure: 🧩【读者问：它凭什么这么说？这些内容之间是什么关系？】主力字段，2-4 条，只拆科学内容，按需覆盖：① 这句话在定义/断言什么；② 靠什么机理或依据成立（公式、原理、实验或提出者）；③ 成立或适用的前提条件；④ 由此能推出什么、与哪些已知知识相连。
  ⛔ 严禁语法成分拆解（主语、定语、状语、虚词、语气词、复句层次、关联词作用）。
- rhetoric: 🎨【读者问：这句话用了修辞吗？】科学句子如实写“平实客观的学术陈述，无修辞手法”，最多补一句表述特点（如“简单”与“复杂”的对比）。只有文学类句子才展开修辞与语气分析。
- rewrite: ✍️【读者问：能用一句话让我记住吗？】学科说明句固定只给 1 条 {"version": "一句话读懂", "text": "……"}；文学类句子改回 1-2 条 {"version": "改写角度（更书面/更口语/更简洁）", "text": "改写后的句子"}。
- usage_tips: 📌【读者问：哪里容易理解错？】最常被误解的术语或表述 + 1-3 个相关概念/出处；没有就填空字符串。

【公式书写】句中出现公式时用 Unicode 纯文本，如 xₙ₊₁ = r·xₙ(1−xₙ)、d²y/dx²、E = mc²；不要写 $...$、\frac、\partial 等 LaTeX 记号（本弹窗不渲染 LaTeX）。

【禁止】① 只做同义改写而不解释专业概念；② 提到专业术语却不解释（如光说“非线性动力系统”却不说明它是什么）；③ 空话套话（“体现了……的思想”“结构清晰、信息密度高”）；④ 编造原句没有的结论、数据或出处，不确定就不写。

输出顺序固定为：style → meaning → structure → rhetoric → rewrite → usage_tips

JSON 示例（对于“逻辑斯谛映射是数学中描述非线性动力系统的经典模型，又称抛物线映射，通过简单的二次函数形式展现复杂动力学行为。”）：
{
  "query_type": "chinese_sentence",
  "title": "逻辑斯谛映射是数学中描述非线性动力系统的经典模型，又称抛物线映射，通过简单的二次函数形式展现复杂动力学行为。",
  "style": "非线性动力学 / 混沌理论领域的学术说明句，语体客观严谨，常见于教材、百科与科普文章的概念引入段落。",
  "meaning": "这句话给“逻辑斯谛映射”下定义：它是非线性动力系统研究里的一个经典模型，英文名 logistic map，别称抛物线映射。它的形式很简单，就是一个二次函数 xₙ₊₁ = r·xₙ(1−xₙ)，含义是“把上一步的结果代回去、按同一条规则反复计算”。但正因如此，参数 r 变化时迭代结果会从收敛到固定值，变为周期振荡，再进入看似随机的混沌状态——所以它重要在：它是“简单规则产生复杂行为”最著名的例证，也是混沌理论的入门模型。",
  "structure": [
    "定义了什么：逻辑斯谛映射首先是一个映射（迭代函数），刻画的是离散时间下某个量如何一步步演化，属于非线性动力系统模型。",
    "靠什么成立：机理是非线性加迭代——函数是二次的（图像为抛物线，故称抛物线映射），并把上一次的输出作为下一次的输入反复代入。",
    "别称的由来：“抛物线映射”得名于其函数图像是抛物线，并非指某种几何变换或一对一对应。",
    "能推出什么：随参数 r 增大，系统会经历倍周期分岔（周期 1→2→4→8…）并最终进入混沌区；它最初来自种群数量变化的生态模型。"
  ],
  "rhetoric": "平实客观的学术陈述，无修辞手法；表述上用“简单”与“复杂”形成对比，以突出结论的反常性。",
  "rewrite": [
    {"version": "一句话读懂", "text": "逻辑斯谛映射就是一个简单的二次函数 xₙ₊₁ = r·xₙ(1−xₙ)，反复迭代后会表现出极其复杂的混沌行为，因此成为非线性动力学与混沌理论的经典范例。"}
  ],
  "usage_tips": "易误解：把“映射”当作“一对一对应”或“翻译”，这里指迭代函数；“抛物线映射”指函数图像为抛物线。相关概念：混沌、倍周期分岔、动力系统、种群增长模型。"
}"""

# ---------- 3c. 中文疑问句专用（答疑：先给答案，再讲依据） ----------
SYSTEM_PROMPT_CHINESE_QUESTION = r"""你是一位中文答疑专家，服务于“桌面词典弹窗”。用户输入一个【中文疑问句】（以全角 ？ 结尾）。

【一句话目标】读者读完必须能：立刻拿到答案 → 搞懂句里的专业词 → 知道答案凭什么成立 → 知道容易错在哪 → 知道还能追问什么。做不到这几点的内容不要写。

【第一步：判断问句类型】
- fact（事实/概念问句：是什么、指什么、有什么区别）→ 先给结论，再给概念定义与边界。
- causal（因果/机制问句：为什么、怎么形成、如何工作）→ 先给结论，再给机理链条（原因 → 过程 → 结果）。
- howto（方法/操作问句：怎么做、如何配置、怎样避免）→ 先给结论，再给步骤与前提条件。
- choice（选择/比较问句：A 还是 B、哪个更好）→ 先给结论，再给对比维度与适用情形。
- rhetorical（反问/设问）→ 先点明句子的真实意图，再作答。

【字段要求（每个字段就是一个读者会问的问题）】
- query_type: 固定为 "chinese_question"
- title: 原问句（与用户输入一致，不得改写；过长时可截取主干但须保留句末 ？）
- subtype: 从 fact / causal / howto / choice / rhetorical 中选一个
- direct_answer: ✅【读者问：答案到底是什么？】主力字段，必须提供。用 1-3 句直接回答问题，放在最前；答案取决于条件时先说一般情形再说例外；确实无法确定就说明缺什么信息，禁止编造
- key_concepts: 📚【读者问：里面的专业词是什么意思？】问句与答案中出现的专业概念逐个落地，每条 {"term","explanation"}；explanation 给准确定义或一条关键性质。无专业概念则空数组
- reasoning: 🔗【读者问：凭什么这么答？】答案的依据与推导 2-4 条，按需覆盖：① 靠什么原理/定义/数据成立；② 成立的前提或适用范围；③ 常见的反例或边界情形
- caveats: ⚠️【读者问：哪里最容易答错或理解错？】1-3 条，须真实常见；没有则空字符串
- followups: ➡️【读者问：还能顺着问什么？】2-3 个自然的追问方向，每条一句话，须与原问题相关
- style: 📍 一句话说明该问句所属领域与语体（学科/技术/日常/考试）

【公式书写】需要写公式时用 Unicode 纯文本，如 xₙ₊₁ = r·xₙ(1−xₙ)、E = mc²；不要写 $...$、\frac、\partial 等 LaTeX 记号（本弹窗不渲染 LaTeX）。

【禁止】① 只解释句子语法而不回答问题；② 提到术语却不解释；③ 空话套话（“这个问题很有意义”“需要具体分析”）；④ 编造数据、结论或出处，不确定就写明不确定。

输出顺序固定为：direct_answer → key_concepts → reasoning → caveats → followups → style

JSON 示例（对于“为什么天空是蓝色的？”）：
{
  "query_type": "chinese_question",
  "title": "为什么天空是蓝色的？",
  "subtype": "causal",
  "direct_answer": "因为阳光被大气中的分子散射，而波长短的蓝光比红光散射得更强，所以从各个方向进入眼睛的散射光以蓝光为主，天空呈现蓝色。",
  "key_concepts": [
    {"term": "瑞利散射", "explanation": "散射粒子（如氮气、氧气分子）远小于光波长时的散射，散射强度与波长的四次方成反比，因此短波蓝光被强烈散射"},
    {"term": "波长", "explanation": "光在一个振动周期内传播的距离，蓝光约 450 nm、红光约 700 nm"}
  ],
  "reasoning": [
    "机理：太阳光进入大气后与气体分子发生瑞利散射，散射强度 ∝ 1/λ⁴，蓝光（λ 小）的散射强度约为红光的 5-6 倍。",
    "结果：被散射的蓝光从天空各个方向进入人眼，而直射光中蓝光被削弱，所以正午天空呈蓝色、日出日落时直射光偏红。",
    "前提：大气洁净、粒子尺度远小于可见光波长；若水汽或尘埃颗粒较大（如雾霾），则转为米氏散射，天空偏白灰。"
  ],
  "caveats": "易误解：天空蓝不是因为大气“本身是蓝色”或反射了海水颜色；海水的蓝是水对红光的吸收所致，成因不同。",
  "followups": ["为什么日出日落时天空是红色的？", "阴天时天空为什么是灰白色的？", "天空在火星上是什么颜色、为什么？"],
  "style": "大气物理 / 光学领域的常识性因果问句，语体中立，常见于科普问答与中学物理教学。"
}""" + JSON_ESCAPE_RULES

# ---------- 3d. 中文段落专用（多句长文本：先给主旨，再逐句拆） ----------
SYSTEM_PROMPT_CHINESE_PARAGRAPH = r"""你是一位中文段落解析专家，服务于“桌面词典弹窗”。用户输入一段【中文段落】（含两个及以上句末标点，长度超过单句上限）。

【一句话目标】读者读完必须能：一句话说出这段讲什么 → 知道每句各承担什么 → 看清句子之间怎么组织 → 查清里面的专业词 → 记住要点与易错处。做不到这几点的内容不要写。

【处理铁律】
1. 先整体后局部：gist 必须是独立可读的主旨，不能写成"本段讨论了……"这类空壳句。
2. sentence_map 按"读者会分几次看"来切：一句一条；过长的句子可以拆成两条，但总数不超过 6 条；不要逐字复述原句，写它承担的功能与要点。
3. 若段落中含疑问句（有 ？），必须在 takeaways 中明确给出该问的答案 —— 这是本解析的硬性要求，不得略过。
4. 严禁编造原段落没有的结论、数据或出处；不确定就写明不确定。

【字段要求（每个字段就是一个读者会问的问题）】
- query_type: 固定为 "chinese_paragraph"
- title: 段落首句（与用户输入首句一致，不得改写；过长可截取主干但须保持原标点）
- subtype: 从 expository（说明/介绍）/ argumentative（论证/推理）/ narrative（叙事/过程）/ mixed（混合）中选一个
- gist: 📌【读者问：这段到底讲什么？】主力字段。1-3 句给出主旨与结论，独立可读
- sentence_map: 🧩【读者问：每句各在干什么？】主力字段，3-6 条，每条 {"sentence","point"}：sentence 摘录原句（可截取主干），point 写该句承担的功能与要点（一句话）
- logic: 🔗【读者问：这些句子之间是什么关系？】2-4 条，说明句间结构：总分 / 递进 / 转折 / 因果 / 举例 / 并列 / 问题—解答 等，并指出段落的推进线索
- terms: 📚【读者问：里面的专业词是什么意思？】段落中出现的专业概念逐个落地，每条 {"term","explanation"}；explanation 给准确定义或一条关键性质；无专业概念则空数组
- takeaways: ✅【读者问：我该记住什么、哪里容易理解错？】3 条以内要点；若段落含疑问句，必须另起一条给出该问的答案；再补 1-3 个常见误解或适用边界；没有则只写要点
- style: 📍 一句话说明该段落所属领域与语体（教材/论文/科普/新闻/日常）

【公式书写】需要写公式时用 Unicode 纯文本，如 xₙ₊₁ = r·xₙ(1−xₙ)、E = mc²；不要写 $...$、\frac、\partial 等 LaTeX 记号（本弹窗不渲染 LaTeX）。

【禁止】① gist 写成空壳复述（“本段主要介绍了……”）；② 只罗列句子却不解释；③ 提到术语却不解释；④ 编造数据、结论或出处；⑤ 输出超过 6 条 sentence_map。

输出顺序固定为：gist → sentence_map → logic → terms → takeaways → style

JSON 示例（对于“逻辑斯谛映射是数学中描述非线性动力系统的经典模型。它的形式很简单，就是一个二次函数 xₙ₊₁ = r·xₙ(1−xₙ)。正因如此，参数 r 变化时迭代结果会从收敛变为周期振荡，再进入混沌状态。”）：
{
  "query_type": "chinese_paragraph",
  "title": "逻辑斯谛映射是数学中描述非线性动力系统的经典模型。",
  "subtype": "expository",
  "gist": "这段用逻辑斯谛映射说明“简单规则能产生复杂行为”：它是一个二次函数的迭代模型，随参数变化会依次出现收敛、周期振荡与混沌三种典型状态。",
  "sentence_map": [
    {"sentence": "逻辑斯谛映射是数学中描述非线性动力系统的经典模型。", "point": "给出对象与定位：它是非线性动力系统里的经典模型"},
    {"sentence": "它的形式很简单，就是一个二次函数 xₙ₊₁ = r·xₙ(1−xₙ)。", "point": "给出形式：迭代规则是二次函数，把上一步结果代回自身"},
    {"sentence": "参数 r 变化时迭代结果会从收敛变为周期振荡，再进入混沌状态。", "point": "给出行为：随 r 增大依次出现收敛、倍周期振荡、混沌"}
  ],
  "logic": [
    "结构是“对象 → 形式 → 行为”的递进：先定位，再给式子，最后说行为。",
    "第二句与第三句构成因果：正因为形式如此简单（非线性 + 迭代），才会有第三句那种随参数变化的丰富行为。"
  ],
  "terms": [
    {"term": "逻辑斯谛映射", "explanation": "离散时间的迭代函数模型，形式为 xₙ₊₁ = r·xₙ(1−xₙ)，别称抛物线映射，是混沌理论的入门模型"},
    {"term": "混沌", "explanation": "确定性系统在参数变化后表现出的对初值极端敏感、看似随机的长期行为"}
  ],
  "takeaways": ["记住一句话：简单二次函数反复迭代就能产生混沌。", "易误解：这里的“映射”指迭代函数，不是“一对一对应”或“翻译”。", "边界：上述收敛—周期—混沌的转变依赖参数 r 的取值范围，并非所有参数下都出现混沌。"],
  "style": "非线性动力学 / 混沌理论的科普性说明段落，语体客观严谨，常见于教材概念引入与百科条目。"
}""" + JSON_ESCAPE_RULES

# ---------- 4. 通用（代码/命令）专用 ----------
SYSTEM_PROMPT_GENERAL = r"""你是一位技术讲解专家，服务于"桌面词典弹窗"。用户输入的是【代码片段、命令行、技术名词或技术相关内容】，读者是中文开发者（含初学者），期望快速得到准确、分层、可读的解析。严格按 JSON 输出，禁止 ```json 标记。

字段要求：
- query_type: 固定为 "general"
- title: 查询主体
- summary: 1-2 句通俗核心解释，必须提供
- key_points: 至少 3 个关键点，必须满足：
  - 第一条必须先给一句话结论（TL;DR）；
  - 每点控制在 3 句以内；
  - 对每个技术术语给出【缩写全称 → 英文全称 → 中文释义】并解释作用；
  - 从"小白"视角说明为什么这样做、原理是什么
- pitfalls: 常见错误/坑 1-3 条，须真实常见，无则空数组
- examples: 至少 2 个用法示例，每个 {"en":"代码/命令内容","cn":"逐行注释/解释"}，代码可直接复制；若用户输入本身就是代码/命令，第一条务必原样回显用户输入

输出顺序固定为：summary → key_points → pitfalls → examples

JSON 示例（对于 "git commit -m"）：
{
  "query_type": "general",
  "title": "git commit -m",
  "summary": "git commit 将暂存区更改提交到本地仓库，-m 允许直接附带提交信息，避免打开编辑器。",
  "key_points": [
    "一句话结论：commit 是'存档'，-m 是'把存档说明写在命令里'。",
    "git → Git（分布式版本控制系统），commit → 提交（记录一次变更快照），-m → message（提交信息）。",
    "提交信息用祈使句简要描述变更目的，如 'Fix login bug'。",
    "省略 -m 会启动默认编辑器（如 Vim）输入多行提交信息。"
  ],
  "pitfalls": ["-m 后不加引号且信息含空格会被拆成多个参数；新仓库未 git add 时 commit 会报 'nothing to commit'"],
  "examples": [
    {"en": "git commit -m \"Initial commit\"", "cn": "提交并写入初始提交信息"},
    {"en": "git commit -m \"Fix login bug\" -m \"Also update tests\"", "cn": "多个 -m 拼出多段提交信息"}
  ]
}""" + JSON_ESCAPE_RULES

# ---------- 5. LaTeX 数学公式专用 ----------
SYSTEM_PROMPT_LATEX = r"""你是一位 LaTeX 数学公式解析专家，服务于"桌面词典弹窗"。用户输入一个【LaTeX 数学公式】（可能是单行公式、多行推导、矩阵/方程组环境），读者是中文学习者或科研人员，期望在几秒内看懂这个式子。严格按 JSON 输出，禁止 ```json 标记。

【总原则】
- 字段顺序就是读者的认知顺序：怎么念 → 叫什么/哪一类 → 整体什么意思 → 拆开看结构 → 查生词 → 什么条件下成立 → 用在哪 → 怎么用 → 还能连到什么。
- 一个字段只回答一个问题；没有内容的字段留空（空字符串或空数组），不要为凑格式编造。
- 每条一句话、信息密度优先。**每条都必须含一个具体信息**（具体量、具体条件、具体数值、具体公式或具体名称），禁止"体现了……的关系""是……的重要工具""在……中有着广泛应用"这类不涉及任何具体信息的表述。
- 拿不准就说拿不准：式子疑似有误、条件记不清、名称不确定，都明写"不确定""一说……"；**宁可某个字段留空，也不要写出可能错的内容**。
- 术语首次出现时给出中英文对照（如"特征值（eigenvalue）"），方便读者检索文献。
- 正文说明里可直接写 LaTeX 片段（如 `\frac{1}{2}`、`\partial`），弹窗会渲染成排版公式；**但不要加 `$` 定界符**（只有 title 需要原样保留 `$`）。
- 若输入是单个符号或记号（如 `\alpha`、`\to`），说清读法、名称与含义即可，structure 留空数组，usage 一般也留空。
- 若输入是多行推导 / 方程组 / 矩阵，把它当整体讲：meaning 说清这一步在算什么，structure 按行或按意群拆。

字段要求：
- query_type: 固定为 "latex"
- title: 公式原文，原样保留用户输入的完整 LaTeX（每个反斜杠、花括号、下标上标、空格、句点、尖括号与竖线都不增删改；输入带 `$` 或 `$$` 包裹时连 `$` 一起保留）
- read_as: 怎么念。① 有通行名称的先说名称（如"欧拉公式""链式法则"）；② 再把式子读成中文口语，让读者能逐字照着念（`\frac{\partial f}{\partial x}` → "f 对 x 的偏导数"，`\int_0^\infty` → "从 0 到无穷的积分"），念法要能对应回原式，不要意译；③ 式子较长时按阅读顺序分段（用分号连接）。**不写英文读法**
- kind: 叫什么、属于哪一类、出自哪个分支。写成"名称（类别）· 分支"或"类别 · 分支"；类别取一到两个词：定义式 / 定律·守恒律 / 恒等式 / 近似式 / 变换关系 / 泛函·变分 / 方程 / 不等式 / 记号约定。没有公认名称就不要硬起名字，只写类别与分支
- meaning: 【主力字段】整体含义 3-5 句，按序覆盖：① 一句话结论：它断言 / 定义 / 计算了什么；② 直觉：几何或物理图像（斜率、面积、守恒量、投影、似然等），必要时说明量纲或量级；③ 主导项与极限行为：哪个量决定量级或趋势，关键量取 0 / 1 / ∞ 或某特殊值时式子退化成什么；④ 容易看漏的隐含前提或反直觉之处（没有就省略）。若式子疑似有笔误（量纲不符、指标不匹配），在末尾指出最可能的正确写法并说明依据，不确定就明说不确定
- structure: 【主力字段】按意群拆解，2-5 条，每条 {"part","role","note"}：part 是公式中对应的 LaTeX 片段（原样保留，不带 `$`）；role 是它的角色（左端 / 被积函数 / 权重 / 指标 / 边界项 / 归一化因子 等）；note 用一句中文说它在这里干什么，**并补一句"为什么需要它"或"去掉 / 改动会怎样"**（如"分母保证归一化，去掉后右端不再是概率"）。粒度按"读者会分几次看"来定，不要一个符号拆一条；**各条 part 合起来要能覆盖原式的主体**（别只拆一半、漏掉主要项），长式按主要意群拆
- symbols: 兜底词典。只列 structure 没讲过、读者可能不认识的符号，每条 {"symbol","explanation"}；symbol 原样保留 LaTeX 片段（不带 `$`），explanation 给中文名称 + 含义 + **类型与约定**（标量 / 向量 / 矩阵 / 算符 / 随机变量 / 张量、取值范围、单位）；存在约定分歧的要指出（如"部分教材用该符号表示共轭转置"）。**不得与 structure 重复**；没有则空数组
- conditions: 成立条件与适用范围。一到三句，用分号分隔，覆盖：① 数学前提（可导 / 收敛 / 可逆 / 独立同分布等）；② 记号或约定前提（指标范围、爱因斯坦求和、坐标系）；③ **条件被违反时会怎样**（失效、退化为近似、需要加修正项）。确实无条件可写则留空字符串
- application: 典型应用场景 1-3 条，每条一句话，写明"学科 + 具体用途 + 它出现在哪个定理 / 方法 / 课程里"（如"统计推断：贝叶斯更新中把先验与似然合成为后验"）
- usage: 🧮 怎么用 1-3 条，每条一句话，按操作顺序回答"拿到这个式子该怎么做"：① 谁是待求量（已知什么、求什么）；② 具体动作（先算哪一部分、把什么代进去、要不要移项 / 取对数 / 展开到一阶 / 归一化 / 换元）；③ 结果怎么读、怎么自查（算出来是什么量，用哪个特例或量纲能立刻验证）。**不要复述 application**（那讲"用在哪"，这里讲"怎么用"）；能顺手给出可核对结论的附在句末（如"代 x = 0 得 0，可先拿它自查一遍"）。定义式 / 纯记号类式子无从"使用"的 → 留空数组，不要硬凑
- related: 🔗 相关与延伸 2-4 条，每条一句话，写成"关系 + 具体对象（名称或公式）+ 一句它和本式的关系"。关系取这几类：**等价变形 / 特例 / 推广**；**上游由什么推出、下游用来推什么**；**常一起配套使用的公式**；**形近易混公式的辨析**（本式是……而 X 是……，区别在……）；有名字的式子可补一句命名由来。禁止"参见微积分""属于线性代数"这类只给学科名、无法核对的条目；不确定就留空数组

输出顺序固定为：read_as → kind → meaning → structure → symbols → conditions → application → usage → related

JSON 示例（对于 "\frac{\partial f}{\partial x} = 2x"）：
{
  "query_type": "latex",
  "title": "\\frac{\\partial f}{\\partial x} = 2x",
  "read_as": "f 对 x 的偏导数等于 2x",
  "kind": "方程（微分关系）· 一元微积分",
  "meaning": "它断言 f 沿 x 方向的变化率恰好等于 2x，是一个把函数与其导数联系起来的微分方程。几何上，f 的图像在每一点的切线斜率等于该点横坐标的两倍，所以曲线越往右越陡。主导项是右端的线性项：x 越大变化越快，x = 0 时斜率为 0，式子退化为 0 = 0。容易看漏的是 ∂ 记号意味着 f 可能是多元函数，其余自变量在这里被当作常数。",
  "structure": [
    {"part": "\\frac{\\partial f}{\\partial x}", "role": "左端", "note": "f 对 x 的偏导数；用 ∂ 而非 d 表明 f 是多元函数，其余自变量在此固定不动"},
    {"part": "=", "role": "等号", "note": "不是恒等变形而是条件方程：它限定 f 必须满足的微分关系，解出来是一族函数"},
    {"part": "2x", "role": "右端", "note": "变化率的取值，随 x 线性增长；若改成常数 k，解就从二次函数退化为一次函数"}
  ],
  "symbols": [
    {"symbol": "\\partial", "explanation": "偏导数符号（partial derivative）：多元函数中只对某一个自变量求导，其余变量视为常数"}
  ],
  "conditions": "f 对 x 可微（偏导数存在）；默认在实数域的某个区间上讨论；若 f 在某点不可微（有尖点），式子在该点无意义，需改用单侧导数或弱导数",
  "application": ["微积分：由导数反求原函数，积分得 f = x² + C，是求解微分方程最简单的例子", "物理：已知速度 v = 2x 时，由位置—速度的微分关系反推运动规律"],
  "usage": ["先明确待求量：已知 x 求 f 沿 x 的变化率，把右端 2x 直接当作斜率读出", "要求 f 本身时，对两边积分一次得 f = x² + C，常数项 C 不能丢", "自查：代 x = 0 应得斜率 0，若算出非零，说明求导或积分哪一步算错了"],
  "related": ["积分一次即得通解 f = x² + C", "特例：右端改成常数 k 时退化为 \\frac{df}{dx} = k，解为一次函数", "形近辨析：写成 \\frac{df}{dx} = 2x 时 f 被当作一元函数，本式用 ∂ 说明 f 是多元函数、其余自变量一律当常数"]
}""" + JSON_ESCAPE_RULES

# ---------- 6. 代码块专用 ----------
SYSTEM_PROMPT_CODE = r"""你是一位代码解析专家，服务于"桌面词典弹窗"。用户输入一段【多行代码或命令块】，读者是中文开发者（含初学者），期望快速理解这段代码的功能与用法。严格按 JSON 输出，禁止 ```json 标记。

【处理铁律】
1. 代码原文必须完整保留在 code 字段：原样逐字输出（含中文注释、中文字符串与缩进换行），不得改写、省略或翻译代码本身。
2. 先整体、后局部：先说明这段代码整体在做什么，再逐段解释，禁止一上来就扎进细节。
3. explanation 的 part 必须从 code 原文中摘录，与 code 字段保持一致；note 用中文解释。
4. 中文注释/中文字符串保留原样，只在 note 中解释其含义，不在代码里做任何改动。

【输出字段及顺序】（由整体到局部、由原理到风险）
{
  "query_type": "code",
  "title": "一句话定位：这段代码的用途（如「用 Python 计算列表平均值」），不含代码本身",
  "code": "用户代码原文，逐字保留，必须提供",
  "summary": "1-2 句通俗解释代码整体在做什么，必须提供",
  "language": "编程语言/环境（python/shell/sql/javascript 等），不确定则空字符串",
  "explanation": [{"part": "从 code 原文摘录的代码片段", "note": "中文解释"}],
  "key_points": ["至少 2 个关键点，从小白视角说明原理"],
  "pitfalls": ["常见错误/坑 1-3 条，无则空数组"]
}
""" + JSON_ESCAPE_RULES

# =======================================================
# 统一分类器（合并原 should_query 与 detect_and_route）
# =======================================================

# 常见文件扩展名（用于拦截纯文件名）
FILE_EXTENSIONS = (
    'exe|dll|so|dylib|bin|dat|log|txt|tmp|pyc|o|obj|class|jar|war|ear|zip|rar|7z|'
    'gz|bz2|xz|tar|iso|img|vmdk|vhd|qcow2|deb|rpm|msi|apk|ipa|dmg|pkg|com|bat|cmd|'
    'ps1|sh|bash|zsh|fish|py|js|html|css|json|xml|cfg|ini|conf|yml|yaml|toml|lock|'
    'jpg|jpeg|png|gif|webp|bmp|svg|ico|tif|tiff|avif|heic|raw|'
    'pdf|doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|rtf|md|tex|epub|mobi|'
    'mp3|mp4|avi|mkv|mov|wmv|flac|wav|ogg|webm|m4a|'
    'csv|tsv|db|sqlite|sqlite3|bak|orig|swp|part|min|map'
)
FILE_EXT_RE = re.compile(r'\.(' + FILE_EXTENSIONS + r')$', re.I)

# 强代码符号：自然语言中几乎不出现的符号。
# % & ( ) / * @ # + $ 等已从全局判定移除，改由上下文规则判定，避免误伤
# "100%"、"a & b"、"R&D"、"e.g."、"#tag"、"$5" 等自然语言/价格/缩写。
STRONG_CODE_SYMBOLS_RE = re.compile(r'[=;{}[\]<>|\\`~^_]')

# "中文自然句让位判定"专用的强符号集：刻意不含方括号。
# 理由：arr[0]、[x for x in y] 等真代码依赖 [ ]，故代码判定保留方括号；
# 但中文学术句里的 [2]、[4-6] 是引注（见 CITATION_REF_RE），不能当作代码索引。
CODE_SIGNAL_STRICT_RE = re.compile(r'[=;{}<>|\\`~^_]')

# ---- LaTeX 数学公式守卫特征 ----
LATEX_DOLLAR_RE = re.compile(r'\$([^$\n]+)\$')        # $...$ 或 $$...$$（非空、单行）
LATEX_PAREN_RE = re.compile(r'\\[\(\[\]\)]')          # \(...\) \[...\]
LATEX_BEGIN_RE = re.compile(r'\\begin\s*\{[A-Za-z*]+\}')  # \begin{env}
LATEX_COMMAND_RE = re.compile(r'\\(?:[A-Za-z]{2,})(?![a-z\\])')  # \frac \alpha 等；排除 \n\t\r 单字母转义
BACKSLASH_PATH_RE = re.compile(r'^\\{1,2}[A-Za-z0-9._-]+(?:\\[A-Za-z0-9._-]+)+$')  # \usr\bin\file.txt / \\server\share 反斜杠路径
PRICE_RE = re.compile(r'^[$¥€£]\s?\d[\d,.]*$|^\d[\d,.]*\s?[$¥€£]$')  # 货币/价格，不视为 LaTeX

# ---- 英文单词：撇号/连字符必须夹在字母之间（排除 'abc、abc-、a''b、--help） ----
WORD_RE = re.compile(r"^[A-Za-z]+(?:['-][A-Za-z]+)*$")
CAMELCASE_RE = re.compile(r'^[a-z]+(?:[A-Z][a-z0-9]*){2,}$')  # getElementById → 代码标识符
FAKE_WORD_RE = re.compile(r'^(.)\1{3,}$|^[^aeiouyAEIOUY]{13,}$')  # aaaa... / 无元音超长串
WORD_MAX_LEN = 15  # 英文单词上限（用户要求，化学名/长术语将被拦截）

# ---- 英文短语：空格分隔的纯字母词 ----
PHRASE_RE = re.compile(r'^[A-Za-z]+(?: [A-Za-z]+)+$')
PHRASE_MAX_WORDS = 5
# 完整英文句子（含句末标点的多词串）→ 拦截
EN_SENTENCE_RE = re.compile(r'^[A-Za-z]+(?: [A-Za-z]+)+[.?!]$')

# ---- 中文句末标点 → 路由"中文语句"（不再静默拦截） ----
# 疑问句信号：仅认全角 ？(U+FF1F)，且必须位于结尾（允许尾随空白）。
# 半角 ? (U+003F) 完全不参与判定：英文问句与代码里的 ? 不进中文答疑路由。
CN_QUESTION_END_RE = re.compile(r'？\s*$')
# 句解信号：句末标点。已移除 ？—— 全角问号结尾归 chinese_question 答疑路由，
# 否则两条路由会抢同一输入，新路由形同虚设。
CHINESE_SENTENCE_PUNCT_RE = re.compile(r'[。！…]')
# 结尾型分句末标点（新增 ；）：以 。；！… 结尾也判句解
CN_SENT_END_PUNCT_RE = re.compile(r'[。；！…]\s*$')
# 句号收尾：完整句的最强信号，直接判句解，不受 CN_SENTENCE_MIN_LEN 约束
# （否则「你好。」这类 2 字短句会被长度下限挡成术语）
CN_END_FULL_STOP_RE = re.compile(r'。\s*$')
# 结尾白名单：句解与段落都只认"以这些标点收尾"的输入。
# 1 = 只认结尾标点（默认）：划选的半截文本（逗号、顿号或无标点收尾）不再触发
#     句解与段落路由，落进术语档被限长拦掉 → 不发起 API 请求，用于控制调用成本。
# 0 = 旧行为（含 。！… 即判句解）。
CN_PUNCT_END_ONLY = _env_int('GD_LLM_CN_PUNCT_END_ONLY', 1)
# 走句解的最小有效字符：仅约束"结尾判定"这条新路径（防「甲；」被判成句子）
CN_SENTENCE_MIN_LEN = _env_int('GD_LLM_CN_SENT_MIN', 4)
# 段落信号：句末标点计数（含 ？，便于统计整段的句子数）
CN_SENT_PUNCT_COUNT_RE = re.compile(r'[。！？…]')
# 至少几个句末标点才允许升格为段落（默认 1 = "任何以句号类收尾、且超过句解
# 上限的中文长文本都按段落处理"）。取 1 可覆盖"只有一个句号的长单句"
# （教材里长达百余字的长定义句），取 2 则这类文本维持拦截。
# 两种取值都不影响 ≤120 的输入，故既有用例零回归。
CN_PARA_MIN_PUNCT = _env_int('GD_LLM_CN_PARA_MIN_PUNCT', 1)
# LaTeX 混合文本中"中文说明句"的强信号（含这些标点说明主体是自然语言而非公式）
CHINESE_STRONG_SENTENCE_RE = re.compile(r'[。？！，；]')

# 学术引注上标：[2] [4-6] [1,3] [1,3-5] ［2］ [图3] [表2]
# 中文学术句常见，属"标注"而非代码索引 → 在中文自然句判定中先剔除再找代码痕迹
CITATION_REF_RE = re.compile(
    r'(?:\[|［)\s*(?:\d+(?:\s*[-,–~]\s*\d+)*|[图表注式附]\s*\d+(?:\s*[-–]\s*\d+)?)\s*(?:\]|］)'
)

# ---- 存在性判定（统一在特征层提取，避免 re.search 散落在决策分支里） ----
CN_CHAR_RE = re.compile(r'[\u4e00-\u9fff]')
EN_CHAR_RE = re.compile(r'[A-Za-z]')

# ---- 长度统一阈值（有效字符 = 中英文字母总数；标点/符号不计入，由特征层统一计算） ----
# 均可用 GD_LLM_* 环境变量覆盖，调参不必改代码
CHINESE_PURE_MAX_LEN = _env_int('GD_LLM_CN_PURE_MAX', 20)        # 纯中文术语上限
CHINESE_MIXED_MAX_LEN = _env_int('GD_LLM_CN_MIXED_MAX', 30)      # 中英夹杂术语上限（符号不计入有效字符）
CHINESE_SENTENCE_MAX_LEN = _env_int('GD_LLM_CN_SENT_MAX', 120)   # 中文语句上限
CHINESE_QUESTION_MAX_LEN = _env_int('GD_LLM_CN_Q_MAX', 120)      # 中文疑问句上限（疑问通常更长，可单独放宽）
CHINESE_PARAGRAPH_MAX_LEN = _env_int('GD_LLM_CN_PARA_MAX', 600)  # 中文段落上限（约 3-8 句；长于此仍静默拦截）
CODE_MAX_LEN = _env_int('GD_LLM_CODE_MAX', 80)           # 单行代码/命令上限
LONG_TEXT_CODE_LEN = _env_int('GD_LLM_LONG_CODE_LEN', 80)  # 长文本代码优先阈值（与单行代码上限衔接）
CODE_BLOCK_MAX_LEN = _env_int('GD_LLM_CODE_BLOCK_MAX', 1000)  # 多行代码块 / 任意文本的绝对上限
MIXED_MAX_LEN = _env_int('GD_LLM_MIXED_MAX', 16)        # 非中文未知混合串的兜底上限
CN_DOMINANT_MIN = _env_int('GD_LLM_CN_DOMINANT_MIN', 6)  # 中文主导判定的汉字数阈值（原为硬编码 6）

# ---- 多行代码块特征（含中文注释的代码不被中文分支误拦） ----
CODE_BLOCK_KEYWORDS_RE = re.compile(
    r'\b(def|class|import|from|function|var|const|let|return|if|elif|else|for|while|'
    r'do|switch|case|break|continue|try|except|finally|public|private|protected|static|'
    r'void|int|float|double|char|bool|struct|enum|namespace|using|package|fn|match|'
    r'async|await|yield|lambda|with|raise|pass|print|include|define)\b'
)

# 多行兜底：多行文本含保守代码符号（自然语言几乎不出现）→ 路由代码块。
# 刻意排除 ;（英文句子可出现分号）与 () <>（自然语言括号/比较），避免误伤段落。
MULTILINE_CODE_SYM_RE = re.compile(r'=|\{|\}|\[|\]|->|=>|::|//|^\s*#', re.M)

# ---- 强 CLI 命令词：无符号命令（如 git push）路由到通用解析。
# 刻意避开 break/make/go/tar/sed 之外的常见英语词，避免与短语识别冲突 ----
CLI_COMMAND_WORDS = {
    'git', 'sudo', 'npm', 'npx', 'yarn', 'pip', 'pip3', 'pipenv', 'poetry', 'conda',
    'docker', 'kubectl', 'minikube', 'helm', 'ssh', 'scp', 'rsync', 'curl', 'wget',
    'apt', 'apt-get', 'yum', 'dnf', 'brew', 'cargo', 'rustc', 'docker-compose',
    'traceroute', 'ifconfig', 'whoami', 'chmod', 'chown', 'chgrp', 'systemctl',
    'journalctl', 'service', 'grep', 'awk', 'find', 'gzip', 'bzip2', 'unzip',
    'cmake', 'meson', 'ninja', 'gradle', 'mvn', 'ant', 'nmap', 'netstat', 'lsof',
    'psql', 'mysql', 'redis-cli', 'mongosh', 'sqlite3', 'rg', 'fzf', 'jq', 'sed',
    # 基础 shell 命令（刻意避开 head/tail/more/less/date/time/man/file/top/free/
    # kill/sleep 等常见英语词，避免与短语识别冲突）
    'echo', 'cd', 'pwd', 'ls', 'cp', 'mv', 'rm', 'mkdir', 'rmdir', 'touch',
    'cat', 'sort', 'uniq', 'wc', 'tr', 'tee', 'tar', 'zip', 'unzip', 'ps',
    'htop', 'df', 'du', 'clear', 'uname', 'hostname', 'uptime', 'useradd',
    'usermod', 'passwd', 'diff', 'comm', 'cmp', 'basename', 'dirname',
    'realpath', 'readlink', 'ln', 'stat', 'strings', 'nm', 'ldd', 'objdump',
    'strace', 'ping', 'nslookup', 'xargs', 'xxd', 'hexdump', 'findstr',
    'tasklist', 'taskkill', 'ipconfig', 'umount',
}

# 英语虚词：CLI 命令判定中，首词命中命令但第二词是虚词时（如 "sort out"、
# "cat and dog"、"Clear the table"）不判命令，避免自然语言短语/句子被误路由
ENGLISH_FUNCTION_WORDS = {
    'the', 'a', 'an', 'of', 'to', 'for', 'with', 'in', 'on', 'at', 'and', 'or',
    'but', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'am', 'this',
    'that', 'these', 'those', 'my', 'your', 'his', 'her', 'its', 'our', 'their',
    'as', 'by', 'from', 'into', 'onto', 'up', 'down', 'out', 'over', 'under',
    'again', 'then', 'there', 'here', 'when', 'where', 'why', 'how', 'all',
    'any', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor',
    'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'about',
    'after', 'before', 'because', 'between', 'during', 'through', 'without',
    'do', 'does', 'did', 'has', 'have', 'had', 'will', 'would', 'can', 'could',
    'shall', 'should', 'may', 'might', 'must',
}

# ---- 常见缩写：句点规则排除，避免 e.g. / U.S.A 被误判为代码 ----
COMMON_ABBREVIATIONS = {
    'e.g.', 'i.e.', 'etc.', 'vs.', 'viz.', 'Dr.', 'Mr.', 'Mrs.', 'Ms.', 'Prof.',
    'St.', 'Ave.', 'U.S.', 'U.K.', 'U.S.A.', 'U.S.S.R.',
}

# 拦截原因 → 展示文案（word_too_long 支持 {q} 占位）
BLOCK_MESSAGES = {
    'empty': '输入内容为空，未发起查询。',
    'url': '检测到 URL 链接，按规则不提供解析。',
    'email': '检测到邮箱地址，按规则不提供解析。',
    'drive': '检测到盘符路径，按规则不提供解析。',
    'filename': '检测到文件名（含常见扩展名），按规则不提供解析。',
    'path': '检测到文件路径（Unix 或 Windows），按规则不提供解析。',
    'sentence': '检测到完整英文句子（中文语句已改为语义解析），按规则只解析单词、短语或术语。',
    'fake_word': '疑似无意义字符序列（纯重复或无元音超长串），按规则不提供解析。',
    'word_too_long': '纯英文单词“{q}”长度超过 15 个字符，按规则不提供翻译。如需查询，请缩短为词根、前缀/后缀，或改为短语形式发送。',
    'phrase_too_long': '英文短语超过 5 个单词，按规则不提供翻译。请拆分为更短的词组查询。',
    # 长度类拦截文案按类型分别说明，且数值由阈值常量动态生成
    # （阈值已可环境变量覆盖，写死数字会导致文案与逻辑脱节）
    'too_long': '内容过长，超出该类型的有效字符上限，按规则不提供解析。',
    'chinese_term_too_long': (f'中文术语过长（纯中文超过 {CHINESE_PURE_MAX_LEN} 个有效字符，'
                              f'中英夹杂超过 {CHINESE_MIXED_MAX_LEN} 个有效字符），按规则不提供解析。'
                              f'若这是一段完整的句子，请以句号结尾后重试。'),
    'chinese_sentence_too_long': (f'中文语句过长（超过 {CHINESE_SENTENCE_MAX_LEN} 个有效字符），'
                                  f'按规则不提供解析。'),
    'chinese_question_too_long': (f'中文疑问句过长（超过 {CHINESE_QUESTION_MAX_LEN} 个有效字符），'
                                  f'按规则不提供解析。'),
    'chinese_paragraph_too_long': (f'中文段落过长（超过 {CHINESE_PARAGRAPH_MAX_LEN} 个有效字符），'
                                   f'按规则不提供解析。请分句或分段后再查询。'),
}


def _letter_count(text):
    """统计中英文字符总数（有效字符，用于长度限制；标点/符号不计入）。"""
    return len(CN_CHAR_RE.findall(text)) + len(EN_CHAR_RE.findall(text))


def is_chinese_dominant(text):
    """中文主导：中文较多（> CN_DOMINANT_MIN）或含中文分句标点 → 主体是自然语言，可否决 LaTeX。"""
    return (len(CN_CHAR_RE.findall(text)) > CN_DOMINANT_MIN) or bool(CHINESE_STRONG_SENTENCE_RE.search(text))


def _is_abbreviation(text):
    """判断是否为常见缩写（避免句点规则将 e.g. / U.S.A 误判为代码）。"""
    t = text.strip().lower()
    if t in COMMON_ABBREVIATIONS:
        return True
    # 全大写缩写：u.s.a / e.u. / b.b.c
    if re.match(r'^[a-z](\.[a-z])+\.?$', t):
        return True
    return False


def _dollar_is_latex(text):
    """判断 $...$ 是否更像 LaTeX 公式（而非价格/货币文本）。"""
    m = LATEX_DOLLAR_RE.search(text)
    if not m:
        return False
    inner = m.group(1).strip()
    if not inner:
        return False
    if ' ' in inner and not re.search(r'[\\^_{}=+\-*/<>]', inner):
        return False  # "$5 and $10" 这类无数学符号的空格文本不是公式；
                      # "$E = mc^2$" 含 = 与 ^，仍判 LaTeX
    if PRICE_RE.match(text.strip()):
        return False  # 纯价格 "$5"、"5$"
    return True


def looks_like_latex(text):
    """判断是否为 LaTeX 数学公式/命令。

    必须在代码判定之前调用：反斜杠命令与 $ 若先落入代码分支会被误分类。
    同时需避免与路径特征冲突（Windows 盘符、反斜杠路径 usr/bin）。
    中文主导排除：含中文较多或含中文说明句标点时不判 LaTeX，交还中文分支，
    使 "\text{平均值}" 仍走 LaTeX，而 "当x→0时，\frac{1}{x}趋于无穷" 归中文。
    """
    if re.match(r'^[A-Za-z]:\\', text):
        return False  # Windows 盘符（双保险，前置拦截已处理）
    if BACKSLASH_PATH_RE.match(text):
        return False  # \usr\bin\file.txt 反斜杠路径（非 LaTeX 命令）
    # 中文说明句主导 → 交还中文分支（避免含 \frac 的中文句子被误判为公式）
    if CN_CHAR_RE.search(text) and is_chinese_dominant(text):
        return False
    if _dollar_is_latex(text):
        return True
    if LATEX_PAREN_RE.search(text):
        return True  # \(...\) \[...\]
    if LATEX_BEGIN_RE.search(text):
        return True  # \begin{env}
    # \frac \alpha 等命令（单段反斜杠命令；\usr\bin 已在上面排除）
    if LATEX_COMMAND_RE.search(text):
        return True
    return False


def looks_like_code_block(text):
    """判断多行文本是否像代码块（而非文章/自然语言段落）。

    特征（满足其一即判定，与单行 looks_like_code 互斥，均归 GENERAL）：
    1. 行首缩进（Python/缩进式语言）
    2. 代码关键字（def/class/import/return/if/for 等）
    3. 成对花括号或圆括号跨行
    4. 注释特征（行首 #、//、/*）
    多行但无任何代码特征 → 非代码块，交还中文/混合分支做句子与长度拦截。
    """
    if '\n' not in text:
        return False
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    # 1. 行首缩进
    if any(re.match(r'^\s+\S', ln) for ln in lines):
        return True
    # 2. 代码关键字
    if CODE_BLOCK_KEYWORDS_RE.search(text):
        return True
    # 3. 成对花括号 / 圆括号跨行
    if text.count('{') > 0 and text.count('}') > 0:
        return True
    if text.count('(') >= 2 and text.count(')') >= 2:
        return True
    # 4. 注释特征
    if re.search(r'^\s*#', text, re.M) or '//' in text or '/*' in text:
        return True
    # 5. 行尾分号 / 花括号 / Python 冒号
    if any(re.search(r'[;{}:]\s*$', ln) for ln in lines):
        return True
    # 6. 命令块：首行以 CLI 命令词开头（echo hello / cd /tmp / pip install）
    #    且第二词不是英语虚词（避免 "Clear the table" 这类句子误判）
    first_words = lines[0].strip().split()
    if first_words and first_words[0].lower() in CLI_COMMAND_WORDS:
        if len(first_words) == 1 or first_words[1].lower() not in ENGLISH_FUNCTION_WORDS:
            return True
    # 7. 赋值/运算符密集：≥2 行含 =（== += => -> 亦含 =；自然语言几乎不用 =）
    assign_lines = sum(1 for ln in lines if '=' in ln or '->' in ln)
    if assign_lines >= 2:
        return True
    # 8. SQL 语句结构：操作动词 + 子句词组合（SELECT...FROM / INSERT...INTO /
    #    UPDATE...SET / DELETE...FROM / CREATE...TABLE）。要求"动词+子句词"成对
    #    出现而非孤立词，避免 "Clear the table" "Set the chairs" 等英语短语误判
    if re.search(
        r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b.*'
        r'\b(FROM|INTO|SET|VALUES|TABLE|WHERE|JOIN|GROUP|ORDER|LIMIT|HAVING)\b',
        text, re.I | re.S
    ):
        return True
    # 9. 标记语言/模板标签（HTML/XML/JSX；< 后须紧跟字母，排除 a < b 比较）
    if re.search(r'<[a-zA-Z][a-zA-Z0-9]*(\s[^>]*)?>', text) or '{{' in text or '{%' in text:
        return True
    return False


def looks_like_code(text):
    """判断文本是否更像代码/命令（而非自然语言）。

    原则：强符号集只保留自然语言几乎不出现的字符；
    其余符号（% & ( ) / * @ # + $）用上下文规则判定，避免误伤
    "100%"、"a & b"、"R&D"、"e.g."、"#tag"、"$5" 等自然语言/价格/缩写。
    调用顺序在 looks_like_latex 之后，两者特征互斥（$、\\ 归 LaTeX）。
    """
    # 1. 强代码符号（等号/分号/花括号/方括号/尖括号/管道/反斜杠/反引号/波浪号/脱字符/下划线）
    if STRONG_CODE_SYMBOLS_RE.search(text):
        return True
    # 2. 命令选项：-x / --xx（连字符前是字符串开头或空白）
    if re.search(r'(?:^|\s)--?\w', text):
        return True
    # 3. 函数调用：foo() / print(x)（括号须紧跟标识符，排除 "(hello)"、自然语言括号）
    if re.search(r'[A-Za-z_]\w*\([^)]*\)', text):
        return True
    # 4. 逻辑/乘法/幂运算符：&& || a*b 2**10（单 & / 单 | 不判定，避免误伤 "R&D"、"a or b"）
    if re.search(r'[A-Za-z0-9_](?:&&|\|\||\*)[A-Za-z0-9_]', text):
        return True
    if '**' in text:
        return True
    # 5. 双斜杠（注释/协议）、双冒号（作用域）
    if '//' in text or '::' in text:
        return True
    # 6. 无空格跟随的逗号：foo,bar（自然语言逗号后通常有空格）
    if re.search(r',\S', text):
        return True
    # 7. 单词/数字内部的句点：foo.bar / 3.14 / v1.2（排除常见缩写）
    if re.search(r'[A-Za-z0-9]\.[A-Za-z0-9_]', text) and not _is_abbreviation(text):
        return True
    # 8. 编程语言名/后缀：C++ C#（自然语言极少）
    if re.search(r'\b[A-Za-z]{1,3}(?:\+\+|#)\b', text):
        return True
    return False


# =======================================================
# 分类器：三层结构（L0 归一化 → L1 硬否决 → L2 特征 → L3 有序规则表）
#
# 设计原则：re 只负责"测量"（计数/布尔），决策全部由 RULES 数据表表达。
# 新增规则只需往表里插一条并声明 veto，不再依赖 if 链的书写顺序，
# 从根本上消除"插入新规则静默改判旧行为"的问题。
# =======================================================

def _decide_long_text_code(f):
    """长文本代码优先（剪贴板取词）：长度已由 L1 保证不超绝对上限。"""
    return 'query', SYSTEM_PROMPT_CODE


def _decide_english_word(f):
    """纯英文单词：camelCase 归代码；伪词/超长先拦截。"""
    if f['camel']:
        return 'query', SYSTEM_PROMPT_GENERAL
    if f['fake_word']:
        return 'block', 'fake_word'
    if len(f['text']) > WORD_MAX_LEN:
        return 'block', 'word_too_long'
    return 'query', SYSTEM_PROMPT_EN_WORD


# ---- 中文子类型层：主路由不变，只做"主 prompt + 附加指令"的变体组合 ----
# 设计取舍：不为每个子类型复制整份 prompt（维护成本随类型数线性膨胀），
# 而是由规则层做廉价粗分 → 拼一段附加指令 → LLM 在 subtype 字段确认并回传 →
# 渲染层据此显示第二枚标签。缓存键含 prompt 哈希，变体天然分桶、互不污染。
CN_PROPER_NOUN_RE = re.compile(
    r'(?:公司|集团|大学|学院|研究所|学会|协会|博物馆|出版社|银行|'
    r'共和国|王国|王朝|帝国|朝代|'
    r'山脉|河流|平原|盆地|高原|半岛|群岛|海峡|运河|'
    r'协议|标准|规范|公约|法案|条约|计划|工程|'
    r'系统|平台|框架|引擎|数据库|操作系统|浏览器|处理器)$'
)   # 专名后缀（须位于末尾）
CN_PROPER_SEP_RE = re.compile(r'[·•・]')      # 间隔号：人名 / 书名 / 专名常见
CN_TERM_SUFFIX_RE = re.compile(
    r'(?:化|性|率|度|量|值|项|集|群|域|环|空间|映射|变换|算子|矩阵|向量|张量|'
    r'系统|模型|算法|结构|机制|效应|定理|定律|公式|方程|函数|变量|参数|'
    r'接口|协议|框架|索引|缓存|队列|栈|树|图|网络|节点|熵|指数|系数|常数)'
)
CN_ACADEMIC_RE = re.compile(
    r'(?:是指|是一种|是一类|是一|定义为|称为|指的是|由.{0,8}构成|满足|成立|表明|说明|'
    r'因此|由于|具有|属于|等价|推出|表现为|定理|定律|假设|模型|算法|系统|函数|方程)'
)
CN_LITERARY_RE = re.compile(r'[！…～~]|啊|呀|吧|呢|吗|呵|哦|唉|啦|嘛|哟')
CN_ARGUMENT_RE = re.compile(
    r'(?:因为|由于|因此|所以|因而|然而|但是|却|尽管|虽然|首先|其次|再次|最后|'
    r'总之|综上|可见|表明|说明|证明|推出|假设|前提|结论|一方面|另一方面)'
)
CN_MATH_SYMBOL_RE = re.compile(r'[=≈≠≤≥±×÷∈∉⊂⊆→⇒⇔∀∃∑∏∫√∂∇∞]')

SUBTYPE_ADDENDA = {
    ('chinese', 'proper_noun'): (
        "\n\n【子类型：专有名词】必须说明：所指对象是什么 → 所属类别（人名/地名/机构/标准/产品等） → "
        "命名由来或构成（可考才写，不可考则写明） → 常见译名或简称 → 与同名/近名对象的区分；"
        "并在 subtype 字段回传 \"proper_noun\"。"),
    ('chinese', 'term'): (
        "\n\n【子类型：科技术语】定义必须包含核心特征 + 一个典型应用场景，并说明所属学科与最相近的 "
        "1-2 个概念的边界；涉及公式时给出 Unicode 纯文本形式；并在 subtype 字段回传 \"term\"。"),
    ('chinese', 'common'): (
        "\n\n【子类型：普通词语/成语】先判断它是否为成语、俗语或惯用语：若是，按 字面义 → 引申义 → "
        "出处或典故（不可考则写明不可考，严禁编造） → 感情色彩与典型使用场合 → 近义与反义各 1-2 个 → "
        "2 个地道例句 的结构输出，并在 subtype 字段回传 \"idiom\"；若确为普通词语，按常规字段解析"
        "并在 subtype 字段回传 \"common\"。"),
    ('chinese_sentence', 'formula'): (
        "\n\n【子类型：含公式句】必须把句中的公式改写成 Unicode 纯文本，并逐项说明其含义、量纲或取值范围，"
        "说清该公式在句中断言了什么；并在 subtype 字段回传 \"formula\"。"),
    ('chinese_sentence', 'scientific'): (
        "\n\n【子类型：学科论断句】走科学理解路线：meaning 与 structure 写满，rhetoric 从简，"
        "rewrite 只给\"一句话读懂\"；严禁语法成分拆解；并在 subtype 字段回传 \"scientific\"。"),
    ('chinese_sentence', 'literary'): (
        "\n\n【子类型：文学/抒情句】走语言视角：style 记语体与场合，rhetoric 展开修辞与语气分析，"
        "rewrite 给 1-2 条不同风格的改写；并在 subtype 字段回传 \"literary\"。"),
    ('chinese_paragraph', 'argumentative'): (
        "\n\n【子类型：论证性段落】logic 必须写清论点 → 论据 → 论证方式（举例/因果/对比/归谬） → 结论，"
        "并指出论证成立所依赖的前提；并在 subtype 字段回传 \"argumentative\"。"),
    ('chinese_paragraph', 'narrative'): (
        "\n\n【子类型：叙事/过程性段落】logic 按时间或步骤顺序梳理脉络，sentence_map 的 point 写明每步发生了什么、"
        "导致什么结果；并在 subtype 字段回传 \"narrative\"。"),
    ('chinese_paragraph', 'formula'): (
        "\n\n【子类型：含公式段落】必须把段中的公式改写成 Unicode 纯文本，并在 terms 中说明其含义、量纲或取值范围；"
        "并在 subtype 字段回传 \"formula\"。"),
}

# 渲染用：LLM 回传的 subtype → 中文显示名（空串表示不展示）
SUBTYPE_LABELS = {
    'idiom': '成语', 'proper_noun': '专名', 'term': '术语', 'common': '词语',
    'formula': '含公式', 'scientific': '学科论断', 'literary': '文学抒情',
    'fact': '事实问句', 'causal': '因果问句', 'howto': '方法问句',
    'choice': '比较问句', 'rhetorical': '反问',
    'expository': '说明', 'argumentative': '论证', 'narrative': '叙事', 'mixed': '混合',
}


def _cn_subtype(text, f):
    """中文子类型粗分：只用廉价、可解释的特征判定，细节交由 LLM 在 subtype 字段确认。

    术语分支：proper_noun / term / common；句解·疑问分支：formula / scientific / literary / general。
    """
    # 段落分支必须先于句解分支判断：段落文本必然"含 。"，若先判 cn_sent_route
    # 会永远命中句解分支，段落变体（论证/叙事/说明）形同虚设。
    if f['cn_para_route'] and not f['cn_question']:
        if f['latex_signal'] or CN_MATH_SYMBOL_RE.search(text) or re.search(r'[A-Za-z]\s*[_^]', text):
            return 'formula'
        if CN_ARGUMENT_RE.search(text):
            return 'argumentative'
        if CN_LITERARY_RE.search(text):
            return 'narrative'
        return 'expository'
    if f['cn_question'] or f['cn_sent_route']:
        if f['latex_signal'] or CN_MATH_SYMBOL_RE.search(text) or re.search(r'[A-Za-z]\s*[_^]', text):
            return 'formula'
        if CN_ACADEMIC_RE.search(text):
            return 'scientific'
        if CN_LITERARY_RE.search(text):
            return 'literary'
        return 'general'
    if CN_PROPER_SEP_RE.search(text) or CN_PROPER_NOUN_RE.search(text):
        return 'proper_noun'
    if f['has_en'] or re.search(r'\d', text) or CN_TERM_SUFFIX_RE.search(text):
        return 'term'
    return 'common'


def _cn_prompt(base, f):
    """主 prompt + 子类型附加指令（缓存键含 prompt 哈希，变体天然分桶）。"""
    return base + SUBTYPE_ADDENDA.get((PROMPT_NAMES[base], f.get('subtype')), '')


def _decide_chinese(f):
    """含中文分支：疑问 → 段落 → 句解 → 术语，四段派发。

    顺序要点：
    1. 疑问句（全角 ？ 结尾）必须最先判 —— ？ 已从 CHINESE_SENTENCE_PUNCT_RE
       移除，且与段落判定互斥；用户规则是"以 ？ 结尾即视为问句"。
    2. 段落（多句且超过句解上限）次之 —— 它只接管原本会被静默拦截的长文本，
       故 ≤120 的多句输入仍落回句解，既有用例零回归。
    """
    if f['cn_question']:
        if f['letters'] > CHINESE_QUESTION_MAX_LEN:
            return 'block', 'chinese_question_too_long'
        return 'query', _cn_prompt(SYSTEM_PROMPT_CHINESE_QUESTION, f)
    if f['cn_para_route']:
        if f['letters'] > CHINESE_PARAGRAPH_MAX_LEN:
            return 'block', 'chinese_paragraph_too_long'
        return 'query', _cn_prompt(SYSTEM_PROMPT_CHINESE_PARAGRAPH, f)
    if f['cn_sent_route']:
        if f['letters'] > CHINESE_SENTENCE_MAX_LEN:
            return 'block', 'chinese_sentence_too_long'
        return 'query', _cn_prompt(SYSTEM_PROMPT_CHINESE_SENTENCE, f)
    # 符号（& = ^ _ ( ) + / % # @ 等）不计入有效字符；中英夹杂阈值更宽
    limit = CHINESE_MIXED_MAX_LEN if f['has_en'] else CHINESE_PURE_MAX_LEN
    if f['letters'] > limit:
        return 'block', 'chinese_term_too_long'
    return 'query', _cn_prompt(SYSTEM_PROMPT_CHINESE, f)


def _decide_code_like(f):
    if f['letters'] > CODE_MAX_LEN:
        return 'block', 'too_long'
    return 'query', SYSTEM_PROMPT_GENERAL


def _decide_english_phrase(f):
    if f['word_count'] > PHRASE_MAX_WORDS:
        return 'block', 'phrase_too_long'
    return 'query', SYSTEM_PROMPT_EN_PHRASE


def _decide_fallback(f):
    """表尾兜底：非中文未知混合串按保守阈值限长，否则走通用解析。"""
    if f['letters'] > MIXED_MAX_LEN:
        return 'block', 'too_long'
    return 'query', SYSTEM_PROMPT_GENERAL


def _is_cn_sentence(text, cn_count, en_count, has_cn, cn_nl_route, code_block):
    """中文自然语句判定：含汉字 + 中文自然语言信号 → 代码类规则必须让位。

    入参 cn_nl_route 是复合谓词（疑问句 ∨ 句解），不是单一的句末标点：
    疑问句（全角 ？ 结尾）虽不再走句解路由，但仍必须让代码规则让位，
    否则"长疑问句 + [n] 引注""疑问句里出现 .md"会被代码块/文件名分支抢走。

    依据：。？！； 是 CJK 专有标点，英文系路由（单词/短语/句子/命令）的正则只接受
    字母与 [.?!]，LaTeX 分支又被 cn_dominant 否决 —— 故"含 。"几乎等价于
    "这是中文自然语言"。唯二的真实例外是代码，用下面的代码痕迹把它们排掉：

    - 多行代码块（code_block：行首缩进 / def / import / 花括号 / SQL / HTML）→ 否；
      这条保证"多行代码 + 中文注释以 。 结尾"仍走代码。
    - 出现 = ; { } < > | \\ ` ~ ^ _ 等强符号时，仅当"汉字数 ≥ 英文字母数"才认作
      中文句：覆盖「…迭代式 x_{n+1} = r·x_n(1−x_n)…」这类中文公式句，而 VSCode
      复制的一行 Python（英文字母远多于汉字）不会被放行。
    - 形如 foo(...) 的函数调用、:: -> // 等记号（无强符号时）→ 否。
    - 学术引注 [2] [4-6] 先剔除：它是标注，不是代码索引。
    """
    if not (has_cn and cn_nl_route) or code_block:
        return False
    stripped = CITATION_REF_RE.sub(' ', text)
    if CODE_SIGNAL_STRICT_RE.search(stripped):
        return cn_count >= en_count
    if re.search(r'[A-Za-z_]\w*\s*\(', stripped):
        return False
    if '::' in stripped or '->' in stripped or '=>' in stripped or '//' in stripped:
        return False
    return True


# L3 有序规则表：自上而下取"第一条 requires 成立且未被任何 veto 否决"的规则。
# requires / veto 均为 (特征名 → 布尔) 的纯函数，便于 --explain 逐条解释。
# 每条规则 = (规则名, requires, vetoes, decide)
#   requires(f) -> bool            该规则的成立条件
#   vetoes = ((否决名, veto_fn),)  任一 veto_fn 为真 → 本条让位给后续规则
#   decide(f) -> (action, payload) 命中后的决策
RULES = (
    # $...$（成对且含数学符号）/ \(...\) / \begin{env} / \command；
    # 中文主导时让位中文分支（"\text{平均值}" 仍是 latex）
    ('latex', lambda f: f['latex_signal'],
     (('chinese_dominant', lambda f: f['cn_dominant']),),
     lambda f: ('query', SYSTEM_PROMPT_LATEX)),
    # > 80 且非 LaTeX 的长文本：仅在存在代码信号时按代码块处理（剪贴板长代码场景）；
    # 无代码信号的长文本（长英文段落 / 长中文散文）让位后续规则 → 由句长/长度规则拦截。
    # 用 strong_sym_nocite 而非 code_like：code_like 会把 3.14 / v1.2（句点规则）判成代码，
    # 使含小数/版本号的英文段落被本规则误接；strong_sym_nocite 是 code_like 第 1 条的子集，
    # 且已剔除学术引注 [7] [9]（自然语言几乎不出现 = ; { } [ ] < > | \ ` ~ ^ _）。
    ('long_text_code', lambda f: f['letters'] > LONG_TEXT_CODE_LEN,
     (('no_code_signal', lambda f: not (f['code_block'] or f['multiline_code_sym_nocite']
                                        or f['strong_sym_nocite'])),
      # 含 。？！ 的中文自然句即便超过 80 有效字符，也不因 [n] 引注被判代码块
      ('cn_sentence', lambda f: f['cn_sentence'])),
     _decide_long_text_code),
    # 多行代码块先于中文分支：含中文注释的代码不被误拦
    ('code_block', lambda f: f['code_block'], (), _decide_long_text_code),
    # 多行兜底：多行文本含保守代码符号（= { } [ ] -> => :: // 行首 #）
    ('multiline_code_symbol', lambda f: f['is_multiline'] and f['multiline_code_sym_nocite'],
     (('cn_sentence', lambda f: f['cn_sentence']),),
     lambda f: ('query', SYSTEM_PROMPT_CODE)),
    # 带常见扩展名、无空格、无代码外壳（load("config.json") 不误拦）
    ('filename', lambda f: f['filename'],
     (('cn_sentence', lambda f: f['cn_sentence']),),
     lambda f: ('block', 'filename')),
    ('english_word', lambda f: f['is_word'], (), _decide_english_word),
    ('chinese', lambda f: f['has_cn'], (), _decide_chinese),
    ('code_like', lambda f: f['code_like'], (), _decide_code_like),
    # "git push" 这类无符号命令（第二词非虚词）；本规则词序早于 english_sentence，
    # 故带句末标点的整句（"find duplicate files here."）须让位英文句子拦截，
    # 避免长英文段落因首词恰好像命令而被当成查询。
    ('cli_command', lambda f: f['is_cli'],
     (('english_sentence', lambda f: f['en_sentence']),),
     lambda f: ('query', SYSTEM_PROMPT_GENERAL)),
    ('english_sentence', lambda f: f['en_sentence'], (), lambda f: ('block', 'sentence')),
    ('english_phrase', lambda f: f['is_phrase'], (), _decide_english_phrase),
    ('fallback', lambda f: True, (), _decide_fallback),  # requires 恒真：表尾兜底
)

# --explain 输出中被追踪的特征（顺序即展示顺序）
_EXPLAIN_FEATURES = (
    'letters', 'cn_count', 'en_count', 'has_cn', 'has_en', 'is_multiline',
    'cn_question', 'has_cn_sent_punct', 'ends_cn_sent_punct', 'cn_sent_route',
    'cn_punct_count', 'cn_para_route',
    'has_cn_clause_punct', 'cn_dominant', 'cn_nl_route', 'cn_sentence', 'subtype',
    'latex_signal',
    'code_block', 'multiline_code_sym', 'filename', 'is_word', 'camel',
    'fake_word', 'code_like', 'strong_sym', 'strong_sym_nocite', 'is_cli', 'en_sentence', 'is_phrase',
    'word_count',
)


def _extract_features(text, norm):
    """L2 特征提取层：一次性产出全部具名特征，供规则表与 --explain 复用。"""
    cn_count = len(CN_CHAR_RE.findall(text))
    en_count = len(EN_CHAR_RE.findall(text))
    letters = cn_count + en_count
    words = text.split()
    first_word = words[0].lower() if words else ''
    second_word = words[1].lower() if len(words) > 1 else ''
    # 疑问句：仅认全角 ？(U+FF1F) 且必须结尾（半角 ? 完全不参与）
    cn_question = bool(CN_QUESTION_END_RE.search(text))
    has_cn_sent_punct = bool(CHINESE_SENTENCE_PUNCT_RE.search(text))
    ends_cn_sent_punct = bool(CN_SENT_END_PUNCT_RE.search(text))
    # 句解路由：默认只认"结尾判定"（CN_PUNCT_END_ONLY=1），半截文本不发起请求。
    # 句号收尾无条件成立；其他收尾标点（；！…）才要求长度达标（防「甲；」被判成句子）。
    # CN_PUNCT_END_ONLY=0 时恢复"含 。！… 即判"的旧行为。
    ends_full_stop = bool(CN_END_FULL_STOP_RE.search(text))
    if CN_PUNCT_END_ONLY:
        cn_sent_route = ends_full_stop or (ends_cn_sent_punct and letters >= CN_SENTENCE_MIN_LEN)
    else:
        cn_sent_route = (has_cn_sent_punct
                         or (ends_cn_sent_punct and letters >= CN_SENTENCE_MIN_LEN))
    # 段落路由（方案 A）：以句号类收尾 + 超过句解上限 + 至少一个句末标点。
    # 与句解一样要求"结尾判定"：不以句号收尾的长文本一律不路由（落进术语档被拦），
    # 避免划选半截内容时白白发起 API 请求。
    punct_count = len(CN_SENT_PUNCT_COUNT_RE.findall(text))
    cn_para_route = (ends_cn_sent_punct
                     and letters > CHINESE_SENTENCE_MAX_LEN
                     and punct_count >= CN_PARA_MIN_PUNCT)
    # 中文自然语言统一谓词：疑问 ∨ 句解 ∨ 段落 ∨ 中文主导长文本，供代码类规则让位。
    # 最后一项是"仅结尾判定"模式必需的补丁：路由判定收紧后，不以句号收尾的长段落
    # （如带 [n] 引注的半截划选）不再被 cn_sent_route 覆盖，而 [ ] 又是强代码符号，
    # 会被 long_text_code 判成代码块 —— 那反而会发起一次请求。让位判定必须比路由宽。
    cn_dominant = is_chinese_dominant(text)
    cn_nl_route = (cn_question or cn_sent_route or cn_para_route
                   or (cn_dominant and letters > CHINESE_SENTENCE_MAX_LEN))
    # 剔除学术引注后的强代码符号：中文学术段落里的 [7] [9] [13] 是标注而非代码索引。
    # 若原样计入 strong_sym，带引注的长段落会被 long_text_code 误判成代码块
    # （既分类错误，又白白发起一次请求）。仅在中文主导时剔除，
    # 避免影响 arr[0] 这类真实代码索引。
    strong_sym_nocite = bool(STRONG_CODE_SYMBOLS_RE.search(
        CITATION_REF_RE.sub(' ', text) if cn_dominant else text))
    # 同理，多行代码符号也要剔除引注：MULTILINE_CODE_SYM_RE 含 [ ]，
    # 否则带 [n] 的中文段落会被 multiline_code_symbol / long_text_code 判成代码。
    nocite_norm = CITATION_REF_RE.sub(' ', norm) if cn_dominant else norm
    multiline_code_sym_nocite = bool(MULTILINE_CODE_SYM_RE.search(nocite_norm))
    code_block = looks_like_code_block(norm)
    f = {
        'text': text,
        'norm': norm,
        'letters': letters,
        'cn_count': cn_count,
        'en_count': en_count,
        'has_cn': cn_count > 0,
        'has_en': en_count > 0,
        'words': words,
        'word_count': len(words),
        'is_multiline': '\n' in norm,
        'cn_question': cn_question,
        'has_cn_sent_punct': has_cn_sent_punct,
        'ends_cn_sent_punct': ends_cn_sent_punct,
        'cn_sent_route': cn_sent_route,
        'cn_punct_count': punct_count,
        'cn_para_route': cn_para_route,
        'cn_nl_route': cn_nl_route,
        'has_cn_clause_punct': bool(CHINESE_STRONG_SENTENCE_RE.search(text)),
        'cn_dominant': cn_dominant,
        'strong_sym_nocite': strong_sym_nocite,
        # 中文自然语句（自然语言信号 + 无真实代码痕迹）：供 long_text_code /
        # multiline_code_symbol / filename 让位，避免中文学术句的 [n] 引注被判代码
        'cn_sentence': _is_cn_sentence(
            text, cn_count, en_count, cn_count > 0, cn_nl_route, code_block),
        'latex_signal': looks_like_latex(norm),
        'code_block': code_block,
        'multiline_code_sym': bool(MULTILINE_CODE_SYM_RE.search(norm)),
        'multiline_code_sym_nocite': multiline_code_sym_nocite,
        'filename': (bool(FILE_EXT_RE.search(text)) and ' ' not in text
                     and not STRONG_CODE_SYMBOLS_RE.search(text)
                     and not re.search(r'["\'()]', text)),
        'is_word': bool(WORD_RE.match(text)),
        'camel': bool(CAMELCASE_RE.match(text)),
        'fake_word': bool(FAKE_WORD_RE.match(text)),
        'code_like': looks_like_code(text),
        # 强代码符号（= ; { } [ ] < > | \ ` ~ ^ _）：自然语言几乎不出现，
        # 供 long_text_code 判断"长文本是否真为代码"（不用 code_like，避免 3.14/v1.2 误判）
        'strong_sym': bool(STRONG_CODE_SYMBOLS_RE.search(text)),
        'is_cli': (bool(first_word) and len(words) > 1
                   and first_word in CLI_COMMAND_WORDS
                   and second_word not in ENGLISH_FUNCTION_WORDS),
        'en_sentence': bool(EN_SENTENCE_RE.match(text)),
        'is_phrase': bool(PHRASE_RE.match(text)),
        'first_word': first_word,
        'second_word': second_word,
    }
    # 子类型依赖上面已算出的路由特征，故在字典构造完成后补算
    f['subtype'] = _cn_subtype(text, f)
    return f


def _classify(text, explain=False):
    """三层分类核心。返回 (action, payload, trace)；trace 仅在 explain=True 时收集。"""
    trace = [] if explain else None

    def log(msg):
        if trace is not None:
            trace.append(msg)

    # --- L0 归一化 ---
    text = text.strip()
    if not text:
        log('L0 归一化：输入为空')
        return 'block', 'empty', trace
    log(f'L0 归一化：raw={text!r}')

    # 判定用副本：含引号的文本（源码字符串字面量）把字面量 \n \t \r 还原为真实
    # 换行/制表，使从编辑器复制的 "SELECT ...\nFROM ..." 也能命中代码块判定。
    # 无引号时保持原样，避免 \neq \times 等 LaTeX 命令被还原破坏。
    norm = text
    if '"' in text or "'" in text:
        norm = text.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        log('L0 字面量反转义：含引号 → 还原 \\n \\t \\r')
    log(f'L0 norm={norm!r}')

    # --- L1 硬否决层：URL / 邮箱 / 盘符路径 / Unix 路径（确定性强，先于一切识别） ---
    if re.match(r'^(?:https?|ftp|file)://\S+$', text, re.I):
        log('L1 硬否决：url')
        return 'block', 'url', trace
    if re.match(r'^[\w.+-]+@[\w-]+(\.[\w-]+)+$', text):
        log('L1 硬否决：email')
        return 'block', 'email', trace
    if re.match(r'^[A-Za-z]:[\\/]', text):
        log('L1 硬否决：drive')
        return 'block', 'drive', trace
    # 反斜杠路径（\usr\bin\file.txt、\\server\share\folder）→ 拦截。
    # 正则要求至少两段路径分量，故 \alpha、\neq、\frac{1}{2}、\begin{matrix}、\\n
    # 全部不匹配 → LaTeX 零回归。已知取舍：无空格的多段裸命令链（\alpha\beta）会命中
    # 而被判 path；带空格的 \alpha \beta 不受影响（正则不接受空格）。
    if BACKSLASH_PATH_RE.match(text):
        log('L1 硬否决：path（反斜杠路径）')
        return 'block', 'path', trace
    if re.match(r'^(?:/|~/|\.\.?/)\S*$', text) and '/' in text:
        log('L1 硬否决：path')
        return 'block', 'path', trace

    # --- L2 特征提取 ---
    f: dict = _extract_features(text, norm)
    if trace is not None:
        trace.append('L2 特征：' + ', '.join(f'{k}={f[k]!r}' for k in _EXPLAIN_FEATURES))

    # L1 续：任意文本的绝对上限（超过则无论何种类型都不值得解析）
    if f['letters'] > CODE_BLOCK_MAX_LEN:
        log(f"L1 硬否决：too_long（有效字符 {f['letters']} > {CODE_BLOCK_MAX_LEN}）")
        return 'block', 'too_long', trace

    # --- L3 有序规则表 ---
    for name, requires, vetoes, decide in RULES:
        if not requires(f):
            if trace is not None:
                trace.append(f'L3 规则 [{name}]：requires 不成立，跳过')
            continue
        veto = next((vname for vname, vfn in vetoes if vfn(f)), None)
        if veto is not None:
            log(f'L3 规则 [{name}]：命中但被否决（{veto}）')
            continue
        action, payload = decide(f)
        log(f'L3 规则 [{name}]：胜出 → {action} / {_prompt_label(payload)}')
        return action, payload, trace

    log('L3 规则表无匹配（异常情形），回退 empty')
    return 'block', 'empty', trace


def classify_text(text):
    """统一分类：三层结构（硬否决 → 特征提取 → 有序规则表）。

    返回 (action, payload)：
    - action == 'query'：payload 为对应的 system prompt；
    - action == 'block'：payload 为拦截原因 key（见 BLOCK_MESSAGES）。
    """
    action, payload, _ = _classify(text, explain=False)
    return action, payload


def explain_text(text):
    """--explain 诊断报告：打印归一化文本、全部特征、规则命中/否决过程与最终决策。"""
    action, payload, trace = _classify(text, explain=True)
    lines = ['=== 分类器诊断（--explain） ===', f'输入：{text!r}', '']
    lines.extend(trace or [])
    if action == 'query':
        lines.append(f'最终决策：query → {_prompt_label(payload)}')
    else:
        # word_too_long 等文案含 {q} 占位，此处补上查询内容；格式异常时退回原文
        msg = BLOCK_MESSAGES.get(payload, '无文案')
        try:
            msg = msg.format(q=text)
        except (KeyError, IndexError, ValueError):
            pass
        lines.append(f'最终决策：block → {payload}（{msg}）')
    return '\n'.join(lines)


# =======================================================
# API 调用（状态码感知重试、动态退避、响应容错）
# =======================================================

# =======================================================
# 结果缓存（默认开启；prompt 或 model 变化后旧缓存自动失效）
# =======================================================

CACHE_ENABLED = os.environ.get('GD_LLM_CACHE', '1') != '0'
CACHE_TTL = int(os.environ.get('GD_LLM_CACHE_TTL', str(30 * 24 * 3600)))  # 默认 30 天
CACHE_DIR = os.environ.get('GD_LLM_CACHE_DIR') or os.path.join(
    os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'gd_llm')
CACHE_MAX_ENTRIES = int(os.environ.get('GD_LLM_CACHE_MAX', '2000'))


def _cache_key(query_text, system_prompt):
    """缓存键 = sha256(sha256(prompt) + model + query)。改 prompt 后自动失效。"""
    h = hashlib.sha256()
    h.update(hashlib.sha256(system_prompt.encode('utf-8')).hexdigest().encode('ascii'))
    h.update(b'\0')
    h.update(MODEL_NAME.encode('utf-8'))
    h.update(b'\0')
    h.update(query_text.encode('utf-8'))
    return h.hexdigest()


def _cache_path(key):
    return os.path.join(CACHE_DIR, key[:2], key + '.json')


def _cache_get(key):
    if not CACHE_ENABLED:
        return None
    try:
        path = _cache_path(key)
        if time.time() - os.stat(path).st_mtime > CACHE_TTL:
            return None
        with open(path, 'r', encoding='utf-8') as f:
            obj = json.load(f)
        data = obj.get('data')
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _cache_put(key, data):
    """写入缓存；错误结果不缓存。返回是否成功写入。"""
    if not CACHE_ENABLED or not isinstance(data, dict) or data.get('error'):
        return False
    path = _cache_path(key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'ts': int(time.time()), 'data': data}, f, ensure_ascii=False)
        os.replace(tmp, path)  # 原子写，避免并发读到半截文件
        return True
    except OSError:
        return False


def _cache_prune() -> None:
    """按 mtime 删除最旧条目，直到条目数不超过 CACHE_MAX_ENTRIES。"""
    if not CACHE_ENABLED:
        return
    try:
        entries = []
        for root, _, names in os.walk(CACHE_DIR):
            entries.extend(os.path.join(root, n) for n in names if n.endswith('.json'))
        if len(entries) <= CACHE_MAX_ENTRIES:
            return
        entries.sort(key=os.path.getmtime)
    except OSError:
        return
    for p in entries[:len(entries) - CACHE_MAX_ENTRIES]:
        try:
            os.remove(p)
        except OSError:
            pass


def _cache_clear():
    removed = 0
    for root, _, names in os.walk(CACHE_DIR):
        for n in names:
            if n.endswith('.json') or n.endswith('.json.tmp'):
                try:
                    os.remove(os.path.join(root, n))
                    removed += 1
                except OSError:
                    pass
    return removed


def _extract_content(res_data):
    """从 API 响应中安全提取 (content, finish_reason)，结构异常返回 (None, None)。"""
    try:
        choice = res_data['choices'][0]
        return choice['message']['content'], choice.get('finish_reason')
    except (KeyError, IndexError, TypeError):
        return None, None


# JSON 合法转义（RFC 8259）：\" \\ \/ \b \f \n \r \t \uXXXX
_JSON_ESC_FULL_RE = re.compile(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})')
# LaTeX 专用：\b \f \n \r \t 在 LaTeX 里几乎都是命令开头（\begin \frac \neq \right \times），
# 因此只保留 \" \\ \/ \uXXXX，把其余也视作漏转义。仅在"严格解析已失败"的兜底路径启用。
_JSON_ESC_MIN_RE = re.compile(r'\\(?:["\\/]|u[0-9a-fA-F]{4})')


def _repair_backslashes(s, minimal_keep=False):
    r"""补全 JSON 字符串里漏转义的反斜杠（模型直出 LaTeX / 正则时的典型错误）。

    做法：先把合法 JSON 转义整体取出暂存，再把剩余反斜杠统一翻倍，最后还原。

    minimal_keep=False：保留全部 9 种合法转义。只修复本来就会崩溃的非法转义，
                        不会误伤任何能正常解析的内容（安全模式）。
    minimal_keep=True ：仅保留 \" \\ \/ \uXXXX，把 \b \f \n \r \t 也当成漏转义处理。
                        仅在 LaTeX 响应且严格解析已失败时使用——此时不修复必然失败。
    """
    keep_re = _JSON_ESC_MIN_RE if minimal_keep else _JSON_ESC_FULL_RE
    slots = []

    def _stash(m):
        slots.append(m.group(0))
        return '\x01%d\x01' % (len(slots) - 1)

    s = keep_re.sub(_stash, s)
    s = s.replace('\\', '\\\\')
    for i, esc in enumerate(slots):
        s = s.replace('\x01%d\x01' % i, esc)
    return s


# 承载"公式/代码原文"的字段：这些字段里出现控制字符，说明反斜杠被 JSON 转义吃掉了
_LATEX_SOURCE_FIELDS = ('title', 'symbol', 'expression')


def _try_load(text):
    """尝试把文本解析成 dict，失败或非 dict 返回 None。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _has_control_chars(data):
    """检查承载 LaTeX 原文的字段里是否混入了控制字符（ord < 0x20）。

    典型场景：模型写 \frac，而 \f 是合法 JSON 转义（换页符），
    于是解析"成功"却得到「换页符 + rac」——静默损坏，比直接报错更隐蔽。
    """
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _LATEX_SOURCE_FIELDS and isinstance(value, str):
                    if any(ord(c) < 0x20 for c in value):
                        return True
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return False


def _parse_json_response(content, latex=False):
    """解析模型返回的 JSON，失败返回 None。

    兼容三类问题：
    1. ```json 围栏与前后杂散文本；
    2. 漏转义的反斜杠（模型直出 LaTeX 的 \\sum、正则的 \\s 等非法转义）；
    3. \\b \\f \\n \\r \\t 被当成合法 JSON 转义吃掉导致内容静默损坏
       （如 \frac 变成「换页符 + rac」）。

    latex=True 时额外启用"激进修复"与"控制字符校验"：LaTeX 源码里的单个反斜杠
    几乎必然是命令而非 JSON 转义，因此可以更激进地处理。
    """
    if not isinstance(content, str):
        return None
    cleaned = content.strip()
    # 去掉 ```json ... ``` 围栏
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    # 依次尝试：原文 → 安全修复 → （仅 LaTeX）激进修复
    candidates = [cleaned, _repair_backslashes(cleaned)]
    if latex:
        candidates.append(_repair_backslashes(cleaned, minimal_keep=True))

    fallback = None
    for text in candidates:
        data = _try_load(text)
        if data is None:
            continue
        if fallback is None:
            fallback = data
        # LaTeX：能解析但反斜杠已被转义吃掉 → 判为损坏，换下一个候选
        if latex and _has_control_chars(data):
            continue
        return data

    # 兜底：从杂散文本中提取 JSON 对象
    for text in candidates:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = _try_load(match.group())
            if data is None:
                continue
            if not latex or not _has_control_chars(data):
                return data
            if fallback is None:
                fallback = data

    # 所有候选都带控制字符时（如公式本身含真实换行），退回第一个能解析的结果
    return fallback


def _query_llm_core(query_text, system_prompt, max_attempts):
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"查询内容：\n{query_text}"}
        ],
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"}
    }

    req_data = json.dumps(payload).encode('utf-8')
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    last_error = None
    attempt = 0  # 预置，避免 max_attempts<=0 时末尾引用未定义
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(API_URL, data=req_data, headers=headers)
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                res_data = json.loads(resp.read().decode('utf-8'))

            content, finish_reason = _extract_content(res_data)

            # 输出被长度上限截断：重试必然得到同样结果，直接给出明确提示
            if finish_reason == 'length':
                return {"error": "模型输出被长度上限截断，未生成完整结果。"
                                 "请缩短查询内容，或调大环境变量 GD_LLM_MAX_TOKENS。"}
            # 被内容安全策略拦截
            if finish_reason == 'content_filter':
                return {"error": "模型输出被内容安全策略过滤，请更换查询内容。"}

            parsed = _parse_json_response(content, latex=(system_prompt is SYSTEM_PROMPT_LATEX))
            if parsed is not None:
                return parsed

            last_error = ("模型返回内容为空（可能被安全策略拦截）" if not content
                          else "模型返回内容无法解析为 JSON")
            if attempt < max_attempts:
                time.sleep(min(2 ** attempt, 8))
                continue
            preview = (content or '')[:200]
            return {"error": f"响应解析失败（finish_reason={finish_reason}），"
                             f"模型返回内容预览：{preview}"}

        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code} {e.reason}"
            # 仅对 429（限流）与 5xx（服务端）重试；其他 4xx（401/403/400 等）直接失败
            retryable = (e.code == 429 or 500 <= e.code < 600)
            if not retryable:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # 网络层错误：可重试
            last_error = str(e)
        except json.JSONDecodeError as e:
            last_error = f"API 响应非 JSON：{e}"

        if attempt < max_attempts:
            time.sleep(min(2 ** attempt, 8))  # 指数退避：2s / 4s / 8s

    return {"error": f"查询失败（第 {attempt} 次尝试后）：{last_error}"}


def query_llm(query_text, system_prompt, max_attempts=MAX_ATTEMPTS):
    """带磁盘缓存的查询入口：命中缓存直接返回，未命中才请求 API。"""
    key = _cache_key(query_text, system_prompt)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    result = _query_llm_core(query_text, system_prompt, max_attempts)

    if _cache_put(key, result):
        _cache_prune()  # 仅在写入成功后做一次容量检查
    return result


# =======================================================
# 渲染 HTML（全面 HTML 转义 + 类型防御）
# =======================================================

def _esc(value):
    """安全转义：任意值 → HTML 转义后的字符串（防 XSS / 渲染破坏）。"""
    if value is None:
        return ''
    return html.escape(str(value), quote=True)


# 弹窗底部的搜索外链：点击后由 GoldenDict 交给系统默认浏览器打开
SEARCH_ENGINES = (
    ("百度", "https://www.baidu.com/s?wd={q}"),
    ("Bing", "https://www.bing.com/search?q={q}"),
    ("Google", "https://www.google.com/search?q={q}"),
)
SEARCH_QUERY_MAXLEN = 120


def _search_links(query):
    """底部一行"用浏览器搜索"外链；查询词过长时截断，避免生成超长 URL。"""
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    if not q:
        return ""
    if len(q) > SEARCH_QUERY_MAXLEN:
        q = q[:SEARCH_QUERY_MAXLEN].rstrip()
    try:
        qq = urllib.parse.quote(q, safe="")
    except Exception:
        return ""
    if not qq:
        return ""
    links = " · ".join(
        f'<a href="{_esc(tpl.format(q=qq))}" target="_blank" rel="noreferrer">{_esc(name)}</a>'
        for name, tpl in SEARCH_ENGINES
    )
    return (f'<div style="margin: 10px 0 0 0; padding-top: 6px; '
            f'border-top: 1px solid #e2e8f0; font-size: 0.85em; color: #718096;">'
            f'🔍 用浏览器搜索：{links}</div>')


# =======================================================
# LaTeX → HTML 数学渲染（纯本地，零第三方依赖，不依赖网络 CDN）
# =======================================================

# ===== LaTeX 符号表：命令名（不含反斜杠）→ 替换字符 =====
# 构建时统一追加 (?![a-zA-Z]) 边界，彻底消除 \in/\int、\to/\top 这类前缀冲突，
# 使替换结果与列表书写顺序无关。
_LATEX_SYMBOL_TABLE = [
    # 希腊字母（小写）
    ('alpha', 'α'), ('beta', 'β'), ('gamma', 'γ'), ('delta', 'δ'),
    ('epsilon', 'ε'), ('varepsilon', 'ε'), ('zeta', 'ζ'), ('eta', 'η'),
    ('theta', 'θ'), ('vartheta', 'ϑ'), ('iota', 'ι'), ('kappa', 'κ'),
    ('lambda', 'λ'), ('mu', 'μ'), ('nu', 'ν'), ('xi', 'ξ'), ('pi', 'π'),
    ('varpi', 'ϖ'), ('rho', 'ρ'), ('varrho', 'ϱ'), ('sigma', 'σ'),
    ('varsigma', 'ς'), ('tau', 'τ'), ('upsilon', 'υ'), ('phi', 'φ'),
    ('varphi', 'φ'), ('chi', 'χ'), ('psi', 'ψ'), ('omega', 'ω'),
    # 希腊字母（大写）
    ('Gamma', 'Γ'), ('Delta', 'Δ'), ('Theta', 'Θ'), ('Lambda', 'Λ'),
    ('Xi', 'Ξ'), ('Pi', 'Π'), ('Sigma', 'Σ'), ('Upsilon', 'Υ'),
    ('Phi', 'Φ'), ('Psi', 'Ψ'), ('Omega', 'Ω'),
    # 二元运算符
    ('times', '×'), ('div', '÷'), ('pm', '±'), ('mp', '∓'), ('cdot', '·'),
    ('ast', '∗'), ('star', '⋆'), ('circ', '∘'), ('bullet', '∙'),
    ('oplus', '⊕'), ('ominus', '⊖'), ('otimes', '⊗'), ('oslash', '⊘'),
    ('odot', '⊙'), ('cap', '∩'), ('cup', '∪'), ('sqcap', '⊓'),
    ('sqcup', '⊔'), ('uplus', '⊎'), ('vee', '∨'), ('wedge', '∧'),
    ('setminus', '∖'), ('diamond', '⋄'), ('bigtriangleup', '△'),
    ('bigtriangledown', '▽'), ('triangleleft', '◁'), ('triangleright', '▷'),
    # 关系符
    ('leq', '≤'), ('le', '≤'), ('geq', '≥'), ('ge', '≥'), ('neq', '≠'),
    ('ne', '≠'), ('approx', '≈'), ('equiv', '≡'), ('sim', '∼'),
    ('simeq', '≃'), ('cong', '≅'), ('asymp', '≍'), ('ll', '≪'),
    ('gg', '≫'), ('prec', '≺'), ('succ', '≻'), ('preceq', '⪯'),
    ('succeq', '⪰'), ('propto', '∝'), ('parallel', '∥'), ('perp', '⊥'),
    ('mid', '∣'), ('nmid', '∤'), ('smile', '⌣'), ('frown', '⌢'),
    # 集合与逻辑
    ('in', '∈'), ('notin', '∉'), ('ni', '∋'), ('subset', '⊂'),
    ('supset', '⊃'), ('subseteq', '⊆'), ('supseteq', '⊇'),
    ('subsetneq', '⊊'), ('supsetneq', '⊋'), ('emptyset', '∅'),
    ('varnothing', '∅'), ('forall', '∀'), ('exists', '∃'), ('nexists', '∄'),
    ('neg', '¬'), ('lnot', '¬'), ('land', '∧'), ('lor', '∨'),
    ('vdash', '⊢'), ('dashv', '⊣'), ('models', '⊨'), ('therefore', '∴'),
    ('because', '∵'),
    # 箭头
    ('to', '→'), ('rightarrow', '→'), ('longrightarrow', '⟶'),
    ('leftarrow', '←'), ('longleftarrow', '⟵'),
    ('leftrightarrow', '↔'), ('longleftrightarrow', '⟷'),
    ('Rightarrow', '⇒'), ('Longrightarrow', '⟹'),
    ('Leftarrow', '⇐'), ('Longleftarrow', '⟸'),
    ('Leftrightarrow', '⇔'), ('Longleftrightarrow', '⟺'),
    ('mapsto', '↦'), ('longmapsto', '⟼'), ('hookrightarrow', '↪'),
    ('rightharpoonup', '⇀'), ('rightharpoondown', '⇁'), ('implies', '⇒'),
    ('uparrow', '↑'), ('downarrow', '↓'), ('updownarrow', '↕'),
    ('Uparrow', '⇑'), ('Downarrow', '⇓'), ('Updownarrow', '⇕'),
    ('nearrow', '↗'), ('searrow', '↘'), ('swarrow', '↙'), ('nwarrow', '↖'),
    # 大型运算符 / 微积分
    ('sum', '∑'), ('prod', '∏'), ('coprod', '∐'), ('int', '∫'),
    ('iint', '∬'), ('iiint', '∭'), ('oint', '∮'), ('oiint', '∯'),
    ('partial', '∂'), ('nabla', '∇'), ('infty', '∞'), ('aleph', 'ℵ'),
    ('hbar', 'ℏ'), ('ell', 'ℓ'), ('imath', 'ı'), ('jmath', 'ȷ'),
    ('Re', 'ℜ'), ('Im', 'ℑ'), ('wp', '℘'), ('prime', '′'), ('angle', '∠'),
    ('measuredangle', '∡'), ('triangle', '△'), ('square', '□'),
    ('blacksquare', '■'), ('top', '⊤'), ('bot', '⊥'), ('degree', '°'),
    ('dagger', '†'), ('ddagger', '‡'), ('S', '§'), ('P', '¶'),
    ('copyright', '©'), ('pounds', '£'),
    # 分隔符
    ('langle', '⟨'), ('rangle', '⟩'), ('lbrace', '{'), ('rbrace', '}'),
    ('lbracket', '['), ('rbracket', ']'), ('vert', '|'), ('Vert', '‖'),
    ('lvert', '|'), ('rvert', '|'), ('lVert', '‖'), ('rVert', '‖'),
    ('lceil', '⌈'), ('rceil', '⌉'), ('lfloor', '⌊'), ('rfloor', '⌋'),
    # --- 希腊字母补充 ---
    ('varkappa', 'ϰ'), ('omicron', 'ο'),
    # --- 二元运算符补充 ---
    ('ltimes', '⋉'), ('rtimes', '⋈'), ('Join', '⨝'), ('bowtie', '⋈'),
    ('Cup', '⋓'), ('Cap', '⋒'), ('doublecap', '⋒'), ('doublecup', '⋓'),
    ('divideontimes', '⋇'), ('leftthreetimes', '⋋'), ('rightthreetimes', '⋌'),
    ('barwedge', '⊼'), ('veebar', '⊻'), ('dotplus', '∔'),
    ('boxplus', '⊞'), ('boxminus', '⊟'), ('boxtimes', '⊠'), ('boxdot', '⊡'),
    ('centerdot', '·'), ('intercal', '⊺'), ('smallsetminus', '∖'),
    ('circledast', '⊛'), ('circledcirc', '⊚'), ('circleddash', '⊝'),
    # --- 关系符补充：更细的不等号与近似 ---
    ('leqq', '≦'), ('geqq', '≧'), ('leqslant', '⩽'), ('geqslant', '⩾'),
    ('lesssim', '≲'), ('gtrsim', '≳'), ('lessgtr', '≶'), ('gtrless', '≷'),
    ('doteq', '≐'), ('circeq', '≗'), ('triangleq', '≜'),
    ('coloneqq', '≔'), ('coloneq', '≔'), ('eqcolon', '≕'), ('eqcirc', '≖'),
    ('fallingdotseq', '≒'), ('risingdotseq', '≓'),
    ('bumpeq', '≏'), ('Bumpeq', '≎'), ('approxeq', '≊'),
    ('backsim', '∽'), ('backsimeq', '⋍'),
    # --- 关系符补充：否定形式 ---
    ('nleq', '≰'), ('ngeq', '≱'), ('nless', '≮'), ('ngtr', '≯'),
    ('lneq', '≨'), ('gneq', '≩'), ('lnsim', '⋦'), ('gnsim', '⋧'),
    ('nsubseteq', '⊈'), ('nsupseteq', '⊉'), ('nsubset', '⊄'), ('nsupset', '⊅'),
    ('nsim', '≁'), ('ncong', '≇'), ('nequiv', '≢'), ('napprox', '≉'),
    ('nvdash', '⊬'), ('nvDash', '⊭'), ('nVdash', '⊮'), ('nVDash', '⊯'),
    ('ntriangleleft', '⋪'), ('ntriangleright', '⋫'),
    ('ntrianglelefteq', '⋬'), ('ntrianglerighteq', '⋭'),
    ('nparallel', '∦'), ('nprec', '⊀'), ('nsucc', '⊁'),
    # --- 集合与序补充 ---
    ('sqsubset', '⊏'), ('sqsupset', '⊐'),
    ('sqsubseteq', '⊑'), ('sqsupseteq', '⊒'),
    ('preccurlyeq', '≼'), ('succcurlyeq', '≽'),
    ('precsim', '≾'), ('succsim', '≿'),
    ('lll', '⋘'), ('ggg', '⋙'), ('llless', '⋘'), ('gggtr', '⋙'),
    ('vartriangleleft', '⊲'), ('vartriangleright', '⊳'),
    ('trianglelefteq', '⊴'), ('trianglerighteq', '⊵'),
    ('blacktriangleleft', '◀'), ('blacktriangleright', '▶'),
    ('blacktriangle', '▲'), ('blacktriangledown', '▼'),
    ('Vdash', '⊩'), ('vDash', '⊨'), ('Vvdash', '⊪'),
    ('multimap', '⊸'), ('multimapinv', '⟜'), ('Perp', '⫫'), ('Vbar', '⫫'),
    # --- 箭头补充 ---
    ('hookleftarrow', '↩'), ('leftharpoonup', '↼'), ('leftharpoondown', '⇁'),
    ('upharpoonleft', '↿'), ('upharpoonright', '↾'),
    ('downharpoonleft', '⇃'), ('downharpoonright', '⇂'),
    ('rightsquigarrow', '⇝'), ('leadsto', '⇝'),
    ('dashrightarrow', '⇢'), ('dashleftarrow', '⇠'),
    ('twoheadrightarrow', '↠'), ('twoheadleftarrow', '↞'),
    ('rightleftharpoons', '⇌'), ('leftrightharpoons', '⇋'),
    ('nleftarrow', '↚'), ('nrightarrow', '↛'), ('nleftrightarrow', '↮'),
    ('nLeftarrow', '⇍'), ('nRightarrow', '⇏'), ('nLeftrightarrow', '⇎'),
    ('impliedby', '⟸'), ('iff', '⟺'),
    # --- 大型运算符补充 ---
    ('bigcup', '⋃'), ('bigcap', '⋂'), ('bigvee', '⋁'), ('bigwedge', '⋀'),
    ('bigoplus', '⨁'), ('bigotimes', '⨂'), ('bigodot', '⨀'),
    ('bigsqcup', '⨆'), ('biguplus', '⨄'),
    ('iiiint', '⨌'), ('oiiint', '∰'), ('varprod', '⨉'),
    # --- 物理 / 微分几何 ---
    ('hslash', 'ℏ'), ('Box', '□'), ('complement', '∁'), ('mho', '℧'),
    ('Finv', 'Ⅎ'), ('Game', '⅁'), ('eth', 'ð'), ('backprime', '‵'),
    ('sphericalangle', '∢'), ('diagup', '╱'), ('diagdown', '╲'),
    # --- 杂项符号 ---
    ('bigstar', '★'), ('clubsuit', '♣'), ('diamondsuit', '♢'),
    ('heartsuit', '♡'), ('spadesuit', '♠'),
    ('flat', '♭'), ('natural', '♮'), ('sharp', '♯'),
    ('checkmark', '✓'), ('maltese', '✠'), ('sun', '☀'), ('moon', '☾'),
    ('circledR', '®'), ('circledS', 'Ⓢ'), ('textregistered', '®'),
    ('trademark', '™'), ('yen', '¥'), ('euro', '€'), ('cent', '¢'),
    ('micro', 'µ'), ('ohm', 'Ω'), ('Angstrom', 'Å'),
    ('degC', '℃'), ('degF', '℉'), ('dag', '†'), ('ddag', '‡'),
    # --- 分隔符补充 ---
    ('llbracket', '⟦'), ('rrbracket', '⟧'), ('colon', ':'),
    # 省略号与间距
    ('dots', '…'), ('ldots', '…'), ('cdots', '⋯'), ('vdots', '⋮'),
    ('ddots', '⋱'), ('quad', ' '), ('qquad', '  '),
    (' ', ' '), (',', ' '), (';', ' '), (':', ' '), ('!', ''),
]

# 函数名：输出可读名称（不加括号）
_LATEX_FUNC_NAMES = [
    ('sin', 'sin'), ('cos', 'cos'), ('tan', 'tan'), ('cot', 'cot'),
    ('sec', 'sec'), ('csc', 'csc'), ('arcsin', 'arcsin'), ('arccos', 'arccos'),
    ('arctan', 'arctan'), ('sinh', 'sinh'), ('cosh', 'cosh'), ('tanh', 'tanh'),
    ('coth', 'coth'), ('log', 'log'), ('ln', 'ln'), ('lg', 'lg'),
    ('exp', 'exp'), ('lim', 'lim'), ('limsup', 'lim sup'),
    ('liminf', 'lim inf'), ('max', 'max'), ('min', 'min'), ('sup', 'sup'),
    ('inf', 'inf'), ('det', 'det'), ('dim', 'dim'), ('ker', 'ker'),
    ('deg', 'deg'), ('arg', 'arg'), ('gcd', 'gcd'), ('Pr', 'Pr'),
    ('hom', 'Hom'), ('tr', 'tr'), ('rank', 'rank'),
    ('sech', 'sech'), ('csch', 'csch'), ('arccot', 'arccot'),
    ('arcsec', 'arcsec'), ('arccsc', 'arccsc'), ('cotg', 'cot'),
    ('argmin', 'arg min'), ('argmax', 'arg max'),
    ('Var', 'Var'), ('var', 'var'), ('Cov', 'Cov'), ('cov', 'cov'),
    ('sgn', 'sgn'), ('Res', 'Res'), ('curl', 'curl'), ('id', 'id'),
    ('bmod', 'mod'), ('pmod', 'mod'), ('mod', 'mod'),
    ('erf', 'erf'), ('erfc', 'erfc'), ('Tr', 'Tr'), ('diag', 'diag'),
    ('span', 'span'), ('null', 'null'), ('grad', 'grad'), ('divg', 'div'),
]

# 排版 / 布局命令：不产生可见内容，必须静默删除。
# 否则会被兜底规则剥掉反斜杠，露出 displaystyle / limits 之类的英文单词。
_LATEX_DROP_CMDS = (
    'displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle',
    'limits', 'nolimits', 'nonumber', 'notag', 'centering', 'noindent',
    'thinspace', 'negthinspace', 'negmedspace', 'negthickspace',
    'medspace', 'thickspace', 'enspace', 'allowbreak', 'protect', 'relax',
    'smallskip', 'medskip', 'bigskip', 'noalign', 'hline', 'cline',
    'hfill', 'hfil', 'hfilneg', 'vfil', 'vfill', 'vspace', 'hspace',
    'raggedright', 'raggedleft', 'mathstrut', 'strut', 'mathpalette',
    'bf', 'it', 'rm', 'tt', 'sf', 'cal', 'label', 'ref', 'eqref',
    'cite', 'tag', 'newline', 'footnotesize', 'small', 'normalsize',
    'large', 'Large', 'LARGE', 'huge', 'Huge', 'nointerlineskip',
    'offinterlineskip', 'mathopen', 'mathclose', 'mathbin', 'mathrel',
    'mathord', 'mathop', 'mathpunct', 'mathinner',
)

# 字体 / 强调命令：用 HTML 标签近似，保留"有强调"这一信息
_LATEX_STYLE_WRAPPERS = {
    'textbf': ('<b>', '</b>'), 'mathbf': ('<b>', '</b>'),
    'boldsymbol': ('<b>', '</b>'), 'bm': ('<b>', '</b>'), 'bold': ('<b>', '</b>'),
    'textit': ('<i>', '</i>'), 'mathit': ('<i>', '</i>'), 'emph': ('<i>', '</i>'),
    'texttt': ('<code>', '</code>'), 'mathtt': ('<code>', '</code>'),
    'textrm': ('<span>', '</span>'), 'mathrm': ('<span>', '</span>'),
    'textup': ('<span>', '</span>'),
}

# 装饰命令（重音符 / 上下划线 / 向量箭头）：有视觉语义，绝不能简单剥掉。
# 内容为单字符时追加 Unicode 组合符，最贴近原意。
_ACCENT_COMBINING = {
    'bar': '̄', 'overline': '̅', 'hat': '̂', 'widehat': '̂',
    'tilde': '̃', 'widetilde': '̃', 'dot': '̇', 'ddot': '̈',
    'dddot': '⃛', 'ddddot': '⃜', 'check': '̌', 'breve': '̆',
    'acute': '́', 'grave': '̀', 'mathring': '̊',
    'underline': '̲',
}

# 内容多字符（或已是 HTML）时用 CSS 文本装饰近似
_ACCENT_DECOR = {
    'bar': 'overline', 'overline': 'overline',
    'hat': 'overline', 'widehat': 'overline',
    'tilde': 'overline', 'widetilde': 'overline',
    'dot': 'overline', 'ddot': 'overline',
    'dddot': 'overline', 'ddddot': 'overline',
    'check': 'overline', 'breve': 'overline',
    'acute': 'overline', 'grave': 'overline', 'mathring': 'overline',
    'underline': 'underline',
}

# 需要叠放符号的（CSS 无法表达，用堆叠 span）
_ACCENT_STACK = {
    'vec': '→', 'overrightarrow': '→', 'overleftarrow': '←',
    'overleftrightarrow': '↔', 'underrightarrow': '←',
    'underleftarrow': '→',
}

# 无法还原字族时：剥掉命令，只保留内容
_LATEX_WRAPPERS = (
    'textsf', 'mathsf', 'mathfrak', 'mathscr', 'textnormal',
    'textup', 'textsl', 'textsc', 'mbox', 'hbox', 'operatorname', 'text',
)

# ===== 数学字母表（\mathbb / \mathcal / \mathfrak / \mathscr / \mathds）=====
_LATEX_MATHBB = {
    'A': '𝔸', 'B': '𝔹', 'C': 'ℂ', 'D': '𝔻', 'E': '𝔼', 'F': '𝔽',
    'G': '𝔾', 'H': 'ℍ', 'I': '𝕀', 'J': '𝕁', 'K': '𝕂', 'L': '𝕃',
    'M': '𝕄', 'N': 'ℕ', 'O': '𝕆', 'P': 'ℙ', 'Q': 'ℚ', 'R': 'ℝ',
    'S': '𝕊', 'T': '𝕋', 'U': '𝕌', 'V': '𝕍', 'W': '𝕎', 'X': '𝕏',
    'Y': '𝕐', 'Z': 'ℤ', 'k': '𝕜',
}
_LATEX_MATHCAL = {
    'A': '𝒜', 'B': 'ℬ', 'C': '𝒞', 'D': '𝒟', 'E': 'ℰ', 'F': 'ℱ',
    'G': '𝒢', 'H': 'ℋ', 'I': 'ℐ', 'J': '𝒥', 'K': '𝒦', 'L': 'ℒ',
    'M': 'ℳ', 'N': '𝒩', 'O': '𝒪', 'P': '𝒫', 'Q': '𝒬', 'R': 'ℛ',
    'S': '𝒮', 'T': '𝒯', 'U': '𝒰', 'V': '𝒱', 'W': '𝒲', 'X': '𝒳',
    'Y': '𝒴', 'Z': '𝒵',
}
_LATEX_MATHFRAK = {
    'A': '𝔄', 'B': '𝔅', 'C': 'ℭ', 'D': '𝔇', 'E': '𝔈', 'F': '𝔉',
    'G': '𝔊', 'H': 'ℌ', 'I': 'ℑ', 'J': '𝔍', 'K': '𝔎', 'L': '𝔏',
    'M': '𝔐', 'N': '𝔑', 'O': '𝔒', 'P': '𝔓', 'Q': '𝔔', 'R': 'ℜ',
    'S': '𝔖', 'T': '𝔗', 'U': '𝔘', 'V': '𝔙', 'W': '𝔚', 'X': '𝔛',
    'Y': '𝔜', 'Z': 'ℨ',
}


def _build_lower_map(start_cp):
    """构造 a-z 的连续 Unicode 区段映射（mathbb / mathfrak 小写区是连续的）。"""
    return {chr(ord('a') + i): chr(start_cp + i) for i in range(26)}


_LATEX_MATHBB.update(_build_lower_map(0x1D552))    # 𝕒-𝕫
_LATEX_MATHFRAK.update(_build_lower_map(0x1D51E))  # 𝔞-𝔷

# 命令名 → 字母表
_MATHFONT_MAPS = {
    'mathbb': _LATEX_MATHBB, 'Bbb': _LATEX_MATHBB, 'mathds': _LATEX_MATHBB,
    'mathcal': _LATEX_MATHCAL, 'mathscr': _LATEX_MATHCAL,
    'mathfrak': _LATEX_MATHFRAK, 'mathfr': _LATEX_MATHFRAK,
}

# 矩阵类环境的左右定界符
_ENV_DELIMS = {
    'matrix': ('', ''), 'pmatrix': ('(', ')'), 'bmatrix': ('[', ']'),
    'Bmatrix': ('{', '}'), 'vmatrix': ('|', '|'), 'Vmatrix': ('‖', '‖'),
    'smallmatrix': ('', ''),
}

# \not 的预组合形式
_LATEX_NOT_MAP = {
    '=': '≠', '<': '≮', '>': '≯', '\\in': '∉', '\\subset': '⊄',
    '\\subseteq': '⊈', '\\supset': '⊅', '\\supseteq': '⊉',
    '\\sim': '≁', '\\simeq': '≄', '\\cong': '≇', '\\equiv': '≢',
    '\\approx': '≉', '\\leq': '≰', '\\geq': '≱', '\\exists': '∄',
    '\\parallel': '∦', '\\prec': '⊀', '\\succ': '⊁', '\\ni': '∌',
    '\\mid': '∤', '\\vdash': '⊬', '\\models': '⊭',
}


def _compile_latex_map(table):
    """按命令长度降序编译，并统一追加 (?![a-zA-Z]) 边界。"""
    return [(re.compile(re.escape('\\' + cmd) + r'(?![a-zA-Z])'), rep)
            for cmd, rep in sorted(table, key=lambda kv: -len(kv[0]))]


_LATEX_SYMBOLS_RE = _compile_latex_map(_LATEX_SYMBOL_TABLE)
_LATEX_FUNCS_RE = _compile_latex_map(_LATEX_FUNC_NAMES)
_LATEX_WRAPPER_RE = re.compile(
    r'\\(?:' + '|'.join(sorted(_LATEX_WRAPPERS, key=len, reverse=True))
    + r')\s*\{([^{}]*)\}(?![a-zA-Z])')
_LATEX_MATHFONT_RE = re.compile(
    r'\\(mathfr|mathfrak|mathbb|Bbb|mathcal|mathscr|mathds)(?![a-zA-Z])\s*(?=\{)')
_FRAC_CMD_RE = re.compile(r'\\(?:frac|dfrac|tfrac|cfrac)(?![a-zA-Z])')
_SQRT_RE = re.compile(r'\\sqrt(?![a-zA-Z])')
_BINOM_RE = re.compile(r'\\(?:binom|dbinom|tbinom)(?![a-zA-Z])\s*(?=\{)')

# 环境 \begin{env}...\end{env}（非贪婪 + 反向引用保证配对）
_LATEX_ENV_RE = re.compile(r'\\begin\s*\{([^{}]*)\}(.*?)\\end\s*\{\1\}', re.DOTALL)
# 静默删除且不保留参数的命令
_LATEX_DROP_ARG_RE = re.compile(
    r'\\(?:label|tag|ref|eqref|cite|cline|vspace|hspace|vskip|hskip)\*?\s*\{[^{}]*\}')
# 静默删除的纯命令
_LATEX_DROP_RE = re.compile(
    r'\\(?:' + '|'.join(sorted(_LATEX_DROP_CMDS, key=len, reverse=True))
    + r')(?![a-zA-Z])')
# 字体 / 强调命令 → HTML 标签
_LATEX_STYLE_RE = re.compile(
    r'\\(' + '|'.join(sorted(_LATEX_STYLE_WRAPPERS, key=len, reverse=True))
    + r')(?![a-zA-Z])\s*(?=\{)')
# 装饰命令（bar / vec / hat / tilde / dot / overline ...）
_ACCENT_NAMES = set(_ACCENT_COMBINING) | set(_ACCENT_DECOR) | set(_ACCENT_STACK)
_LATEX_ACCENT_RE = re.compile(
    r'\\(?:' + '|'.join(sorted(_ACCENT_NAMES, key=len, reverse=True))
    + r')(?![a-zA-Z])\s*(?=\{)')
_ACCENT_BARE_RE = re.compile(
    r'\\(' + '|'.join(sorted(_ACCENT_NAMES, key=len, reverse=True))
    # `\bar\psi` 这类无花括号形式：符号表已先跑过，被装饰的可能是非 ASCII 字符，
    # 因此字符类要放宽到"任意非空白、非花括号、非上下标、非标签尖括号"的单个字符。
    + r')(?![a-zA-Z])\s*(\\[a-zA-Z]+|[^\s{}^_&<>])')
# Dirac 符号
_LATEX_BRACKET_RE = re.compile(
    r'\\(braket|ketbra|Bra|Ket|bra|ket|Braket|set)(?![a-zA-Z])\s*(?=\{)')
# 堆叠结构
_OVER_UNDER_RE = re.compile(r'\\(overbrace|underbrace)(?![a-zA-Z])\s*(?=\{)')
_SUBSTACK_RE = re.compile(r'\\substack(?![a-zA-Z])\s*(?=\{)')
_OVERSET_RE = re.compile(r'\\(overset|underset|stackrel)(?![a-zA-Z])\s*(?=\{)')
_XARROW_RE = re.compile(
    r'\\(x(?:leftright|right|left|Leftright|Right|Left)arrow)'
    r'(?:\s*\[([^\[\]]*)\])?\s*(?=\{)')
# 分隔符尺寸
_BIG_SIZES = {
    'left': '1.2em', 'right': '1.2em', 'middle': '1.2em',
    'big': '1.35em', 'Big': '1.7em', 'bigg': '2.1em', 'Bigg': '2.5em',
}
_BIG_DELIM_RE = re.compile(
    r'\\(left|right|middle|big|Big|bigg|Bigg)(?![a-zA-Z])\s*([()\[\]{}|/.])')
_BIG_CMD_RE = re.compile(
    r'\\(?:left|right|middle|big|Big|bigg|Bigg)[lrm]?(?![a-zA-Z])')
# \not
_LATEX_NOT_RE = re.compile(r'\\not(?![a-zA-Z])\s*(\\[a-zA-Z]+|.)')
# \pmod{n}
_PMOD_RE = re.compile(r'\\pmod\s*\{([^{}]*)\}')


def _read_braced(s, i):
    """从 s[i] == '{' 开始做括号配平，返回 (内容, 右括号后一位)。不配对返回 (None, i)。"""
    if i >= len(s) or s[i] != '{':
        return None, i
    depth = 0
    for j in range(i, len(s)):
        if s[j] == '{':
            depth += 1
        elif s[j] == '}':
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return None, i


def _render_frac(s):
    """递归渲染 \\frac / \\dfrac / \\tfrac / \\cfrac，支持嵌套分式。"""
    out, i = [], 0
    while True:
        m = _FRAC_CMD_RE.search(s, i)
        if not m:
            out.append(s[i:])
            break
        out.append(s[i:m.start()])
        num, j = _read_braced(s, m.end())
        if num is None:
            out.append(s[m.start():m.end()])
            i = m.end()
            continue
        den, k = _read_braced(s, j)
        if den is None:
            out.append(s[m.start():j])
            i = j
            continue
        out.append(
            '<span style="display:inline-block;vertical-align:middle;'
            'text-align:center;margin:0 3px;">'
            '<span style="display:block;border-bottom:1px solid #2d3748;'
            'padding:0 4px;line-height:1.15;">' + _render_frac(num) + '</span>'
            '<span style="display:block;padding:0 4px;line-height:1.15;">'
            + _render_frac(den) + '</span></span>')
        i = k
    return ''.join(out)


def _render_scripts(s):
    """渲染 ^{...} / _{...} / ^x / _x / ^\\cmd，支持花括号嵌套。"""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in '^_' and i + 1 < n:
            tag = 'sup' if c == '^' else 'sub'
            if s[i + 1] == '{':
                inner, j = _read_braced(s, i + 1)
                if inner is not None:
                    out.append(f'<{tag}>{_render_scripts(inner)}</{tag}>')
                    i = j
                    continue
            if s[i + 1] == '\\':
                m = re.match(r'\\[A-Za-z]+', s[i + 1:])
                if m:
                    j = i + 1 + m.end()
                    # \cmd{...}（如 x_\text{max} / \mathcal{L}_\text{matter}）：
                    # 必须连花括号一起消费，否则命令与参数被截断，
                    # 后续 \text 替换就再也匹配不上，会把 \text 原样漏给用户
                    if j < n and s[j] == '{':
                        inner, k = _read_braced(s, j)
                        if inner is not None:
                            token = m.group(0) + '{' + inner + '}'
                            out.append(f'<{tag}>{_render_scripts(token)}</{tag}>')
                            i = k
                            continue
                    out.append(f'<{tag}>{m.group(0)}</{tag}>')
                    i = j
                    continue
            if s[i + 1].isascii() and s[i + 1].isalnum():
                out.append(f'<{tag}>{s[i + 1]}</{tag}>')
                i += 2
                continue
        out.append(c)
        i += 1
    return ''.join(out)


# ---------- 通用工具 ----------

def _scan_braced_command(s, pattern, build):
    """扫描 `\\cmd{...}`（花括号可嵌套），用 build 生成替换。

    build(m, inner, s, j) 返回替换文本；若返回 (文本, 下一位置) 二元组，
    则用于需要连读两个花括号组的命令（如 \\overset{a}{b}）。
    """
    out, i = [], 0
    while True:
        m = pattern.search(s, i)
        if not m:
            out.append(s[i:])
            break
        inner, j = _read_braced(s, m.end())
        if inner is None:
            out.append(s[i:m.end()])
            i = m.end()
            continue
        out.append(s[i:m.start()])
        res = build(m, inner, s, j)
        if isinstance(res, tuple):
            out.append(res[0])
            i = res[1]
        else:
            out.append(res)
            i = j
    return ''.join(out)


def _stack_span(rows, small_idx=()):
    """把若干行居中纵向堆叠。small_idx 中的行用小字号。"""
    parts = []
    for k, r in enumerate(rows):
        style = 'display:block;line-height:1.15;'
        if k in small_idx:
            style += 'font-size:0.72em;'
        parts.append(f'<span style="{style}">{r}</span>')
    return ('<span style="display:inline-block;vertical-align:middle;'
            'text-align:center;">' + ''.join(parts) + '</span>')


def _small_span(t):
    return f'<span style="display:block;font-size:0.72em;line-height:1.1;">{t}</span>'


def _block_span(t):
    return f'<span style="display:block;line-height:1.15;">{t}</span>'


def _delim_size(nrow):
    return round(min(1.0 + 0.8 * max(0, nrow - 1), 4.0), 2)


def _wrap_delim(ch, nrow):
    size = _delim_size(nrow)
    return ('<span style="display:inline-block;vertical-align:middle;'
            f'font-size:{size}em;line-height:1;">{ch}</span>')


# ---------- 环境：矩阵 / cases / aligned ----------

def _split_cells(row):
    """按顶层 & 切分列（HTML 转义后写作 &amp;），花括号内的 & 不切。"""
    cells, cur, depth, i, n = [], [], 0, 0, len(row)
    while i < n:
        c = row[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth = max(0, depth - 1)
        elif c == '&' and depth == 0 and row[i:i + 5] == '&amp;':
            cells.append(''.join(cur))
            cur = []
            i += 5
            continue
        cur.append(c)
        i += 1
    cells.append(''.join(cur))
    return cells


def _render_environments(s, depth=0):
    """递归展开 \\begin{env}...\\end{env}，由外到内逐级下钻。"""
    if depth > 6 or '\\begin' not in s:
        return s
    out, i = [], 0
    for m in _LATEX_ENV_RE.finditer(s):
        out.append(s[i:m.start()])
        out.append(_render_env(m.group(1).strip(), m.group(2), depth))
        i = m.end()
    out.append(s[i:])
    return ''.join(out)


def _render_env(name, body, depth):
    body = re.sub(r'^\s*\{[^{}]*\}', '', body)      # 去掉 array 的 {cc} 列格式
    body = _render_environments(body, depth + 1)
    body = re.sub(r'\\(?:hline|hdashline)', '', body)
    rows = body.split('\x02')
    while rows and not rows[-1].strip():
        rows.pop()
    if not rows:
        return ''
    grid = [_split_cells(r) for r in rows]
    ncol = max(len(g) for g in grid)
    nrow = len(grid)

    if name in ('cases', 'dcases', 'drcases'):
        left, right = ('{', '') if name != 'drcases' else ('', '}')
        first_align = 'left'
    elif name in _ENV_DELIMS:
        left, right = _ENV_DELIMS[name]
        first_align = 'center'
    else:   # aligned / align / split / gather / array
        left, right = '', ''
        first_align = 'right' if ncol > 1 else 'center'

    trs = []
    for g in grid:
        tds = []
        for k, c in enumerate(g):
            al = first_align if k == 0 else 'left'
            tds.append(f'<td style="padding:1px 0.45em;text-align:{al};">{c}</td>')
        if len(g) < ncol:
            tds.append(f'<td colspan="{ncol - len(g)}"></td>')
        trs.append('<tr>' + ''.join(tds) + '</tr>')

    table = ('<table style="display:inline-table;vertical-align:middle;'
             'border-collapse:collapse;margin:1px 0;">' + ''.join(trs) + '</table>')
    return ((_wrap_delim(left, nrow) if left else '')
            + table
            + (_wrap_delim(right, nrow) if right else ''))


# ---------- 分隔符 / 根号 / 二项式 ----------

def _render_delimiters(s):
    r"""\left( \right) \big[ \Big| 等：去掉命令并按级别放大分隔符。"""

    def _rep(m):
        delim = m.group(2)
        if delim == '.':                      # \left. 是不可见分隔符
            return ''
        size = _BIG_SIZES.get(m.group(1), '1.2em')
        return (f'<span style="font-size:{size};line-height:1;'
                f'vertical-align:-0.1em;">{delim}</span>')

    s = _BIG_DELIM_RE.sub(_rep, s)
    s = _BIG_CMD_RE.sub('', s)
    return s


def _render_sqrt(s):
    r"""\sqrt[n]{x} 与 \sqrt{x}，花括号内容可嵌套。"""
    out, i = [], 0
    while True:
        m = _SQRT_RE.search(s, i)
        if not m:
            out.append(s[i:])
            break
        out.append(s[i:m.start()])
        j = m.end()
        idx = ''
        mm = re.match(r'\s*\[([^\[\]]*)\]', s[j:])
        if mm:
            idx, j = mm.group(1), j + mm.end()
        j += len(s[j:]) - len(s[j:].lstrip())
        inner, k = _read_braced(s, j)
        if inner is None:
            out.append(s[m.start():m.end()])
            i = m.end()
            continue
        head = f'<sup>{idx}</sup>' if idx else ''
        out.append(head + '<span style="border-top:1px solid #2d3748;'
                          f'padding:0 2px;">√{inner}</span>')
        i = k
    return ''.join(out)


def _render_binom(s):
    def build(m, a, s_, j):
        b, k = _read_braced(s_, j)
        if b is None:
            return s_[m.start():j]
        stack = _stack_span([a, b])
        return _wrap_delim('(', 2) + stack + _wrap_delim(')', 2), k
    return _scan_braced_command(s, _BINOM_RE, build)


# ---------- 数学字体 / Dirac ----------

def _render_mathfonts(s):
    def build(m, inner, _s, _j):
        table = _MATHFONT_MAPS.get(m.group(1))
        if table is None:
            return inner
        return ''.join(table.get(ch, ch) for ch in inner)
    return _scan_braced_command(s, _LATEX_MATHFONT_RE, build)


def _render_brackets(s):
    def build(m, a, s_, j):
        cmd = m.group(1)
        if cmd == 'ketbra':
            b, k = _read_braced(s_, j)
            return (f'|{a}⟩⟨{b}|', k) if b is not None else s_[m.start():j]
        if cmd in ('ket', 'Ket'):
            return f'|{a}⟩'
        if cmd in ('bra', 'Bra'):
            return f'⟨{a}|'
        if cmd == 'set':
            return '{' + a + '}'
        return f'⟨{a}⟩'
    return _scan_braced_command(s, _LATEX_BRACKET_RE, build)


# ---------- 堆叠结构 ----------

def _render_stacks(s):
    r"""\overset{上}{下} / \underset{下}{上} / \stackrel{上}{下}。"""
    def build(m, first, s_, j):
        second, k = _read_braced(s_, j)
        if second is None:
            return s_[m.start():j]
        if m.group(1) in ('overset', 'stackrel'):
            return _stack_span([first, second], small_idx=(0,)), k
        return _stack_span([second, first], small_idx=(1,)), k
    return _scan_braced_command(s, _OVERSET_RE, build)


def _render_substack(s):
    def build(m, inner, _s, _j):
        rows = [r for r in inner.split('\x02') if r.strip()]
        return _stack_span(rows or [inner])
    return _scan_braced_command(s, _SUBSTACK_RE, build)


def _render_overunder_braces(s):
    r"""\overbrace{x}^{n} / \underbrace{x}_{n}（标注写在花括号之后）。"""
    def build(m, inner, s_, j):
        over = m.group(1) == 'overbrace'
        glyph = '⏞' if over else '⏟'
        label, k = '', j
        mm = re.match(r'\s*[\^_]\s*', s_[j:])
        if mm:
            pos = j + mm.end()
            if pos < len(s_) and s_[pos] == '{':
                lab, k2 = _read_braced(s_, pos)
                if lab is not None:
                    label, k = lab, k2
            elif pos < len(s_) and s_[pos].isascii() and s_[pos].isalnum():
                label, k = s_[pos], pos + 1
        brace = (f'<span style="display:block;font-size:1.15em;'
                 f'line-height:0.85;">{glyph}</span>')
        lab = _small_span(label) if label else ''
        seq = (lab + brace + _block_span(inner)) if over \
            else (_block_span(inner) + brace + lab)
        return ('<span style="display:inline-block;vertical-align:middle;'
                'text-align:center;">' + seq + '</span>', k)
    return _scan_braced_command(s, _OVER_UNDER_RE, build)


_XARROW_GLYPHS = {
    'xrightarrow': '⟶', 'xleftarrow': '⟵', 'xleftrightarrow': '⟷',
    'xRightarrow': '⟹', 'xLeftarrow': '⟸', 'xLeftrightarrow': '⟺',
}


def _render_xarrows(s):
    def build(m, inner, _s, _j):
        parts = []
        if inner:
            parts.append(_small_span(inner))
        parts.append(_block_span(_XARROW_GLYPHS.get(m.group(1), '⟶')))
        if m.group(2):
            parts.append(_small_span(m.group(2)))
        return ('<span style="display:inline-block;vertical-align:middle;'
                'text-align:center;margin:0 3px;">' + ''.join(parts) + '</span>')
    return _scan_braced_command(s, _XARROW_RE, build)


# ---------- \not / 装饰 / 样式 ----------

def _render_not(s):
    def _rep(m):
        tok = m.group(1)
        return _LATEX_NOT_MAP.get(tok, tok + '̸')
    return _LATEX_NOT_RE.sub(_rep, s)


# 单个字符被一层标签包裹，如 <b>r</b>（\mathbf{r} 已被转换过）
_ONE_CHAR_TAG_RE = re.compile(r'^(<(\w+)[^>]*>)(.)(</\2>)$')


def _apply_accent(cmd, inner):
    arrow = _ACCENT_STACK.get(cmd)
    if arrow is not None:
        return _stack_span([arrow, inner], small_idx=(0,))
    mark = _ACCENT_COMBINING.get(cmd)
    if mark:
        if len(inner) == 1 and '<' not in inner:
            return inner + mark
        tm = _ONE_CHAR_TAG_RE.match(inner)     # \ddot{\mathbf{r}} 之类
        if tm:
            return tm.group(1) + tm.group(3) + mark + tm.group(4)
    decor = _ACCENT_DECOR.get(cmd)
    if decor:
        return f'<span style="text-decoration:{decor};">{inner}</span>'
    return inner


def _render_accents(s):
    def build(m, inner, _s, _j):
        # 先展开内层的字体命令再施加装饰，这样单字符内容
        # 才能用 Unicode 组合符精确表达，而不是退化成 CSS 上划线
        inner = _render_accents(_render_style_wrappers(inner))
        return _apply_accent(m.group(0)[1:], inner)
    s = _scan_braced_command(s, _LATEX_ACCENT_RE, build)
    # 无花括号形式：\bar\psi / \vec v
    s = _ACCENT_BARE_RE.sub(lambda m: _apply_accent(m.group(1), m.group(2)), s)
    return s


def _render_style_wrappers(s):
    def build(m, inner, _s, _j):
        open_tag, close_tag = _LATEX_STYLE_WRAPPERS.get(m.group(1),
                                                        ('<span>', '</span>'))
        return open_tag + _render_style_wrappers(inner) + close_tag
    return _scan_braced_command(s, _LATEX_STYLE_RE, build)


def latex_to_html(text):
    """把 LaTeX 数学片段渲染为 HTML（纯本地，无第三方依赖）。"""
    if not isinstance(text, str):
        return ''
    # 去除首尾的 $ 或 $$（包括可能的空格）
    text = re.sub(r'^\s*\$\$?\s*|\s*\$\$?\s*$', '', text.strip())
    # 这几个字面量转义必须先于 HTML 转义处理，否则 \& 会变成 \&amp; 而残留反斜杠
    text = re.sub(r'\\[&%$#]', lambda m: m.group(0)[1:], text)

    s = _esc(text)
    s = s.replace('&#10;', '<br>')   # 将 JSON 中的 \n 换行转为 HTML <br>

    s = s.replace('\\_', '\x00')     # 保护字面下划线，避免被上下标规则误判
    s = s.replace('\\\\', '\x02')    # \\ → 行分隔符（环境表格要用，末尾统一转 <br>）
    s = s.replace('\x02*', '\x02')   # \\* 的星号
    s = re.sub(r'\x02\s*\[[^\]]*\]', '\x02', s)   # \\[1em] 行距参数
    s = re.sub(r'\\([{}])', r'\1', s)
    s = s.replace('\\|', '‖')

    # 环境：矩阵 / cases / aligned ...（必须先于一切字符级替换）
    s = _render_environments(s)

    # 排版命令（\displaystyle \limits \label{...} 等）静默删除
    s = _LATEX_DROP_ARG_RE.sub('', s)
    s = _LATEX_DROP_RE.sub('', s)

    # 分隔符尺寸（\left \right \big \Big \bigg \Bigg）
    s = _render_delimiters(s)

    # \not X → 预组合否定符
    s = _render_not(s)

    # 分式（支持嵌套）/ 根号（含次数）/ 二项式
    s = _render_frac(s)
    s = _render_sqrt(s)
    s = _render_binom(s)

    # 堆叠结构：\overset \underset \stackrel \substack \overbrace \underbrace \xrightarrow
    s = _render_stacks(s)
    s = _render_substack(s)
    s = _render_overunder_braces(s)
    s = _render_xarrows(s)

    # 数学字体（必须在上下标之前：否则会把 <sup> 里的字母也一起转掉）
    s = _render_mathfonts(s)

    # 上下标（支持 ^{...} 嵌套）
    s = _render_scripts(s)
    s = s.replace('\x00', '_')       # 还原字面下划线

    # \pmod{n} / \text{...} / Dirac 符号
    s = _PMOD_RE.sub(r' (mod \1)', s)
    s = re.sub(r'\\text\s*\{([^{}]*)\}', r'\1', s)
    s = _render_brackets(s)

    # 符号表 + 函数名（顺序已无关，靠 (?![a-zA-Z]) 边界隔离）
    for pat, rep in _LATEX_SYMBOLS_RE:
        s = pat.sub(rep, s)
    for pat, rep in _LATEX_FUNCS_RE:
        s = pat.sub(rep, s)

    # 装饰命令（\bar \vec \hat \tilde \dot \overline ...）：此刻花括号内的
    # \psi 等已替换成单字符，可安全追加 Unicode 组合符
    s = _render_accents(s)

    # 字体 / 强调命令 → HTML 标签；无法还原的剥掉命令保留内容
    s = _render_style_wrappers(s)
    s = _LATEX_WRAPPER_RE.sub(r'\1', s)

    # 兜底：仍未识别的命令去掉反斜杠，避免把 \foo 原样显示给用户
    s = re.sub(r'\\([A-Za-z]+)', r'\1', s)
    s = s.replace('\x02', '<br>')    # 未被环境消费的行分隔符
    return s

def _render_html_inner(data, raw_query):
    if "error" in data:
        return (f"<div style='color:#e53e3e; padding:10px;'>"
                f"<b>AI 查询失败：</b>{_esc(data['error'])}</div>")

    query_type = data.get("query_type", "general")
    if not isinstance(query_type, str):
        query_type = "general"
    raw_title = data.get("title")
    if not isinstance(raw_title, str) or not raw_title.strip():
        raw_title = raw_query
    if query_type == "latex":
        # 头部不再显示原始 LaTeX 源码，改用与正文一致的渲染结果
        title = latex_to_html(raw_title)
        title_style = ("font-family: Georgia, 'Times New Roman', serif; "
                       "white-space: pre-wrap; overflow-x: auto;")
    else:
        title = _esc(raw_title)
        title_style = ""

    html_out = f"""
    <div style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 12px; color: #2d3748; line-height: 1.6;">
        <div style="font-size: 1.4em; font-weight: bold; color: #2b6cb0; display: flex; align-items: center; gap: 8px;">
            <span style="{title_style}">{title}</span>
    """

    tag_map = {
        "english_word": "📖 单词",
        "english_phrase": "🔤 短语",
        "chinese": "🀄 中文",
        "chinese_sentence": "🀄 句解",
        "chinese_question": "❓ 问答",
        "chinese_paragraph": "📄 段落",
        "latex": "📐 公式",
        "code": "💻 代码块",
        "general": "⚙️ 通用"
    }
    html_out += f"""<span style="font-size: 0.55em; background: #edf2f7; color: #4a5568; padding: 2px 6px; border-radius: 4px; font-weight: normal;">{_esc(tag_map.get(query_type, '通用'))}</span>"""
    # 子类型标签：由 LLM 在 subtype 字段回传（规则层只做粗分与 prompt 变体）
    raw_subtype = data.get("subtype")
    sub_label = SUBTYPE_LABELS.get(raw_subtype, '') if isinstance(raw_subtype, str) else ''
    if sub_label:
        html_out += f"""<span style="font-size: 0.55em; background: #f7fafc; color: #718096; padding: 2px 6px; border-radius: 4px; font-weight: normal;">{_esc(sub_label)}</span>"""
    html_out += """</div><hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 8px 0;" />"""

    if query_type == "english_word":
        if data.get("phonetic"):
            html_out += f"""<div style="margin-bottom: 6px; color: #718096; font-size: 1.1em;">{_esc(data.get('phonetic'))}</div>"""
        defs = data.get("definitions")
        if defs:
            if isinstance(defs, list):
                for df in defs:
                    if isinstance(df, dict):
                        pos_tag = f"""<span style="background: #ebf8ff; color: #2b6cb0; padding: 2px 6px; border-radius: 4px; font-size: 0.82em; font-weight: bold; margin-right: 6px;">{_esc(df.get('pos'))}</span>""" if df.get('pos') else ""
                        html_out += f"""<div style="margin: 4px 0;">{pos_tag}{_esc(df.get('cn', ''))}</div>"""
                    else:
                        html_out += f"""<div style="margin: 4px 0;">{_esc(df)}</div>"""
            elif isinstance(defs, str):
                html_out += f"""<div style="margin: 4px 0;">{_esc(defs)}</div>"""
        if data.get("etymology_roots"):
            html_out += f"""
            <div style="margin: 10px 0; padding: 10px 12px; background: #faf5ff; border: 1px solid #e9d8fd; border-radius: 6px;">
                <div style="font-weight: bold; color: #553c9a; margin-bottom: 6px;">🧬 词源与词根词缀详解：</div>
                <div style="white-space: pre-wrap; line-height: 1.6;">{_esc(data.get('etymology_roots'))}</div>
            </div>
            """
        phrases = data.get("phrases")
        if phrases:
            html_out += '<div style="margin: 10px 0;"><div style="font-weight: bold; color: #2c5282;">🔗 常用词组：</div>'
            for ph in phrases:
                if isinstance(ph, dict):
                    html_out += f"""<div style="margin-left: 8px;"><b>{_esc(ph.get('phrase'))}</b> <span style="color: #718096;">{_esc(ph.get('cn'))}</span></div>"""
                else:
                    html_out += f"""<div style="margin-left: 8px;">{_esc(ph)}</div>"""
            html_out += '</div>'

    elif query_type == "english_phrase":
        if data.get("literal_meaning"):
            html_out += f"""
            <div style="margin: 8px 0; padding: 6px 10px; background: #f0fff4; border-left: 3px solid #38a169; border-radius: 0 4px 4px 0;">
                <b>🖼️ 字面义/由来：</b>{_esc(data.get('literal_meaning'))}
            </div>
            """
        if data.get("meaning"):
            html_out += f"""
            <div style="background: #f0fff4; border-left: 3px solid #38a169; padding: 8px 12px; margin-bottom: 8px; border-radius: 0 4px 4px 0; font-size: 1.05em;">
                <b>💡 中文释义：</b>{_esc(data.get('meaning'))}
            </div>
            """
        if data.get("chinese_equivalent"):
            html_out += f"""<div style="margin: 4px 0;"><b>🇨🇳 对应中文：</b>{_esc(data.get('chinese_equivalent'))}</div>"""
        if data.get("usage"):
            html_out += f"""
            <div style="margin: 8px 0; padding: 6px 10px; background: #fffff0; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;">
                <b>📝 用法：</b>{_esc(data.get('usage'))}
            </div>
            """
        sim = data.get("similar")
        if sim:
            if isinstance(sim, list):
                sim_str = "、".join(_esc(x) for x in sim if x)
            else:
                sim_str = _esc(sim)
            if sim_str:
                html_out += f"""<div style="margin: 4px 0;"><b>🔁 近义：</b>{sim_str}</div>"""
        if data.get("opposite"):
            html_out += f"""<div style="margin: 4px 0;"><b>↔️ 反义/相对：</b>{_esc(data.get('opposite'))}</div>"""
        cm = data.get("common_mistakes")
        if cm and isinstance(cm, list) and len(cm) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #c53030;">⚠️ 常见错误：</div><ul style="margin: 0; padding-left: 20px;">'
            for m in cm:
                html_out += f"<li>{_esc(m)}</li>"
            html_out += '</ul></div>'

    elif query_type == "chinese":
        if data.get("context"):
            html_out += f"""<div style="margin: 4px 0; padding: 6px 10px; background: #fefcbf; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;"><b>🌐 语境：</b>{_esc(data.get('context'))}</div>"""

        if data.get("definition"):
            def_text = str(data.get('definition'))
            lines = def_text.split('\n')
            blocks = []
            current = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('【'):
                    if current:
                        blocks.append('\n'.join(current))
                        current = []
                    current.append(line)
                else:
                    current.append(line)
            if current:
                blocks.append('\n'.join(current))

            html_out += '<div style="margin: 8px 0;">'
            for block in blocks:
                match = re.match(r'(【[^】]+】)(.*)', block, re.DOTALL)
                if match:
                    title_part = _esc(match.group(1).strip())
                    content_part = _esc(match.group(2).strip())
                    html_out += f'<div style="font-weight: bold; color: #2b6cb0; margin-top: 6px;">{title_part}</div>'
                    html_out += f'<div style="padding-left: 12px; border-left: 2px solid #e2f0e8; margin: 4px 0;">{content_part}</div>'
                else:
                    html_out += f'<div style="margin: 4px 0;">{_esc(block)}</div>'
            html_out += '</div>'

        dme = data.get("definition_method_explanation")
        if dme:
            if isinstance(dme, dict):
                html_out += '<div style="margin: 4px 0 8px 0;"><b>📘 方法解释：</b><br>'
                for discipline, explanation in dme.items():
                    html_out += f'<div style="padding-left: 12px; margin-top: 4px;"><b>{_esc(discipline)}：</b>{_esc(explanation)}</div>'
                html_out += '</div>'
            elif isinstance(dme, list):
                html_out += '<div style="margin: 4px 0 8px 0;"><b>📘 方法解释：</b><br>'
                for item in dme:
                    html_out += f'<div style="padding-left: 12px; margin-top: 4px;">{_esc(item)}</div>'
                html_out += '</div>'
            else:
                html_out += f"""<div style="margin: 4px 0 8px 0; padding: 6px 10px; background: #f0f4ff; border-left: 3px solid #4c51bf; border-radius: 0 4px 4px 0; color: #2d3748;"><b>📘 方法解释：</b>{_esc(dme)}</div>"""

        summary = data.get("summary_and_distinction")
        if summary:
            html_out += f"""
            <div style="margin: 10px 0; padding: 8px 12px; background: #edf2f7; border: 1px solid #cbd5e0; border-radius: 6px;">
                <div style="font-weight: bold; color: #2d3748;">🔍 总结与辨析：</div>
                <div style="margin-top: 4px; color: #4a5568;">{_esc(summary)}</div>
            </div>
            """
        else:
            html_out += """
            <div style="margin: 10px 0; padding: 8px 12px; background: #fff5f5; border: 1px solid #feb2b2; border-radius: 6px;">
                <div style="font-weight: bold; color: #c53030;">⚠️ 总结与辨析缺失</div>
                <div style="margin-top: 4px; color: #4a5568;">该词条未提供多学科对比分析，建议后续补充。</div>
            </div>
            """

        if data.get("hypernym"):
            html_out += f"""<div style="margin: 4px 0;"><b>⬆️ 上位概念：</b>{_esc(data.get('hypernym'))}</div>"""
        if data.get("hyponyms"):
            hyps = data.get("hyponyms")
            if isinstance(hyps, list):
                hyps_text = "、".join(str(h) for h in hyps)
            else:
                hyps_text = str(hyps)
            html_out += f"""<div style="margin: 4px 0;"><b>⬇️ 下位概念：</b>{_esc(hyps_text)}</div>"""

    elif query_type == "chinese_sentence":
        # 中文语句：语体 → 句意 → 结构 → 修辞 → 改写 → 用法（非翻译）
        if data.get("style"):
            html_out += f"""<div style="margin: 4px 0; padding: 6px 10px; background: #fefcbf; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;"><b>🎭 语体与语境：</b>{_esc(data.get('style'))}</div>"""

        if data.get("meaning"):
            html_out += f"""<div style="background: #f0fff4; border-left: 3px solid #38a169; padding: 8px 12px; margin: 8px 0; border-radius: 0 4px 4px 0;"><b>💡 句意解释：</b>{_esc(data.get('meaning'))}</div>"""

        structure = data.get("structure")
        if structure:
            items = structure if isinstance(structure, list) else [structure]
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2b6cb0;">🧩 知识结构：</div><ul style="margin: 4px 0 0 0; padding-left: 20px;">'
            for item in items:
                if isinstance(item, dict):
                    item = "：".join(str(v) for v in item.values() if v)
                html_out += f"<li>{_esc(item)}</li>"
            html_out += '</ul></div>'

        if data.get("rhetoric"):
            html_out += f"""<div style="margin: 8px 0; padding: 6px 10px; background: #faf5ff; border-left: 3px solid #805ad5; border-radius: 0 4px 4px 0;"><b>🎨 修辞与语气：</b>{_esc(data.get('rhetoric'))}</div>"""

        rewrite = data.get("rewrite")
        if rewrite:
            items = rewrite if isinstance(rewrite, list) else [rewrite]
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2c5282;">✍️ 改写与润色：</div>'
            for item in items:
                if isinstance(item, dict):
                    version = _esc(item.get('version'))
                    text = _esc(item.get('text'))
                    html_out += (f'<div style="margin: 4px 0 4px 8px; padding-left: 8px; border-left: 2px solid #4299e1;">'
                                 f'<div style="font-weight: bold; color: #4a5568; font-size: 0.9em;">{version}</div>'
                                 f'<div>{text}</div></div>')
                else:
                    html_out += f'<div style="margin: 4px 0 4px 8px;">{_esc(item)}</div>'
            html_out += '</div>'

        if data.get("usage_tips"):
            html_out += f"""<div style="margin: 10px 0; padding: 8px 12px; background: #edf2f7; border: 1px solid #cbd5e0; border-radius: 6px;"><b>📌 用法提示：</b>{_esc(data.get('usage_tips'))}</div>"""

    elif query_type == "chinese_question":
        # 中文疑问句：直接回答 → 关键概念 → 依据与推导 → 易错点 → 追问 → 领域语体
        if data.get("direct_answer"):
            html_out += f"""<div style="background: #ebf8ff; border-left: 3px solid #2b6cb0; padding: 10px 12px; margin: 8px 0; border-radius: 0 4px 4px 0; font-size: 1.05em;"><b>✅ 直接回答：</b>{_esc(data.get('direct_answer'))}</div>"""
        else:
            html_out += """
            <div style="margin: 8px 0; padding: 8px 12px; background: #fff5f5; border: 1px solid #feb2b2; border-radius: 6px;">
                <div style="font-weight: bold; color: #c53030;">⚠️ 缺少直接回答</div>
                <div style="margin-top: 4px; color: #4a5568;">该问句未给出明确答案，建议重新查询。</div>
            </div>
            """
        kcs = data.get("key_concepts")
        if kcs and isinstance(kcs, list) and len(kcs) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2c5282;">📚 关键概念：</div>'
            for kc in kcs:
                if isinstance(kc, dict):
                    html_out += (f'<div style="margin: 3px 0 3px 8px;">'
                                 f'<b style="color: #2b6cb0;">{_esc(kc.get("term"))}</b> '
                                 f'<span style="color: #4a5568;">{_esc(kc.get("explanation"))}</span></div>')
                else:
                    html_out += f'<div style="margin: 3px 0 3px 8px;">{_esc(kc)}</div>'
            html_out += '</div>'
        reasoning = data.get("reasoning")
        if reasoning:
            items = reasoning if isinstance(reasoning, list) else [reasoning]
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2b6cb0;">🔗 依据与推导：</div><ul style="margin: 4px 0 0 0; padding-left: 20px;">'
            for item in items:
                html_out += f"<li>{_esc(item)}</li>"
            html_out += '</ul></div>'
        if data.get("caveats"):
            html_out += f"""<div style="margin: 8px 0; padding: 6px 10px; background: #fffaf0; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;"><b>⚠️ 易错点：</b>{_esc(data.get('caveats'))}</div>"""
        fus = data.get("followups")
        if fus:
            items = fus if isinstance(fus, list) else [fus]
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2f855a;">➡️ 可继续追问：</div><ul style="margin: 4px 0 0 0; padding-left: 20px;">'
            for item in items:
                html_out += f"<li>{_esc(item)}</li>"
            html_out += '</ul></div>'
        if data.get("style"):
            html_out += f"""<div style="margin: 4px 0; padding: 6px 10px; background: #fefcbf; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;"><b>📍 领域与语体：</b>{_esc(data.get('style'))}</div>"""

    elif query_type == "chinese_paragraph":
        # 中文段落：主旨 → 逐句要点 → 句间逻辑 → 概念表 → 要点与易错 → 领域语体
        if data.get("gist"):
            html_out += f"""<div style="background: #ebf8ff; border-left: 3px solid #2b6cb0; padding: 10px 12px; margin: 8px 0; border-radius: 0 4px 4px 0; font-size: 1.05em;"><b>📌 段落主旨：</b>{_esc(data.get('gist'))}</div>"""
        else:
            html_out += """
            <div style="margin: 8px 0; padding: 8px 12px; background: #fff5f5; border: 1px solid #feb2b2; border-radius: 6px;">
                <div style="font-weight: bold; color: #c53030;">⚠️ 缺少段落主旨</div>
                <div style="margin-top: 4px; color: #4a5568;">该段落未给出主旨概括，建议重新查询。</div>
            </div>
            """
        smap = data.get("sentence_map")
        if smap and isinstance(smap, list) and len(smap) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2c5282;">🧩 逐句要点：</div>'
            for item in smap[:6]:
                if isinstance(item, dict):
                    html_out += (f'<div style="margin: 4px 0 4px 8px; padding-left: 8px; border-left: 2px solid #4299e1;">'
                                 f'<div style="color: #4a5568; font-size: 0.92em;">{_esc(item.get("sentence"))}</div>'
                                 f'<div><b>{_esc(item.get("point"))}</b></div></div>')
                else:
                    html_out += f'<div style="margin: 4px 0 4px 8px;">{_esc(item)}</div>'
            html_out += '</div>'
        logic = data.get("logic")
        if logic:
            items = logic if isinstance(logic, list) else [logic]
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2b6cb0;">🔗 句间逻辑：</div><ul style="margin: 4px 0 0 0; padding-left: 20px;">'
            for item in items:
                html_out += f"<li>{_esc(item)}</li>"
            html_out += '</ul></div>'
        terms = data.get("terms")
        if terms and isinstance(terms, list) and len(terms) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2c5282;">📚 关键概念：</div>'
            for t in terms:
                if isinstance(t, dict):
                    html_out += (f'<div style="margin: 3px 0 3px 8px;">'
                                 f'<b style="color: #2b6cb0;">{_esc(t.get("term"))}</b> '
                                 f'<span style="color: #4a5568;">{_esc(t.get("explanation"))}</span></div>')
                else:
                    html_out += f'<div style="margin: 3px 0 3px 8px;">{_esc(t)}</div>'
            html_out += '</div>'
        tks = data.get("takeaways")
        if tks:
            items = tks if isinstance(tks, list) else [tks]
            html_out += '<div style="margin: 10px 0; padding: 8px 12px; background: #f0fff4; border: 1px solid #c6f6d5; border-radius: 6px;"><div style="font-weight: bold; color: #2f855a;">✅ 要点与易错：</div><ul style="margin: 4px 0 0 0; padding-left: 20px;">'
            for item in items:
                html_out += f"<li>{_esc(item)}</li>"
            html_out += '</ul></div>'
        if data.get("style"):
            html_out += f"""<div style="margin: 4px 0; padding: 6px 10px; background: #fefcbf; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;"><b>📍 领域与语体：</b>{_esc(data.get('style'))}</div>"""

    elif query_type == "latex":
        # 公式本体已在头部标题渲染，此处按 读法 → 类型 → 含义 → 结构 → 符号 → 条件 → 应用 → 例子 输出
        read_as = data.get("read_as")
        kind = data.get("kind")
        if read_as or kind:
            html_out += '<div style="margin: 4px 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;">'
            if read_as:
                html_out += f"""<span style="color: #4a5568; font-size: 0.95em;">🗣️ 读作：{latex_to_html(read_as)}</span>"""
            if kind:
                html_out += f"""<span style="background: #ebf8ff; color: #2b6cb0; padding: 2px 8px; border-radius: 10px; font-size: 0.82em; font-weight: bold; white-space: nowrap;">{_esc(kind)}</span>"""
            html_out += '</div>'
        if data.get("meaning"):
            html_out += f"""<div style="background: #f0fff4; border-left: 3px solid #38a169; padding: 8px 12px; margin-bottom: 8px; border-radius: 0 4px 4px 0;"><b>📐 公式含义：</b>{latex_to_html(data.get('meaning'))}</div>"""
        structure = data.get("structure")
        if structure and isinstance(structure, list) and len(structure) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2c5282;">🧩 结构拆解：</div>'
            for st in structure:
                if not isinstance(st, dict):
                    html_out += f"""<div style="margin: 3px 0 3px 8px;">{latex_to_html(st)}</div>"""
                    continue
                role = st.get('role')
                role_html = f"""<b style="color: #2c5282;">{_esc(role)}</b> """ if role else ""
                html_out += f"""<div style="margin: 3px 0 3px 8px;"><code style="font-family: 'Courier New', monospace; background: #edf2f7; padding: 1px 5px; border-radius: 3px; white-space: pre-wrap;">{latex_to_html(st.get('part'))}</code> {role_html}<span style="color: #4a5568;">{latex_to_html(st.get('note'))}</span></div>"""
            html_out += '</div>'
        symbols = data.get("symbols")
        if symbols and isinstance(symbols, list) and len(symbols) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #2c5282;">🔣 符号说明：</div>'
            for s in symbols:
                if isinstance(s, dict):
                    explanation = s.get('explanation') or s.get('meaning') or ""
                    html_out += f"""<div style="margin: 3px 0 3px 8px;"><code style="font-family: 'Courier New', monospace; background: #edf2f7; padding: 1px 5px; border-radius: 3px; white-space: pre-wrap;">{latex_to_html(s.get('symbol'))}</code> <span style="color: #4a5568;">{latex_to_html(explanation)}</span></div>"""
                else:
                    html_out += f"""<div style="margin-left: 8px;">{latex_to_html(s)}</div>"""
            html_out += '</div>'
        if data.get("conditions"):
            html_out += f"""<div style="margin: 8px 0; padding: 6px 10px; background: #fff5f5; border-left: 3px solid #e53e3e; border-radius: 0 4px 4px 0;"><b>⚠️ 成立条件：</b>{latex_to_html(data.get('conditions'))}</div>"""
        application = data.get("application")
        if application:
            html_out += '<div style="margin: 8px 0; padding: 6px 10px; background: #fefcbf; border-left: 3px solid #d69e2e; border-radius: 0 4px 4px 0;"><b>🎯 应用场景：</b>'
            for a in (application if isinstance(application, list) else [application]):
                if a:
                    html_out += f"""<div style="margin: 2px 0;">• {latex_to_html(a)}</div>"""
            html_out += '</div>'
        usage = data.get("usage")
        if usage:
            items = usage if isinstance(usage, list) else [usage]
            html_out += ('<div style="margin: 8px 0; padding: 6px 10px; background: #f0fff4; '
                         'border-left: 3px solid #38a169; border-radius: 0 4px 4px 0;">'
                         '<b>🧮 怎么用：</b><ol style="margin: 4px 0 0 0; padding-left: 22px;">')
            for u in items:
                if isinstance(u, str) and u.strip():
                    html_out += f"<li>{latex_to_html(u)}</li>"
            html_out += '</ol></div>'
        rel = data.get("related")
        if rel:
            items = rel if isinstance(rel, list) else [rel]
            html_out += ('<div style="margin: 8px 0;"><div style="font-weight: bold; '
                         'color: #2b6cb0;">🔗 相关与延伸：</div>'
                         '<ul style="margin: 4px 0 0 0; padding-left: 20px;">')
            for r in items:
                if r:
                    html_out += f"<li>{latex_to_html(r)}</li>"
            html_out += '</ul></div>'

    elif query_type == "code":
        # 代码原文：优先用模型返回的 code 字段，兜底用用户原始输入（保证不丢代码）
        code_text = data.get("code") or raw_query
        if not isinstance(code_text, str):
            code_text = raw_query
        html_out += f"""
        <div style="margin: 8px 0; padding: 10px 12px; background: #1a202c; color: #e2e8f0; border-radius: 6px; overflow-x: auto;">
            <pre style="margin: 0; font-family: 'Courier New', monospace; font-size: 0.9em; line-height: 1.5; white-space: pre-wrap; word-break: break-all;">{_esc(code_text)}</pre>
        </div>"""
        if data.get("summary"):
            html_out += f"""
            <div style="background: #f7fafc; border-left: 3px solid #4299e1; padding: 8px 12px; margin-bottom: 10px; border-radius: 0 4px 4px 0;">
                <b>💡 简要解释：</b>{_esc(data.get('summary'))}
            </div>
            """
        if data.get("language"):
            html_out += f"""<div style="margin: 4px 0;"><b>🔧 语言：</b>{_esc(data.get('language'))}</div>"""
        expl = data.get("explanation")
        if expl and isinstance(expl, list) and len(expl) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold;">📖 逐段解释：</div>'
            for item in expl:
                if isinstance(item, dict):
                    html_out += f"""<div style="margin: 4px 0 4px 8px;"><code style="font-family: 'Courier New', monospace; background: #edf2f7; padding: 1px 5px; border-radius: 3px;">{_esc(item.get('part'))}</code> <span style="color: #4a5568;">{_esc(item.get('note'))}</span></div>"""
                else:
                    html_out += f"""<div style="margin-left: 8px;">{_esc(item)}</div>"""
            html_out += '</div>'
        kps = data.get("key_points")
        if kps and isinstance(kps, list) and len(kps) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold;">📌 核心要点：</div><ul style="margin: 0; padding-left: 20px;">'
            for kp in kps:
                html_out += f"<li>{_esc(kp)}</li>"
            html_out += '</ul></div>'
        pits = data.get("pitfalls")
        if pits and isinstance(pits, list) and len(pits) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #c53030;">⚠️ 常见坑：</div><ul style="margin: 0; padding-left: 20px;">'
            for p in pits:
                html_out += f"<li>{_esc(p)}</li>"
            html_out += '</ul></div>'

    elif query_type == "general":
        if data.get("summary"):
            html_out += f"""
            <div style="background: #f7fafc; border-left: 3px solid #4299e1; padding: 8px 12px; margin-bottom: 10px; border-radius: 0 4px 4px 0;">
                <b>💡 简要解释：</b>{_esc(data.get('summary'))}
            </div>
            """
        else:
            html_out += """
            <div style="background: #fff5f5; border-left: 3px solid #fc8181; padding: 8px 12px; margin-bottom: 10px; border-radius: 0 4px 4px 0;">
                <b>⚠️ 摘要缺失</b>
            </div>
            """
        key_points = data.get("key_points")
        if key_points and isinstance(key_points, list) and len(key_points) > 0:
            html_out += '<div style="margin: 10px 0;"><div style="font-weight: bold;">📌 核心要点：</div><ul style="margin: 0; padding-left: 20px;">'
            for kp in key_points:
                html_out += f"<li>{_esc(kp)}</li>"
            html_out += '</ul></div>'
        else:
            html_out += """
            <div style="margin: 10px 0; padding: 8px 12px; background: #fff5f5; border: 1px solid #feb2b2; border-radius: 6px;">
                <div style="font-weight: bold; color: #c53030;">⚠️ 核心要点缺失</div>
                <div style="margin-top: 4px; color: #4a5568;">模型返回的内容中缺少关键点，请尝试重新查询或检查网络。</div>
            </div>
            """

    if query_type == "general":
        pitfalls = data.get("pitfalls")
        if pitfalls and isinstance(pitfalls, list) and len(pitfalls) > 0:
            html_out += '<div style="margin: 8px 0;"><div style="font-weight: bold; color: #c53030;">⚠️ 常见坑：</div><ul style="margin: 0; padding-left: 20px;">'
            for p in pitfalls:
                html_out += f"<li>{_esc(p)}</li>"
            html_out += '</ul></div>'

    if query_type != "latex" and data.get("examples"):
        exs = data.get("examples")
        if isinstance(exs, list) and len(exs) > 0:
            icon = "💬 例句" if query_type in ("english_word", "english_phrase", "chinese") else "💻 示例"
            html_out += f"""
            <div style="margin-top: 10px;">
                <div style="font-weight: bold; color: #2f855a;">{icon}：</div>
            """
            for ex in exs:
                if not isinstance(ex, dict):
                    continue
                en_text = ex.get('en', '')
                cn_text = ex.get('cn', '')
                en_str = str(en_text) if en_text is not None else ''
                is_code = "\n" in en_str or "def " in en_str or "function" in en_str
                style = "font-family: monospace; background: #f7fafc; padding: 4px 6px; border-radius: 4px;" if is_code else ""
                html_out += f"""
                <div style="margin: 4px 0 4px 8px; border-left: 2px solid #38a169; padding-left: 8px;">
                    <div style="{style}">{_esc(en_text)}</div>
                    <div style="color: #718096; font-size: 0.9em;">{_esc(cn_text)}</div>
                </div>
                """
            html_out += "</div>"
        else:
            html_out += """
            <div style="margin-top: 10px; padding: 8px; background: #f7fafc; border-radius: 4px;">
                <div style="font-weight: bold; color: #2f855a;">💬 例句：</div>
                <div style="color: #718096;">（暂无示例）</div>
            </div>
            """

    if query_type == "english_word" and data.get("memory_tip"):
        html_out += f"""<div style="margin: 10px 0; padding: 8px 12px; background: #fffaf0; border: 1px solid #feebc8; border-radius: 6px;"><b>🧠 记忆钩子：</b><span style="color: #744210;">{_esc(data.get('memory_tip'))}</span></div>"""

    html_out += "</div>"
    return html_out


def render_html(data, raw_query):
    """渲染正文 + 底部搜索外链（外链由 GoldenDict 交给系统默认浏览器打开）。"""
    return _render_html_inner(data, raw_query) + _search_links(raw_query)


# =======================================================
# 内置自测（--selftest）：验证分类器路由，不调用 API
# =======================================================

PROMPT_NAMES = {
    SYSTEM_PROMPT_EN_WORD: 'english_word',
    SYSTEM_PROMPT_EN_PHRASE: 'english_phrase',
    SYSTEM_PROMPT_CHINESE: 'chinese',
    SYSTEM_PROMPT_CHINESE_SENTENCE: 'chinese_sentence',
    SYSTEM_PROMPT_CHINESE_QUESTION: 'chinese_question',
    SYSTEM_PROMPT_CHINESE_PARAGRAPH: 'chinese_paragraph',
    SYSTEM_PROMPT_LATEX: 'latex',
    SYSTEM_PROMPT_CODE: 'code',
    SYSTEM_PROMPT_GENERAL: 'general',
}


def _prompt_label(payload):
    """把决策载荷转成可读名字。

    精确命中 PROMPT_NAMES 直接返回；否则按"最长前缀"回退到主 prompt 名 ——
    中文分支的载荷是"主 prompt + 子类型附加指令"的变体，若不做回退，
    自测与 --explain 会把整段 prompt 当成分类名打印/比较。
    """
    name = PROMPT_NAMES.get(payload)
    if name:
        return name
    if not isinstance(payload, str):
        return payload
    best = None
    for base, base_name in PROMPT_NAMES.items():
        if payload.startswith(base) and (best is None or len(base) > len(best)):
            best = base
            name = base_name
    return name if best is not None else payload

# (输入, 期望 action, 期望分类名)
SELFTEST_CASES = [
    # 拦截：空/URL/邮箱/路径/文件名
    ("", 'block', 'empty'),
    ("https://example.com", 'block', 'url'),
    ("foo@bar.com", 'block', 'email'),
    ("C:\\Windows\\System32", 'block', 'drive'),
    ("/usr/bin/python", 'block', 'path'),
    ("~/docs", 'block', 'path'),
    ("./script.sh", 'block', 'path'),
    ("../main.py", 'block', 'path'),
    ("config.py", 'block', 'filename'),
    ("install.sh", 'block', 'filename'),
    # LaTeX 公式/命令
    ("\\frac{1}{2}", 'query', 'latex'),
    ("$E=mc^2$", 'query', 'latex'),
    ("\\alpha", 'query', 'latex'),
    ("\\begin{matrix}1&2\\end{matrix}", 'query', 'latex'),
    ("\\text{平均值}", 'query', 'latex'),             # 中文字符少且无标点 → 仍走 LaTeX
    ("$5", 'query', 'general'),          # 价格不判 LaTeX
    ("E=mc^2", 'query', 'general'),      # 无 LaTeX 守卫 → 代码
    ("当x→0时，\\frac{1}{x}趋于无穷", 'query', 'chinese'),  # 中文主导（逗号）→ 交还中文分支
    # 英文单词（合法撇号/连字符）
    ("don't", 'query', 'english_word'),
    ("e-mail", 'query', 'english_word'),
    ("mother-in-law", 'query', 'english_word'),
    ("strengths", 'query', 'english_word'),
    ("pneumonoultramicroscopicsilicovolcanoconiosis", 'block', 'word_too_long'),  # 45 > 15
    ("tetrafluoroethylene", 'block', 'word_too_long'),  # 20 > 15 化学名
    # 标识符 / 伪词
    ("getElementById", 'query', 'general'),
    ("foo_bar", 'query', 'general'),
    ("aaaaaaa", 'block', 'fake_word'),
    ("bcdfghjklmnpqrstvwxz", 'block', 'fake_word'),
    # 英文短语 vs 句子
    ("break the ice", 'query', 'english_phrase'),
    ("in the", 'query', 'english_phrase'),
    ("How are you?", 'block', 'sentence'),
    ("break the ice.", 'block', 'sentence'),
    # 代码 / CLI 命令
    ("git push", 'query', 'general'),
    ("pip install numpy", 'query', 'general'),
    ("foo()", 'query', 'general'),
    ("a&&b", 'query', 'general'),
    ("--help", 'query', 'general'),
    ("C++", 'query', 'general'),
    ("sed -i", 'query', 'general'),
    ("hello, world", 'query', 'general'),
    ("x=" + "abcdefgh" * 20, 'query', 'code'),      # 单行长代码 160 > 80 → 长文本代码块
    # 多行代码块（含中文注释不被中文分支误拦；允许较长）
    ("def foo():\n    # 计算平均值\n    return sum(x)/len(x)", 'query', 'code'),
    ("import numpy as np\nnp.array([1,2,3])", 'query', 'code'),
    ("x=1\n" + " y=abcdefghijklmnopqrstuvwxyz" * 40, 'block', 'too_long'),  # 代码块字母 1081 > 1000
    ("Hello world.\nHow are you today?", 'block', 'too_long'),  # 多行文章（无代码特征）→ 混合拦截
    # 自然语言不被误判为代码
    ("100%", 'query', 'general'),
    ("a & b", 'query', 'general'),
    ("R&D", 'query', 'general'),
    ("e.g.", 'query', 'general'),
    ("U.S.A", 'query', 'general'),
    ("3.14", 'query', 'general'),
    # 中文术语：纯中文 ≤20 / 中英夹杂 ≤30（符号不计入有效字符；阈值可用环境变量覆盖）
    ("为什么天空是蓝色的", 'query', 'chinese'),          # 纯中文 9 ≤20
    ("机器学习", 'query', 'chinese'),
    ("HTTP协议", 'query', 'chinese'),
    ("Python环境配置指南", 'query', 'chinese'),          # 中英夹杂 12 ≤30
    ("中华人民共和国成立七十周年", 'query', 'chinese'),   # 纯中文 13 ≤20
    ("OpenAI最新发布的GPT4模型功能详解", 'query', 'chinese'),  # 中英夹杂 20 ≤30（数字不计入有效字符）
    ("HTTP协议 & RESTful接口", 'query', 'chinese'),      # 含符号仍判术语（符号不计入有效字符）
    ("HTTP协议；RESTful接口", 'query', 'chinese'),       # 中间的 ； 不判句解（句解只认结尾的 。；！…）
    ("这是一段明显超过二十个有效字符上限的纯中文术语内容", 'block', 'chinese_term_too_long'),   # 纯中文 25 > 20
    ("Python编程语言在人工智能领域应用非常广泛并且深受开发者喜爱", 'block', 'chinese_term_too_long'),  # 中英夹杂 32 > 30
    # 中文语句：含 。！… 或以 。；！… 结尾 → 句解路由（不再静默拦截）
    ("深度学习是一种机器学习方法。", 'query', 'chinese_sentence'),
    ("深度学习是一种机器学习方法；", 'query', 'chinese_sentence'),   # 新增：； 结尾也判句解
    ("这是一段尚未结束的中文分句；", 'query', 'chinese_sentence'),
    ("你好。", 'query', 'chinese_sentence'),
    ("甲；", 'query', 'chinese'),                        # 有效字符 1 < CN_SENTENCE_MIN_LEN → 仍是术语
    ("中华人民共和国成立七十周年了，这是一个伟大的时刻。", 'query', 'chinese_sentence'),
    ("在人工智能技术飞速发展的今天，机器学习与深度学习已经广泛应用于图像识别、自然语言处理以及语音识别等诸多领域，并且仍在持续快速演进。", 'query', 'chinese_sentence'),  # 61 ≤ 120
    # 中文疑问句：全角 ？ 结尾 → 独立答疑路由（半角 ? 不参与判定）
    ("为什么天空是蓝色的？", 'query', 'chinese_question'),
    ("什么是深度学习？", 'query', 'chinese_question'),
    ("深度学习为什么有效？", 'query', 'chinese_question'),
    ("你好?", 'query', 'chinese'),                      # 半角 ? 不判疑问 → 按术语解析
    ("什么是X？它与Y的区别", 'query', 'chinese'),        # ？ 不在结尾 → 不判疑问（无句末标点 → 术语）
    # 学术句：括号内英文对译与 [n] 引注都不算代码信号（含 。 → 中文语句）
    ("这类映射是只有一个临界点的区间映射，其经典例子包括逻辑映射（Logistic map）和正弦映射（Sine map），是研究混沌动力学和分岔现象的典型模型 [2] [4-6]。", 'query', 'chinese_sentence'),  # 69 ≤ 100
    # >80 有效字符、仅靠 [n] 引注构成"强符号" → 仍必须走中文语句（锁住 long_text_code 否决）
    ("这类映射是只有一个临界点的区间映射（a unimodal interval map），其经典例子包括逻辑映射（Logistic map）和正弦映射（Sine map），是研究混沌动力学和分岔现象的典型模型 [2] [4-6]。", 'query', 'chinese_sentence'),
    # 多行中文摘要含 [n] 引注不被 multiline_code_symbol 抢走
    ("这类映射是只有一个临界点的区间映射。\n其经典例子包括逻辑映射和正弦映射 [2] [4-6]。", 'query', 'chinese_sentence'),
    # 含 = 的中文公式句：汉字数 ≥ 英文字母数 → 仍判中文语句
    ("逻辑斯谛映射的迭代式 x_{n+1} = r·x_n(1−x_n) 在参数 r 增大时呈现倍周期分岔。", 'query', 'chinese_sentence'),
    # 反向保险：含真实方括号索引/赋值的长代码仍判代码
    ("nums = [1, 2, 3]\ntotal = sum(nums)\nprint(total)", 'query', 'code'),
    # 拦截与识别交叉的冲突规避（同一特征只服务一条判定路径）
    ("\\usr\\bin", 'block', 'path'),             # 反斜杠路径 → L1 硬否决（非 LaTeX、非代码）
    ("price $5 and $10", 'query', 'general'),    # 含空格价格 → 非 LaTeX
    ("$PATH", 'query', 'general'),               # shell 变量（$ 无配对）→ 非 LaTeX
    ("#include", 'query', 'general'),            # C 预处理器（# 不判代码）
    ("@user", 'query', 'general'),               # 社交提及（@ 不判代码）
    ("and/or", 'query', 'general'),              # 自然语言斜杠（/ 不判代码）
    ("python", 'query', 'english_word'),         # 语言名单词走单词分类
    ("foo bar", 'query', 'english_phrase'),      # 两词纯字母短语
    ("v1.2", 'query', 'general'),                # 版本号（句点规则 → 通用）
    ("你好.txt", 'block', 'filename'),           # 中文文件名
    ("/tmp/文件", 'block', 'path'),              # 中文路径
    # 反斜杠路径（UNC 双反斜杠 / 单反斜杠）不误判 LaTeX，统一按路径拦截
    ("\\\\server\\share\\folder", 'block', 'path'),
    # 图片等扩展名（扩充后拦截）
    ("my.photo.jpg", 'block', 'filename'),
    ("photo.jpg", 'block', 'filename'),
    # 代码块：shell 命令块 / 首行命令
    ("echo hello\necho world", 'query', 'code'),
    ("cd /tmp\nmkdir -p test", 'query', 'code'),
    ("echo hello", 'query', 'general'),           # 单行 CLI 命令
    ("cd project", 'query', 'general'),
    # CLI 虚词守卫：自然语言短语不误判命令
    ("sort out", 'query', 'english_phrase'),
    ("cat and dog", 'query', 'english_phrase'),
    ("Clear the table\nSet the chairs", 'block', 'too_long'),  # 多行句子不判代码块
    # 中英文混合代码块（用户反馈）：赋值/配置、SQL、HTML
    ("username = \"张三\"\npassword = \"123\"\nserver = \"192.168.1.1\"", 'query', 'code'),
    ("SELECT name, age\nFROM users\nWHERE city = '北京'", 'query', 'code'),
    ("<div>标题</div>\n<p>内容</p>", 'query', 'code'),
    # 源码字符串字面量（含字面量 \n 转义，VSCode 复制）不再误判 LaTeX
    ("username = \"张三\"\\npassword = \"123\"\\nserver = \"192.168.1.1\"", 'query', 'code'),
    ("SELECT name, age\\nFROM users\\nWHERE city = '北京'", 'query', 'code'),
    # 长文本代码优先：>80 非 LaTeX → 代码块（剪贴板取词场景，单行化代码不再被 sentence 误拦）
    ("def _latex_frac_repl(m):     \"\"\"分式 \\\\frac{n}{d} → CSS 上下堆叠（分子 n 在上、分母 d 在下）。\"\"\"     num = m.group(1)     den = m.group(2)     inner = (f'<span>{num}</span>' + f'<span>{den}</span>')     return f'<span style=\"margin:0\">{inner}</span>'", 'query', 'code'),
    # 长文本（>80 有效字符、无代码信号）不再走代码块 → 交给句长/长度规则拦截
    ("This is a very long English paragraph written to verify that long natural language text is no longer routed to the code branch.", 'block', 'sentence'),
    (("alpha " * 17).strip(), 'block', 'phrase_too_long'),   # 85 字母 / 17 词，无代码信号
    ("深" * 81, 'block', 'chinese_term_too_long'),           # 81 中文，无句末标点
    ("深" * 81 + "。", 'query', 'chinese_sentence'),          # 81 ≤ 120（阈值放松的代价：畸形长输入会真实调用 API）
    ("深" * 120 + "。", 'query', 'chinese_sentence'),         # 120 = 句解上限，仍放行
    ("深" * 121 + "。", 'query', 'chinese_paragraph'),          # 121 > 120 → 升格为段落（不再静默拦截）
    ("深" * 121, 'block', 'chinese_term_too_long'),             # 121 且不以句号收尾 → 不路由（不发请求）
    # 半截划选（去掉句末句号）→ 一律不路由，避免白白发起 API 请求
    ("在人工智能技术飞速发展的今天，机器学习与深度学习已经广泛应用于图像识别、自然语言处理以及语音识别等诸多领域，并且仍在持续快速演进",
     'block', 'chinese_term_too_long'),
    ("深" * 121 + "？", 'block', 'chinese_question_too_long'),  # 疑问句单独限长
    # 中文段落：≥2 个句末标点且 >120 有效字符 → 段落路由
    # （方案 A：只接管原本会被静默拦截的输入，≤120 的多句文本仍走句解）
    ("深度学习是一种机器学习方法。它通过多层网络拟合数据。", 'query', 'chinese_sentence'),  # 25 ≤120 → 仍是句解
    ("深" * 121 + "。" + "深" * 3 + "。", 'query', 'chinese_paragraph'),      # 124 >120 且 2 个句末标点
    ("深" * 300 + "。" + "深" * 300 + "。", 'query', 'chinese_paragraph'),    # 600 = 段落上限，仍放行
    ("深" * 300 + "。" + "深" * 301 + "。", 'block', 'chinese_paragraph_too_long'),  # 601 > 600
    # 教材式长定义句：只有一个句号但长达 130 有效字符 → 段落（CN_PARA_MIN_PUNCT=1）
    ("电路分析（Circuit Analysis）是电气工程学科的基础分支，以线性、集总参数、时不变电路为研究对象，系统研究电路的基本规律和分析方法，涵盖直流电路分析、动态电路时域分析和正弦稳态电路相量分析三大核心模块，内容包括电路模型定律、等效变换、网络定理、一阶/二阶电路响应及三相电路等主题 [1-2] [4-6]。", 'query', 'chinese_paragraph'),
    # 带 [n] 引注、不以句号收尾的中文段落：引注不是代码索引，不得判成代码块
    ("电路理论作为一门独立的学科已有约200多年的历史，其发展分为经典电路理论、近代电路理论和电路与系统理论三个阶段 [7]。电路分析为模拟电路、数字系统与逻辑设计、信号与系统等后续专业领域提供必要的电路理论基础和分析方法支撑 [9] [13]，是电气与电子信息类学科的重要技术基础",
     'block', 'chinese_term_too_long'),
    # 同一段补上句末句号 → 116 ≤ 120，走句解（段落要 >120 才升格）
    ("电路理论作为一门独立的学科已有约200多年的历史，其发展分为经典电路理论、近代电路理论和电路与系统理论三个阶段 [7]。电路分析为模拟电路、数字系统与逻辑设计、信号与系统等后续专业领域提供必要的电路理论基础和分析方法支撑 [9] [13]，是电气与电子信息类学科的重要技术基础。",
     'query', 'chinese_sentence'),
    # 同一段去掉句末句号 → 视为半截划选，不路由（不发请求）
    ("电路分析（Circuit Analysis）是电气工程学科的基础分支，以线性、集总参数、时不变电路为研究对象，系统研究电路的基本规律和分析方法，涵盖直流电路分析、动态电路时域分析和正弦稳态电路相量分析三大核心模块，内容包括电路模型定律、等效变换、网络定理、一阶/二阶电路响应及三相电路等主题 [1-2] [4-6]", 'block', 'chinese_term_too_long'),
    # 长段落夹带 [n] 引注与英文对译：不被 long_text_code 抢走（锁住 cn_nl_route 让位）
    ("这类映射是只有一个临界点的区间映射，其经典例子包括逻辑映射（Logistic map）和正弦映射（Sine map），是研究混沌动力学和分岔现象的典型模型 [2] [4-6]。研究者常用它演示倍周期分岔与混沌的产生过程，也常把它作为入门教材里的第一个例子。对一般读者而言，最直观的理解方式仍然是观察不同参数下的分岔图。", 'query', 'chinese_paragraph'),
    # ？ 结尾优先于段落：多句段落以 ？ 结尾时仍按问句处理
    ("深度学习是机器学习的分支，它通过多层网络拟合数据，那为什么它有效？", 'query', 'chinese_question'),
    ("深" * 120 + "？", 'query', 'chinese_question'),           # 120 = 疑问上限，仍放行
    # 首词像命令但整体是英文整句 → 仍让位 english_sentence 拦截（cli_command veto）
    ("find duplicate files in this directory.", 'block', 'sentence'),
    # URL 整串才拦：代码内嵌 URL / 带前缀文本不误拦
    ("https://example.com/page?q=1&x=2", 'block', 'url'),
    ("url = \"https://api.example.com\"", 'query', 'general'),
    # 函数调用含文件名不误拦（filename 需无代码外壳）
    ("load(\"config.json\")", 'query', 'general'),
    # 含空格但带数学符号的 $...$ 仍判 LaTeX
    ("$E = mc^2$", 'query', 'latex'),
    # LaTeX 命令不回归（\n \t 开头的合法命令仍判公式）
    ("\\alpha", 'query', 'latex'),
    ("\\neq", 'query', 'latex'),
    ("\\text{平均值}", 'query', 'latex'),
    ("$\\text{平均值}$", 'query', 'latex'),          # $...$ 包裹且中文少 → 仍判 LaTeX
    # 多行含中文注释的代码不被中文分支截走
    ("含中文注释的代码：\n    print('你好')", 'query', 'code'),
]

# (输入 LaTeX, 渲染结果中必须包含的片段)
LATEX_RENDER_CASES = [
    (r'\psi_1', ['ψ', '<sub>1</sub>']),
    (r'\int_0^\infty', ['∫', '<sub>0</sub>', '<sup>∞</sup>']),
    (r'\frac{\frac{a}{b}}{c}', ['a', 'b', 'c', 'border-bottom']),
    (r'\mathbb{R}^{n}', ['ℝ', '<sup>n</sup>']),
    (r'\overline{x}', ['x', '̅']),
    (r'\langle\psi_1|E_0\rangle', ['⟨', '⟩', 'ψ', '<sub>1</sub>', '<sub>0</sub>']),
    (r'\top', ['⊤']),
    (r'\to', ['→']),
    (r'\sin x', ['sin']),
    (r'\left\{ x \right\}', ['{', '}']),
    (r'$E = mc^2$', ['<sup>2</sup>']),
    (r'\lim_{x \to 0}', ['lim', '<sub>', '→']),
    (r'a \neq b \leq c', ['≠', '≤']),
    (r'\sqrt{x^2}', ['√', '<sup>2</sup>']),
    # ===== 以下覆盖"原先会被静默破坏"的场景 =====
    # --- 装饰命令（旧版只剥命令、丢掉上划线）---
    (r'\bar{\psi}', ['ψ', '̄']),
    (r'\hat{H}', ['H', '̂']),
    (r'\tilde{f}', ['f', '̃']),
    (r'\dot{x}', ['x', '̇']),
    (r'\ddot{x}', ['x', '̈']),
    (r'\overline{AB}', ['text-decoration:overline', 'AB']),
    (r'\vec{v}', ['→', 'v', 'display:block']),
    (r'\bar\psi', ['ψ', '̄']),                     # 无花括号形式
    # --- Dirac 符号 ---
    (r'\ket{\psi}', ['|', 'ψ', '⟩']),
    (r'\bra{n}', ['⟨', 'n', '|']),
    (r'\braket{n|m}', ['⟨', 'n|m', '⟩']),
    (r'\ketbra{a}{b}', ['|a⟩⟨b|']),
    # --- 数学字体 ---
    (r'\mathcal{L}', ['ℒ']),
    (r'\mathfrak{sl}', ['𝔰', '𝔩']),
    (r'\mathbf{v}', ['<b>', 'v', '</b>']),
    (r'\texttt{ls}', ['<code>', 'ls', '</code>']),
    # --- 排版命令必须静默消失（旧版会露出 displaystyle / limits）---
    (r'\displaystyle\sum_{n=1}^{\infty}', ['∑', '<sub>n=1</sub>', '<sup>∞</sup>']),
    (r'\sum\limits_{i=1}^{n} a_i', ['∑', '<sub>i=1</sub>', '<sub>i</sub>']),
    # --- 环境：矩阵 / cases / aligned ---
    (r'\begin{pmatrix} a & b \\ c & d \end{pmatrix}', ['<table', 'a', 'b', 'c', 'd', '(']),
    (r'\begin{bmatrix} 1 \\ 2 \end{bmatrix}', ['<table', '[', ']', '1', '2']),
    (r'\begin{cases} x, & x>0 \\ -x, & x\le 0 \end{cases}', ['<table', '{', 'x', '≤']),
    (r'\begin{aligned} a &= b \\ c &= d \end{aligned}',
     ['<table', 'text-align:right', 'a', '= b']),
    # --- 根号 / 二项式 / 堆叠结构 ---
    (r'\sqrt[3]{x}', ['<sup>3</sup>', '√', 'x']),
    (r'\binom{n}{k}', ['(', ')', 'n', 'k']),
    (r'\overset{\text{def}}{=}', ['def', '=']),
    (r'\sum_{\substack{i<j \\ i,j\in S}} x_{ij}', ['∑', 'i&lt;j', '∈', '<sub>ij</sub>']),
    (r'\overbrace{a_1+\cdots+a_n}^{n\text{ terms}}', ['⏞', 'n terms', '⋯', '<sub>1</sub>']),
    (r'\xrightarrow{f}', ['⟶', 'f']),
    (r'\xrightarrow[g]{f}', ['⟶', 'f', 'g']),
    # --- 否定符 / 分隔符 / 取模 ---
    (r'A \not= B', ['≠']),
    (r'\not\in', ['∉']),
    (r'\Big( \frac{a}{b} \Big)', ['font-size', '(', ')', 'border-bottom']),
    (r'f(x) \equiv g(x) \pmod{n}', ['≡', '(mod n)']),
    # --- 用户原始失败公式（端到端）---
    (r'$J_{\mu}^{\alpha} = ig \sum_{i,j,\alpha} \bar{\psi}^{i\alpha} \gamma_{\mu}'
     r' \left( \frac{\lambda^{\alpha}}{2} \right)_{ij} \psi^{j\alpha}.$',
     ['<sub>μ</sub>', '<sup>α</sup>', '∑', 'ψ̄', 'γ', '<sub>ij</sub>']),
]


# (原始响应文本, 是否按 LaTeX 解析, 解析后应取到的 title)
# 模拟模型把 LaTeX 源码直出到 JSON 字符串、未做反斜杠翻倍的真实错误
JSON_REPAIR_CASES = [
    # 含 \s \l \, —— 非法转义，严格解析必然失败，靠修复救回
    (r'''{"query_type":"latex","title":"$y_k = \sum_{j=1}^{r} P_{kj}(x) e^{\lambda_j x}, \, (k=1,\ldots,n).$","meaning":"通解"}''',
     True, r'$y_k = \sum_{j=1}^{r} P_{kj}(x) e^{\lambda_j x}, \, (k=1,\ldots,n).$'),
    (r'''{"query_type":"latex","title":"\psi_1","meaning":"基态"}''', True, r'\psi_1'),
    (r'''{"query_type":"latex","title":"\to","meaning":"趋于"}''', True, r'\to'),
    # \f 是合法 JSON 转义（换页符），会被静默吃掉 → 需激进模式救回
    (r'''{"query_type":"latex","title":"\frac{1}{2}","meaning":"一半"}''', True, r'\frac{1}{2}'),
    (r'''{"query_type":"latex","title":"\begin{matrix}a\end{matrix}","meaning":"矩阵"}''',
     True, r'\begin{matrix}a\end{matrix}'),
    # 代码块里的正则（非 LaTeX，只用安全模式）
    (r'''{"query_type":"code","code":"re.match(r'^\s+\S', ln)"}''', False, None),
    # 完全合法的响应必须原样通过，不能被修复逻辑破坏
    (r'''{"query_type":"code","code":"a\\nb","title":"含字面对反斜杠"}''', False, None),
    ('{"query_type":"english_word","title":"construct","phonetic":"/kənˈstrʌkt/"}', False, None),
    # 围栏 + 非法转义
    ('```json\n' + r'''{"query_type":"latex","title":"\alpha","meaning":"阿尔法"}''' + '\n```',
     True, r'\alpha'),
]


def run_selftest():
    """运行分类用例矩阵、LaTeX 渲染用例与 JSON 修复用例，打印 PASS/FAIL，退出码反映结果。"""
    failed = 0
    for query, exp_action, exp_name in SELFTEST_CASES:
        action, payload = classify_text(query)
        got_name = _prompt_label(payload) if action == 'query' else payload
        ok = (action == exp_action) and (got_name == exp_name)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {query!r:60s} -> {action}/{got_name}  (期望 {exp_action}/{exp_name})")
        if not ok:
            failed += 1
    total = len(SELFTEST_CASES)
    print(f"\n分类器：{total - failed}/{total} 通过")

    lf = 0
    for src, expects in LATEX_RENDER_CASES:
        out = latex_to_html(src)
        missing = [e for e in expects if e not in out]
        status = "PASS" if not missing else "FAIL"
        print(f"[{status}] {src!r:40s} -> {out!r}")
        if missing:
            print(f"        缺少：{missing}")
            lf += 1
    ltotal = len(LATEX_RENDER_CASES)
    print(f"\nLaTeX 渲染：{ltotal - lf}/{ltotal} 通过")

    jf = 0
    for src, is_latex, exp_title in JSON_REPAIR_CASES:
        data = _parse_json_response(src, latex=is_latex)
        if data is None:
            print(f"[FAIL] {src[:52]!r:56s} -> 解析失败")
            jf += 1
            continue
        got = data.get('title')
        if exp_title is None:
            print(f"[PASS] {src[:52]!r:56s} -> 解析成功")
        elif got == exp_title:
            print(f"[PASS] {src[:52]!r:56s} -> title 完全还原")
        else:
            print(f"[FAIL] {src[:52]!r:56s} -> title={got!r} 期望 {exp_title!r}")
            jf += 1
    jtotal = len(JSON_REPAIR_CASES)
    print(f"\nJSON 修复：{jtotal - jf}/{jtotal} 通过")

    sys.exit(1 if (failed or lf or jf) else 0)


# =======================================================
# 主入口
# =======================================================

USAGE = """用法：
  gd_llm.py <查询词>               查询单词/短语/代码/中文术语/中文语句/中文疑问句/中文段落/LaTeX 公式
  gd_llm.py <词1> <词2> ...        多个参数自动合并为一个查询
  gd_llm.py -                     从 stdin 读取查询内容
  gd_llm.py --selftest / -s       运行自测（分类器 + LaTeX 渲染，不调用 API）
  gd_llm.py --explain / -x <查询>  打印分类诊断（归一化/特征/规则命中与否决，不调用 API）
  gd_llm.py --cache-clear         清空结果缓存
  gd_llm.py --help / -h           显示本帮助

输出：HTML 片段（打印到 stdout），供外部弹窗/词典工具调用。

环境变量：
  GD_LLM_TIMEOUT       单次请求超时秒数（默认 60）
  GD_LLM_ATTEMPTS      总尝试次数（默认 2）
  GD_LLM_MAX_TOKENS    模型最大输出 token（默认 8192）
  GD_LLM_CACHE         设为 0 关闭缓存（默认开启）
  GD_LLM_CACHE_TTL     缓存有效期秒数（默认 2592000，即 30 天）
  GD_LLM_CACHE_DIR     缓存目录（默认 $XDG_CACHE_HOME/gd_llm 或 ~/.cache/gd_llm）
  GD_LLM_CACHE_MAX     缓存条目上限（默认 2000）
  GD_LLM_DEBUG         设为 1 时在错误页输出完整 traceback

长度与判定阈值（默认值见文件顶部常量区，改后拦截文案同步生效）：
  GD_LLM_CN_PURE_MAX       纯中文术语上限（默认 20）
  GD_LLM_CN_MIXED_MAX      中英夹杂术语上限（默认 30）
  GD_LLM_CN_SENT_MAX       中文语句上限（默认 120）
  GD_LLM_CN_Q_MAX          中文疑问句上限（默认 120）
  GD_LLM_CN_PARA_MAX       中文段落上限（默认 600）
  GD_LLM_CN_PARA_MIN_PUNCT 判为"多句段落"的最少句末标点（默认 2）
  GD_LLM_CN_SENT_MIN       以 。；！… 结尾判句解的最小有效字符（默认 4）
  GD_LLM_CN_PUNCT_END_ONLY 默认 1：句解与段落只认"以 。；！… 收尾"的输入，
                           半截文本不发起请求；设为 0 恢复"含 。！… 即判句解"
  GD_LLM_CN_DOMINANT_MIN   中文主导判定的汉字数阈值（默认 6）
  GD_LLM_CODE_MAX / GD_LLM_LONG_CODE_LEN / GD_LLM_CODE_BLOCK_MAX / GD_LLM_MIXED_MAX
                           代码类长度上限（默认 80 / 80 / 1000 / 16）

提示：多行查询请优先使用 stdin 模式（gd_llm.py -），
      否则换行会在命令行参数拼接中丢失，导致代码块识别失效。"""


def _read_query(argv):
    """解析命令行输入，返回查询字符串；无有效输入返回 None。"""
    args = argv[1:]
    if not args:
        # 无参数：若 stdin 有管道数据则读取（保持原"无参报错"行为兼容）
        if not sys.stdin.isatty():
            data = sys.stdin.read().strip()
            return data if data else None
        return None
    if args[0] in ('-h', '--help'):
        print(USAGE)
        sys.exit(0)
    if args[0] == '-':
        data = sys.stdin.read().strip()
        return data if data else None
    return ' '.join(args).strip() or None


if __name__ == "__main__":
    try:
        if any(a in ('--selftest', '-s') for a in sys.argv[1:]):
            run_selftest()

        if any(a == '--cache-clear' for a in sys.argv[1:]):
            print(f"已清除 {_cache_clear()} 条缓存：{CACHE_DIR}")
            sys.exit(0)

        # --explain：只打印分类诊断，不调用 API（在 _read_query 之前拦截 -x）
        explain_args = [a for a in sys.argv[1:] if a not in ('--explain', '-x')]
        if len(explain_args) != len(sys.argv[1:]):
            explain_query = ' '.join(explain_args).strip()
            if not explain_query and not sys.stdin.isatty():
                explain_query = sys.stdin.read().strip()
            if not explain_query:
                print(USAGE)
                sys.exit(0)
            print(explain_text(explain_query))
            sys.exit(0)

        raw_query = _read_query(sys.argv)
        if not raw_query:
            print("<div>错误：未收到查询词</div>")
            sys.exit(0)

        action, payload = classify_text(raw_query)

        if action == 'block':
            # 静默拦截：不输出任何内容（stdout 为空）→ Goldendict-ng 视为无结果，不弹窗
            sys.exit(0)

        json_res = query_llm(raw_query, payload)
        print(render_html(json_res, raw_query))
    except SystemExit:
        raise
    except Exception as e:
        detail = ''
        if DEBUG:
            detail = ("<pre style='white-space:pre-wrap;font-size:0.85em;margin-top:6px;'>"
                      + html.escape(traceback.format_exc()) + "</pre>")
        print(f"<div style='color:#e53e3e; padding:10px;'>"
              f"<b>脚本内部错误：</b>{html.escape(str(e))}{detail}</div>")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        sys.exit(0)
