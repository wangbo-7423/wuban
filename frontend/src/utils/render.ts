/**
 * 富文本渲染工具：Markdown + KaTeX。
 *
 * - `renderRich`：把助手文本里的 Markdown 与 $...$/$$...$$ 公式渲染成 HTML。
 * - `renderKatex`：直接渲染一段 LaTeX（用于 math 卡的步骤/答案）。
 *
 * 管线设计（顺序很重要）：
 * 1. 先从「原始 Markdown 文本」里抽取数学片段换成占位符——若在 md.render 之后
 *    再用正则找 $...$，公式里的 `"` `_` 等已被转义/改写（&quot;、<em>），KaTeX 必炸。
 * 2. md.render 渲染剩余 Markdown（html:false 防注入）。
 * 3. 把占位符换回 KaTeX HTML。
 *
 * 失败兜底：KaTeX 解析失败时不用它的红色报错样式，而是把原始公式以
 * 等宽代码样式原样展示（红色对用户而言就是"报错了"）。
 */
import MarkdownIt from 'markdown-it'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

/** 折叠 3+ 连续换行为 1 个空行（跳过代码围栏内部，避免破坏代码格式）。
 *  GLM 输出常在段落/标题/列表项之间塞多个空行，markdown-it 会原样放大成巨大间隙。 */
function normalizeMd(text: string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$))/g)
    .map((seg, i) => (i % 2 === 1 ? seg : seg.replace(/\n{3,}/g, '\n\n')))
    .join('')
}

interface MathSlot {
  expr: string
  display: boolean
}

/** 从原始文本抽取 $$...$$ 与 $...$（跳过代码围栏/行内代码），返回替换后的文本与片段表。 */
function extractMath(text: string): { text: string; slots: MathSlot[] } {
  const slots: MathSlot[] = []
  // 先按代码围栏/行内代码切段，数学抽取只作用于非代码段
  const parts = text.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g)
  const out = parts.map((seg, i) => {
    if (i % 2 === 1) return seg // 代码段原样保留
    let s = seg
    // 块级 $$...$$（允许跨行）
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_m, expr: string) => {
      slots.push({ expr: expr.trim(), display: true })
      return `@@MATH${slots.length - 1}@@`
    })
    // 行内 $...$（排除 \$ 转义、不跨行、右侧不是数字——避免误吞价格如 $5）
    s = s.replace(/(?<!\\)\$([^$\n]+?)\$(?!\d)/g, (_m, expr: string) => {
      slots.push({ expr: expr.trim(), display: false })
      return `@@MATH${slots.length - 1}@@`
    })
    return s
  })
  return { text: out.join(''), slots }
}

/** KaTeX 渲染单个片段；失败时回退为等宽原文（不红、不炸）。 */
function renderSlot({ expr, display }: MathSlot): string {
  try {
    return katex.renderToString(expr, { displayMode: display, throwOnError: true })
  } catch {
    const esc = expr
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
    return `<code class="math-fallback">${esc}</code>`
  }
}

/** 渲染一段可能含 Markdown 与 LaTeX 的助手文本。
 *
 * @param deferMath 流式进行时为 true：公式先渲染成轻量等宽占位，不走 KaTeX。
 *   原因：流式期间每次 flush（60ms）都会整段重渲染 + v-html 全量重建 innerHTML，
 *   KaTeX 的 renderToString（含大段 SVG）在长回答里每个公式要几十毫秒，
 *   几十个 flush × 多个公式 = 主线程被完全堵死，用户看到的就是「卡住后一口气全出来」。
 *   流结束（done 替换为正式卡片）后再走完整 KaTeX 渲染。
 */
export function renderRich(text: string, deferMath = false): string {
  if (!text) return ''
  const { text: mdText, slots } = extractMath(normalizeMd(text))
  let html = md.render(mdText)
  // 换回 KaTeX HTML（占位符是纯 ASCII，markdown-it 不会改写；
  // 但 breaks/段落可能给它包上 <p>，块级公式允许换行出现在占位符前后）
  slots.forEach((slot, i) => {
    const out = deferMath ? renderSlotDeferred(slot) : renderSlot(slot)
    html = html.split(`@@MATH${i}@@`).join(out)
  })
  return html
}

/** 流式期间的公式占位：等宽原文 + 呼吸动画，提示"公式稍后成形"。 */
function renderSlotDeferred({ expr, display }: MathSlot): string {
  const esc = expr
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return display
    ? `<div class="math-deferred">${esc}</div>`
    : `<code class="math-deferred">${esc}</code>`
}

/** 直接渲染一段 LaTeX 表达式（display=false 行内，true 块级）。 */
export function renderKatex(expr: string, display = false): string {
  return renderSlot({ expr, display })
}
