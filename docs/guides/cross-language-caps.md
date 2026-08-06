# 跨语言消费 caps 与 conformance

本指南面向不读取 Python 源码、只拿到 `caps.json` 和 `conformance.json`，需要在 Bun/JS
或 Go 中实现参数翻译的工程师。两份文件都是 JSON；跨语言实现只需要复现下面的匹配、翻译
和向量比对规则。

## 两份文件分别是什么

- `caps.json` 是 L1 知识：模型名如何匹配到 provider family，以及每个 family 支持哪些
  `reasoning_effort`、如何关闭思考、是否支持视觉和哪种 JSON 模式。它回答“这个 family
  有什么能力”。
- `conformance.json` 是 L1+L2 行为契约：每条向量给出模型名、`reasoning_effort` 和
  `strict` 输入，并规定应得到的 `family`、`matched`、请求体字段 `set` 和 warning 类别。
  它回答“给定输入，翻译器必须产出什么”。当前文件包含 336 条向量。

不要把 `caps.json` 中的 `families` 当成匹配结果：必须先按 `patterns` 得到 family，再
读取该 family 的能力记录。

## 1. 模型名匹配：有序的 first-match-wins

实现步骤固定如下：

1. 将模型名转为 lowercase。
2. 按 `caps.json` 的 `patterns` 数组顺序，依次将 pattern 也转为 lowercase。
3. 用 Python `fnmatch` 语义匹配，首个命中就返回该项的 `family`，并令 `matched=true`。
4. 全部不命中时返回 `family="openai"`、`matched=false`。

数组顺序是契约的一部分，不能排序、去重或按 family 重组。当前文件中的完整顺序如下：

| 顺序 | pattern | family |
| ---: | --- | --- |
| 1 | `deepseek-chat` | `deepseek` |
| 2 | `deepseek-reasoner` | `deepseek` |
| 3 | `deepseek-*` | `deepseek` |
| 4 | `gemini-2.5-*` | `gemini_25` |
| 5 | `gemini-3-*` | `gemini_3` |
| 6 | `gemini-3.*-*` | `gemini_3` |
| 7 | `gemini-*` | `gemini` |
| 8 | `gpt-5` | `openai_gpt5` |
| 9 | `gpt-5-*` | `openai_gpt5` |
| 10 | `gpt-5.*` | `openai_gpt5` |
| 11 | `gpt-4*` | `openai_gpt4` |
| 12 | `gpt-*` | `openai` |
| 13 | `doubao-seed-*` | `doubao_seed` |
| 14 | `doubao-*` | `doubao` |
| 15 | `o1*` | `openai_o` |
| 16 | `o3*` | `openai_o` |
| 17 | `o4*` | `openai_o` |
| 18 | `o5*` | `openai_o` |

例如，`gpt-5` 必须先命中第 8 项而不是第 12 项；如果把通用的 `gpt-*` 排到
`gpt-5` 前面，它会错误地落入 `openai` 族，随后得到错误的能力和请求字段。这种错误
通常不会被 API 报出来，所以顺序必须原样保留。

## 2. glob 语义：只实现本仓实际用到的子集

Python 端调用 `fnmatch`。JavaScript 没有内置等价函数，而 npm 上多数 `glob` 库是文件
路径匹配器，会对 `/`、`**` 等使用路径语义，不能直接拿来替代这里的 `fnmatch`。

当前 `caps.json` 的全部 pattern 只使用 `*` 这一种通配符；其余字符（包括 `.`、`-`）
都是字面量，没有 `?` 或字符组 `[abc]`。因此 Bun/JS 可以实现一个最小、等价于当前数据
的 matcher：先把非 `*` 片段做正则转义，再把 `*` 替换为 `.*`，并加上 `^`/`$` 全串
锚定。伪代码如下：

```ts
function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function fnmatchCurrent(text: string, pattern: string): boolean {
  const regexSource = pattern
    .split("*")
    .map(escapeRegex)
    .join(".*");
  return new RegExp(`^${regexSource}$`).test(text);
}

function detectProvider(model: string, caps: CapsDocument): Detection {
  const modelLower = model.toLowerCase();
  for (const { pattern, family } of caps.patterns) {
    if (fnmatchCurrent(modelLower, pattern.toLowerCase())) {
      return { family, matched: true };
    }
  }
  return { family: "openai", matched: false };
}
```

如果将来 `caps.json` 增加了 `?` 或字符组，不能继续假设这段子集实现足够；应先扩展
matcher，并用新的 conformance 向量验证。

## 3. 按 family 翻译参数

匹配成功后，从 `caps.families[detection.family]` 读取能力。关闭思考时，读取
`caps.disable_mode_semantics`，五种 `disable_mode` 的 wire 结果是：

| `disable_mode` | 要加入请求体的字段 |
| --- | --- |
| `native` | `{ "thinking": { "type": "disabled" } }` |
| `effort_none` | `{ "reasoning_effort": "none" }` |
| `minimal_fallback` | `{ "reasoning_effort": "minimal" }` |
| `unsupported` | `{}`，同时记录“无法关闭思考”的 warning |
| `na` | `{}`；该 family 本来就不推理 |

`reasoning_effort` 未设置（JSON `null`）时也返回 `{}`。`none`、`off`、`false` 是旧别名，
应先归一化为 `disabled`；`conformance.json` 的 warning 向量会覆盖这些别名。

其他 effort 值按 `caps.effort_rank` 的数值比较：

1. family 已支持该值时原样发送。
2. 不支持时，在该 family 的 `efforts` 中找 rank **大于等于请求 rank 的最近值**。
3. 没有更高或相等的值时，取该 family 的最高档；因此超过上限会向下钳制到最高档，
   低于下限会向上钳制到最低档。
4. family 的 `efforts` 为空时丢弃字段并记录 warning。

例如 `deepseek` 的 `efforts` 是 `low/high/max/xhigh`，请求 `medium` 的 rank 为 2，
最近的更高支持档是 `high`，所以应发送 `{"reasoning_effort":"high"}`；请求 `xhigh`
则原样发送。

未知模型的 `matched=false` 也要保留：lenient（默认）模式按 `openai` 族翻译，strict
模式则丢弃 reasoning 字段并记录 warning。下游可以在落日志或配置检查时用
`matched=false` 找出未知模型，而不能只看 `family`。

## 4. 用 336 条向量自证实现

只有 `conformance.json.reviewed` 为 `true` 时，才把这批向量当作人工审定过的契约。读取
每条向量的 `input`，运行自己的 normalize、detect 和 translate，然后至少精确比较：

- `family` 与 `expect.family`；
- `matched` 与 `expect.matched`；
- 请求体字段 `set` 与 `expect.set`，键必须**完全相等**，不能只检查“期望键都存在”；
- warning 类别数组与 `expect.warnings`。

TypeScript runner 的最小形状如下（`translateVector` 是你的实现）：

```ts
import assert from "node:assert/strict";
import conformance from "./conformance.json" with { type: "json" };

type Vector = (typeof conformance.vectors)[number];

if (conformance.reviewed !== true) {
  throw new Error("conformance vectors are not reviewed; do not use as a contract");
}

for (const vector of conformance.vectors as Vector[]) {
  const actual = translateVector(vector.input);
  assert.equal(actual.family, vector.expect.family, vector.id);
  assert.equal(actual.matched, vector.expect.matched, vector.id);
  assert.deepEqual(actual.set, vector.expect.set, `${vector.id}: exact wire fields`);
  assert.deepEqual(actual.warnings, vector.expect.warnings, `${vector.id}: warnings`);
}

console.log(`passed ${conformance.vectors.length} conformance vectors`);
```

Go 实现使用 `encoding/json` 读取同样的结构，比较 map 时也必须检查键集合和对应值都
相同（不能只做“期望键包含于实际 map”的断言）。

## 5. `reviewed` 与指纹

`reviewed=false` 表示这批向量尚未经过人工覆盖轴审定，不能作为跨语言契约使用。导出脚本
只对 `vectors` 数组做规范化 JSON（UTF-8、`sort_keys=true`、固定分隔符）SHA-256；任何
向量的增删改都会让指纹失配，自动把 `reviewed` 变回 `false`。重新审定后，才允许把新的
指纹填入 `scripts/export_conformance.py` 的 `REVIEWED_VECTORS_DIGEST` 并重新生成文件。

## 6. 版本、来源与网关边界

当前 checked-in 两份文件对应 commit
`d69bf307140644e6402d5d0bf32e0d1967095c98`（短 SHA：`d69bf30`）中的
`src/llm_compat/providers.py`，由 `scripts/export_caps.py` 与
`scripts/export_conformance.py` 导出，JSON 的 `schema_version` 为 1。升级 provider 规则
或导出器后，应随同代码提交重新生成的两份文件，并在消费方 pin 到同一个 commit/版本。

`caps.json.gateway.kind` 为 `new-api` 很重要：其中的能力值是通过 New API 代理实际观测到
的 wire 行为，不是模型的固有属性。同一个模型换到另一个网关后，字段可能被转换、忽略或
拒绝，必须重新探测、审定并生成新的 caps/conformance 产物。

## 7. CI 防漂移闸

仓库已有两条 pytest 闸：`tests/test_export_caps.py` 的
`test_checked_in_caps_matches_export` 和 `tests/test_conformance.py` 的
`test_checked_in_conformance_matches_export`。它们分别重新导出 `caps.json` 和
`conformance.json`，再对 checked-in 文件做精确文本比较，防止源码、导出脚本和提交产物
漂移。

`.github/workflows/gate.yml` 只是调用复用 workflow
`zlxlabs/gate/.github/workflows/gate-v2.yml`；gate 的 `quality` job 已执行
`uv run --frozen pytest -q`，pytest 会自动收集上述两条测试。因此不需要、也不应为这两道
闸新增单独的 workflow 配置。
