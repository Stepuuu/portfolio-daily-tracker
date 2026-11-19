import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ImageOff } from 'lucide-react'
import { classifyImageSrc } from '@/lib/imagePolicy'

// Markdown renderer for model output. Use this instead of <ReactMarkdown> directly.
//
// The only difference from plain react-markdown is the image rule in src/lib/imagePolicy.ts:
// an image is loaded only from /api/ on this origin or from a data:image URL. A remote image is
// replaced by a link, so nothing is fetched from another host unless the user clicks it.
// (Raw HTML in the text is not rendered at all: react-markdown shows it as text.)

const components: Components = {
  img({ src, alt, title }) {
    const decision = classifyImageSrc(src)
    if (decision.kind === 'image') {
      return <img src={decision.src} alt={alt ?? ''} title={title} className="max-w-full rounded-lg" />
    }
    const label = alt ? `图片：${alt}` : '图片'
    if (decision.kind === 'link') {
      return (
        <a
          href={decision.href}
          target="_blank"
          rel="noopener noreferrer nofollow"
          referrerPolicy="no-referrer"
          title="外部图片未自动加载。点击会在新标签页打开该地址。"
          className="inline-flex items-center gap-1 break-all"
        >
          <ImageOff className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
          {label}（外部图片，未加载）：{decision.href}
        </a>
      )
    }
    return (
      <span className="inline-flex items-center gap-1 text-fg-3">
        <ImageOff className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
        {label}（地址不受支持，未加载）
      </span>
    )
  },
}

// react-markdown removes data: URLs before a component sees them. Image sources are passed
// through untouched so the img component above can apply the policy to the original value;
// every other URL (links) keeps the library's default sanitising.
function urlTransform(url: string, key: string, node: { tagName: string }): string {
  if (key === 'src' && node.tagName === 'img') return url
  return defaultUrlTransform(url)
}

export default function SafeMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} urlTransform={urlTransform}>
      {children}
    </ReactMarkdown>
  )
}
