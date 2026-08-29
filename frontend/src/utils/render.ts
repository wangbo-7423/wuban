/**
 * 富文本渲染工具：Markdown + KaTeX。
 *
 * - `renderRich`：把助手文本里的 Markdown 与 $...$/$$...$$ 公式渲染成 HTML。
 * - `renderKatex`：直接渲染一段 LaTeX（用于 math 卡的步骤/答案）。
 *
 * 安全：markdown-it 关闭 html 选项，避免原始 HTML 注入；KaTeX throwOnError=false
 * 保证即便公式有误也不会整段崩。
 */
import MarkdownIt from 'markdown-it'
import katex from 'katex'
import 'katex/dist/katex.min.css'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

function renderMathInHtml(html: string): string {
  // 先处理块级 $$...$$
  let out = html.replace(/\$\$([\s\S]+?)\$\$/g, (_m, expr: string) => {
    try {
      return katex.renderToString(expr.trim(), {
        displayMode: true,
        throwOnError: false,
      })
    } catch {
      return expr
    }
  })
  // 再行内 $...$（排除已处理块级残留与 \$ 转义）
  out = out.replace(/(?<!\\)\$([^$\n]+?)\$(?!\d)/g, (_m, expr: string) => {
    try {
      return katex.renderToString(expr.trim(), {
        displayMode: false,
        throwOnError: false,
      })
    } catch {
      return expr
    }
  })
  return out
}

/** 渲染一段可能含 Markdown 与 LaTeX 的助手文本。 */
export function renderRich(text: string): string {
  if (!text) return ''
  const html = md.render(text)
  return renderMathInHtml(html)
}

/** 直接渲染一段 LaTeX 表达式（display=false 行内，true 块级）。 */
export function renderKatex(expr: string, display = false): string {
  try {
    return katex.renderToString(expr, {
      displayMode: display,
      throwOnError: false,
    })
  } catch {
    return expr
  }
}
