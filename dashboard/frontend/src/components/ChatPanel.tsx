import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Image, Loader2, X, Upload, MessageSquare, PlusCircle, History, Square } from 'lucide-react'
import SafeMarkdown from './SafeMarkdown'
import { useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '@/store'
import { chatService, getApiErrorMessage, ChatStreamCancelledError } from '@/services/chat'
import { busyHint } from '@/lib/conversationOps'
import { buttonClass, cx, focusRing, numeric } from '@/components/ui'
import { useAppMode } from '@/components/shell/useAppMode'
import ConversationHistory from './ConversationHistory'
import type { ChatMessage } from '@/types'

// Assistant text: tables scroll inside the bubble instead of widening the dock.
const REPLY = 'min-w-0 max-w-none overflow-x-auto break-words'
// The message field, drawn like the kit's Input (a plain input: the shell's tests address it as input[type=text]).
const INPUT =
  'h-8 min-w-0 flex-1 rounded-md border border-line-input bg-surface px-3 text-body text-fg placeholder:text-fg-3 ' +
  'transition-colors duration-[120ms] hover:border-fg-3 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent ' +
  'disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-fg-3 coarse:h-10'
// Nothing to send yet (or no conversation to leave): the dock's primary buttons are drawn like the kit's disabled
// controls (surface-2, text-3), not as a faded accent that still glows on the dark dock.
const MUTED_DISABLED = 'disabled:bg-surface-2 disabled:text-fg-3 disabled:opacity-100'

export default function ChatPanel() {
  const [input, setInput] = useState('')
  const [selectedImages, setSelectedImages] = useState<{file: File, preview: string}[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const dropZoneRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  // Sending (text or images) and starting a new conversation reach the model or write history: not in the
  // demo, and not while the backend's mode is unknown (it may be a demo). Typing stays possible.
  const { readOnlyReason } = useAppMode()

  const {
    messages,
    isLoading,
    currentResponse,
    cancelStream,
    addMessage,
    setLoading,
    setCurrentResponse,
    setCancelStream,
    appendToCurrentResponse,
    clearMessages,
    conversationOp,
    beginConversationOp,
    finishConversationOp,
    isConversationOpCurrent,
  } = useAppStore()
  // Resume / reset / send share one state in the store, so it survives this panel remounting.
  const opBusy = conversationOp.active !== null

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentResponse])

  // 处理图片选择（文件或拖拽）
  const handleImageFile = useCallback((file: File) => {
    if (!file.type.startsWith('image/')) {
      return
    }

    // 创建预览
    const reader = new FileReader()
    reader.onload = (e) => {
      setSelectedImages(prev => [...prev, { file, preview: e.target?.result as string }])
    }
    reader.readAsDataURL(file)
  }, [])

  // 清除单张图片
  const clearImage = useCallback((index: number) => {
    setSelectedImages(prev => prev.filter((_, i) => i !== index))
  }, [])

  // 清除所有图片
  const clearAllImages = useCallback(() => {
    setSelectedImages([])
  }, [])

  // 拖拽事件处理
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    // 检查是否真的离开了拖拽区域
    if (e.currentTarget.contains(e.relatedTarget as Node)) {
      return
    }
    setIsDragging(false)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      Array.from(files).forEach(file => {
        if (file.type.startsWith('image/')) {
          handleImageFile(file)
        }
      })
    }
  }, [handleImageFile])

  // 粘贴事件处理
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData.items
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        const file = items[i].getAsFile()
        if (file) {
          handleImageFile(file)
          e.preventDefault()
          break
        }
      }
    }
  }, [handleImageFile])

  // 开始新对话
  // Not while a reply is in progress: its text would otherwise be appended to the new, empty
  // conversation when it finishes. The button is disabled then; this guard covers other callers.
  const handleNewConversation = async () => {
    if (readOnlyReason) return
    if (isLoading || opBusy) return
    if (window.confirm('确定要开始新对话吗？当前对话将被清空（但已保存到历史记录中）')) {
      const token = beginConversationOp('reset')
      if (token === null) return
      try {
        await chatService.clearHistory()
        // Only clear if this reset is still the current operation.
        if (finishConversationOp(token)) clearMessages()
      } catch (error) {
        console.error('Failed to start new conversation:', error)
        if (finishConversationOp(token)) alert('开始新对话失败')
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (readOnlyReason) return
    if (!input.trim() && selectedImages.length === 0) return
    if (isLoading || opBusy) return
    const opToken = beginConversationOp('send')
    if (opToken === null) return
    const stillCurrent = () => isConversationOpCurrent(opToken)

    const currentImages = [...selectedImages]
    const userMessage = input.trim()

    // 立即清空输入和图片
    setInput('')
    clearAllImages()

    // 添加用户消息
    addMessage({
      role: 'user',
      content: userMessage,
      images: currentImages.map(img => img.preview), // 保存多图预览
    })

    setLoading(true)
    setCurrentResponse('')

    // Text streamed so far, kept outside the try so a stopped or failed reply can still show it.
    let streamedResponse = ''
    // Lets the Stop button (and the inactivity timeout inside streamMessage) end the reply.
    const abortController = new AbortController()

    try {
      if (currentImages.length > 0) {
        // 带图片的消息
        const response = await chatService.sendMessageWithImage(
          userMessage,
          currentImages.map(c => c.file),
          true
        )

        // 添加助手回复
        if (stillCurrent()) addMessage({
          role: 'assistant',
          content: response.response,
        })

      } else {
        // 普通文本消息默认流式返回
        setCancelStream(() => abortController.abort())
        await chatService.streamMessage(
          userMessage,
          (chunk) => {
            streamedResponse += chunk
            appendToCurrentResponse(chunk)
          },
          () => {},
          { signal: abortController.signal }
        )
        setCancelStream(null)

        if (stillCurrent()) addMessage({
          role: 'assistant',
          content: streamedResponse,
        })
        setCurrentResponse('')
        setLoading(false)

        // After the reply is shown: the backend extracts what the assistant may propose (lessons, profile
        // changes); they arrive as proposals in the 待确认 tray.
        void chatService.extractResponse(userMessage, streamedResponse)
          .catch((extractError) => {
            console.error('Extraction error:', extractError)
          })
        return
      }
    } catch (error) {
      if (!stillCurrent()) {
        console.error('Chat error after the conversation changed:', error)
      } else if (error instanceof ChatStreamCancelledError) {
        // Stopped by the user: keep what was already shown and say that it is incomplete.
        addMessage({
          role: 'assistant',
          content: streamedResponse ? `${streamedResponse}\n\n（已停止生成）` : '（已停止生成）',
        })
      } else {
        console.error('Chat error:', error)
        // A reply cut off part-way (timeout, dropped connection) keeps its text plus the reason.
        addMessage({
          role: 'assistant',
          content: streamedResponse
            ? `${streamedResponse}\n\n（回复中断：${getApiErrorMessage(error)}）`
            : `抱歉，请求失败：${getApiErrorMessage(error)}`,
        })
      }
    } finally {
      void queryClient.invalidateQueries({ predicate: query => String(query.queryKey[0]).startsWith('ledger') })
      setCancelStream(null)
      setLoading(false)
      setCurrentResponse('')
      finishConversationOp(opToken)
    }
  }

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files) {
      Array.from(files).forEach(file => {
        if (file.type.startsWith('image/')) handleImageFile(file)
      })
    }
    // reset input
    if (e.target) e.target.value = ''
  }

  const renderMessage = (message: ChatMessage, index: number) => {
    const isUser = message.role === 'user'

    return (
      <div
        key={index}
        className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}
      >
        <div
          className={cx('max-w-[80%] rounded-lg px-4 py-3 text-body', isUser ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-fg')}
        >
          {/* 显示图片 (旧兼容逻辑单图) */}
          {message.image && (
            <div className="mb-2">
              <img
                src={message.image}
                alt="上传的图片"
                className="max-w-full rounded-lg max-h-48 object-contain"
              />
            </div>
          )}
          {/* 显示多张图片 */}
          {message.images && message.images.length > 0 && (
            <div className={`mb-2 grid gap-2 ${message.images.length > 1 ? 'grid-cols-2' : 'grid-cols-1'}`}>
              {message.images.map((imgUrl, i) => (
                <img
                  key={i}
                  src={imgUrl}
                  alt={`上传图片 ${i+1}`}
                  className="max-w-full rounded-lg max-h-48 object-contain"
                />
              ))}
            </div>
          )}
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className={REPLY}>
              <SafeMarkdown>{message.content}</SafeMarkdown>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={dropZoneRef}
      className={cx('relative flex h-full flex-col text-fg', isDragging && 'ring-2 ring-inset ring-accent')}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* 拖拽覆盖层 */}
      {isDragging && (
        <div className="absolute inset-0 z-50 flex items-center justify-center rounded-lg border-2 border-dashed border-accent bg-surface/90">
          <div className="text-center">
            <Upload className="mx-auto mb-2 h-10 w-10 text-accent" aria-hidden />
            <p className="text-title text-accent">释放以上传图片</p>
            <p className="mt-1 text-table text-fg-3">支持 JPG、PNG 等图片格式</p>
          </div>
        </div>
      )}

      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <MessageSquare className="h-4 w-4 shrink-0 text-fg-3" aria-hidden />
          <span className={cx('truncate text-table font-medium text-fg-2', numeric)}>
            {messages.length > 0 ? `对话中 (${messages.length} 条消息)` : '准备开始对话'}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => setShowHistory(true)}
            className={buttonClass({ size: 'sm' })}
            title="查看历史对话"
          >
            <History className="h-4 w-4" aria-hidden />
            <span>历史</span>
          </button>
          <button
            type="button"
            onClick={handleNewConversation}
            disabled={messages.length === 0 || isLoading || opBusy || !!readOnlyReason}
            className={cx(buttonClass({ variant: 'primary', size: 'sm' }), MUTED_DISABLED)}
            title={readOnlyReason ?? busyHint(conversationOp) ?? '开始新对话'}
          >
            <PlusCircle className="h-4 w-4" aria-hidden />
            <span>新对话</span>
          </button>
        </div>
      </div>

      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-fg-3">
            <div className="text-center">
              <p className="text-title text-fg-2">开始与交易助手对话</p>
              <p className="mt-2 text-body">
                可以问行情和持仓，或让它准备记账、提醒和计划的方案（确认后才生效）
              </p>
              <p className="mt-4 text-caption">
                提示：可以直接拖拽图片到这里上传
              </p>
            </div>
          </div>
        ) : (
          <>
            {messages.map(renderMessage)}
            {/* 流式回复 */}
            {currentResponse && (
              <div className="flex justify-start mb-4">
                <div className="max-w-[80%] rounded-lg bg-surface-2 px-4 py-3 text-body text-fg">
                  <div className={REPLY}>
                    <SafeMarkdown>{currentResponse}</SafeMarkdown>
                  </div>
                </div>
              </div>
            )}
            {isLoading && !currentResponse && (
              <div className="flex justify-start mb-4">
                <div className="rounded-lg bg-surface-2 px-4 py-3">
                  <Loader2 className="h-5 w-5 animate-spin text-fg-3" aria-label="正在回复" />
                </div>
              </div>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 图片预览 - 支持多张图片 */}
      {selectedImages.length > 0 && (
        <div className="overflow-x-auto border-t border-line px-4 py-2">
          <div className="flex items-start gap-4">
            {selectedImages.map((imgObj, i) => (
              <div key={i} className="flex relative flex-col items-center flex-shrink-0">
                <div className="relative">
                  <img
                    src={imgObj.preview}
                    alt={`预览 ${i}`}
                    className="h-20 w-auto rounded-md border border-line bg-surface-2 object-contain"
                  />
                  <button
                    type="button"
                    onClick={() => clearImage(i)}
                    aria-label={`移除图片 ${imgObj.file.name}`}
                    className={cx('absolute -right-2 -top-2 z-10 rounded-full border border-line-input bg-surface p-1 text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg', focusRing)}
                  >
                    <X className="h-3 w-3" aria-hidden />
                  </button>
                </div>
                <div className={cx('mt-1 w-full max-w-[100px] truncate text-center text-caption text-fg-3', numeric)}>
                  {imgObj.file.name}
                  <br />
                  {(imgObj.file.size / 1024).toFixed(1)} KB
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 输入区域 */}
      <form onSubmit={handleSubmit} className="border-t border-line p-4">
        {readOnlyReason && (
          <p role="status" className="mb-2 text-caption text-fg-3">{readOnlyReason}：现在不能发送消息或图片。</p>
        )}
        <div className="flex items-center gap-3">
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleImageSelect}
            accept="image/*"
            multiple
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={!!readOnlyReason}
            aria-label="上传图片"
            className={cx(buttonClass({ variant: 'ghost' }), 'w-8 px-0 coarse:w-10')}
            title={readOnlyReason ?? '上传图片（也可以直接拖拽或粘贴）'}
          >
            <Image className="h-5 w-5" aria-hidden />
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={handlePaste}
            placeholder="输入消息...（可拖拽或 Ctrl+V 粘贴图片）"
            className={INPUT}
            disabled={isLoading || opBusy}
          />
          {isLoading && cancelStream ? (
            <button
              type="button"
              onClick={cancelStream}
              className={buttonClass()}
              title="停止生成"
              aria-label="停止生成"
            >
              <Square className="h-4 w-4" aria-hidden />
              <span>停止</span>
            </button>
          ) : (
            <button
              type="submit"
              aria-label="发送"
              title={readOnlyReason}
              disabled={isLoading || opBusy || !!readOnlyReason || (!input.trim() && selectedImages.length === 0)}
              className={cx(buttonClass({ variant: 'primary' }), MUTED_DISABLED)}
            >
              <Send className="h-4 w-4" aria-hidden />
            </button>
          )}
        </div>
      </form>

      {/* 历史对话模态框 */}
      <ConversationHistory
        isOpen={showHistory}
        onClose={() => setShowHistory(false)}
      />
    </div>
  )
}
