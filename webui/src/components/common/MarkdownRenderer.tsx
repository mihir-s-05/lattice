import React from 'react'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

interface MarkdownRendererProps {
  content: string
  className?: string
}

// Configure markdown-it with safe settings
const md = new MarkdownIt({
  html: false, // Disable raw HTML for security
  xhtmlOut: false,
  breaks: true, // Convert '\n' in paragraphs into <br>
  linkify: true, // Autoconvert URL-like text to links
  typographer: true, // Enable some language-neutral replacement + quotes beautification
})

// Configure link renderer to be safe
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const href = token.attrGet('href')
  
  // Add security attributes to external links
  if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
    token.attrSet('target', '_blank')
    token.attrSet('rel', 'noopener noreferrer')
  }
  
  return self.renderToken(tokens, idx, options)
}

// Configure code block renderer
md.renderer.rules.code_block = (tokens, idx) => {
  const token = tokens[idx]
  const content = token.content
  
  return `<div class="relative group">
    <pre class="bg-gray-100 dark:bg-gray-800 rounded-md p-4 overflow-x-auto"><code>${md.utils.escapeHtml(content)}</code></pre>
    <button 
      class="absolute top-2 right-2 opacity-0 group-hover:opacity-100 p-1 rounded bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 transition-opacity"
      onclick="navigator.clipboard.writeText('${md.utils.escapeHtml(content).replace(/'/g, "\\'")}'); this.textContent='Copied!'; setTimeout(() => this.innerHTML='<svg class=\\'h-4 w-4\\' fill=\\'none\\' stroke=\\'currentColor\\' viewBox=\\'0 0 24 24\\'><path stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\' stroke-width=\\'2\\' d=\\'M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z\\'></path></svg>', 1000)"
      title="Copy code"
    >
      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
      </svg>
    </button>
  </div>`
}

// Configure fence (```code```) renderer
md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const content = token.content
  const lang = token.info.trim()
  
  return `<div class="relative group">
    <div class="flex items-center justify-between bg-gray-100 dark:bg-gray-800 px-4 py-2 rounded-t-md border-b border-gray-200 dark:border-gray-700">
      <span class="text-xs text-gray-600 dark:text-gray-400 font-mono">${lang || 'code'}</span>
      <button 
        class="opacity-0 group-hover:opacity-100 p-1 rounded bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 transition-opacity"
        onclick="navigator.clipboard.writeText('${md.utils.escapeHtml(content).replace(/'/g, "\\'")}'); this.textContent='Copied!'; setTimeout(() => this.innerHTML='<svg class=\\'h-4 w-4\\' fill=\\'none\\' stroke=\\'currentColor\\' viewBox=\\'0 0 24 24\\'><path stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\' stroke-width=\\'2\\' d=\\'M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z\\'></path></svg>', 1000)"
        title="Copy code"
      >
        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
        </svg>
      </button>
    </div>
    <pre class="bg-gray-50 dark:bg-gray-900 rounded-b-md p-4 overflow-x-auto"><code class="language-${lang}">${md.utils.escapeHtml(content)}</code></pre>
  </div>`
}

export function MarkdownRenderer({ content, className = '' }: MarkdownRendererProps) {
  const html = React.useMemo(() => {
    const rendered = md.render(content)
    return DOMPurify.sanitize(rendered, {
      ALLOWED_TAGS: [
        'p', 'br', 'strong', 'em', 'u', 's', 'code', 'pre', 'div', 'span',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li',
        'blockquote',
        'a', 'img',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'button', 'svg', 'path'
      ],
      ALLOWED_ATTR: [
        'href', 'target', 'rel', 'title', 'alt', 'src', 'class', 'onclick',
        'stroke-linecap', 'stroke-linejoin', 'stroke-width', 'fill', 'stroke', 'viewBox', 'd'
      ],
      ALLOWED_URI_REGEXP: /^(?:(?:(?:f|ht)tps?|mailto|tel|callto|sms|cid|xmpp):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
    })
  }, [content])

  return (
    <div 
      className={`markdown-content ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
