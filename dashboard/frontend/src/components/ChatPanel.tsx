import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Image, Loader2, X, Upload, MessageSquare, PlusCircle, History } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useQueryClient } from '@tanstack/react-query'
import { useAppStore } from '@/store'
import { chatService } from '@/services'
import { getApiErrorMessage } from '@/services/chat'
import ConversationHistory from './ConversationHistory'
import type { ChatMessage } from '@/types'

export default function ChatPanel() {
  const [input, setInput] = useState('')
  const [selectedImages, setSelectedImages] = useState<{file: File, preview: string}[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const dropZoneRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  const {
    messages,
    isLoading,
    currentResponse,
    addMessage,
    setLoading,
    setCurrentResponse,
    appendToCurrentResponse,
    setSuggestions,
    setRisks,
    clearMessages,
  } = useAppStore()

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
  const handleNewConversation = async () => {
    if (window.confirm('确定要开始新对话吗？当前对话将被清空（但已保存到历史记录中）')) {
      try {
        await chatService.clearHistory()
        clearMessages()
      } catch (error) {
        console.error('Failed to start new conversation:', error)
        alert('开始新对话失败')
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() && selectedImages.length === 0) return
    if (isLoading) return

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

    try {
      if (currentImages.length > 0) {
        // 带图片的消息
        const response = await chatService.sendMessageWithImage(
          userMessage,
          currentImages.map(c => c.file),
          true
        )

        // 添加助手回复
        addMessage({
          role: 'assistant',
          content: response.response,
        })

        // 更新建议和风险
        if (response.suggestions) {
          setSuggestions(response.suggestions)
        }
        if (response.risks) {
          setRisks(response.risks)
        }

        // 如果有持仓导入，刷新持仓数据
        if (response.imported_positions && response.imported_positions > 0) {
          queryClient.invalidateQueries({ queryKey: ['portfolio'] })
        }
      } else {
        // 普通文本消息默认流式返回
        let streamedResponse = ''
        await chatService.streamMessage(
          userMessage,
          (chunk) => {
            streamedResponse += chunk
            appendToCurrentResponse(chunk)
          },
          () => {}
        )

        addMessage({
          role: 'assistant',
          content: streamedResponse,
        })
        setCurrentResponse('')
        setLoading(false)

        // 在回复已展示后，异步提取建议/风险/记忆
        void chatService.extractResponse(userMessage, streamedResponse)
          .then((result) => {
            if (result.suggestions) {
              setSuggestions(result.suggestions)
            }
            if (result.risks) {
              setRisks(result.risks)
            }
            if (result.imported_positions && result.imported_positions > 0) {
              queryClient.invalidateQueries({ queryKey: ['portfolio'] })
            }
          })
          .catch((extractError) => {
            console.error('Extraction error:', extractError)
          })
        return
      }
    } catch (error) {
      console.error('Chat error:', error)
      addMessage({
        role: 'assistant',
        content: `抱歉，请求失败：${getApiErrorMessage(error)}`,
      })
    } finally {
      setLoading(false)
      setCurrentResponse('')
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
          className={`max-w-[80%] rounded-lg px-4 py-3 ${
            isUser
              ? 'bg-primary-600 text-white'
              : 'bg-slate-700 text-slate-100'
          }`}
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
            <div className="prose prose-invert max-w-none prose-td:border prose-td:border-slate-600 prose-th:border prose-th:border-slate-600 prose-th:bg-slate-800 prose-table:w-auto overflow-x-auto">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={dropZoneRef}
      className={`flex h-full flex-col card relative ${
        isDragging ? 'ring-2 ring-primary-500 ring-offset-2 ring-offset-slate-900' : ''
      }`}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* 拖拽覆盖层 */}
      {isDragging && (
        <div className="absolute inset-0 bg-primary-600/20 backdrop-blur-sm z-50 flex items-center justify-center rounded-lg border-2 border-dashed border-primary-500">
          <div className="text-center">
            <Upload className="h-12 w-12 mx-auto mb-2 text-primary-400" />
            <p className="text-lg font-medium text-primary-400">释放以上传图片</p>
            <p className="text-sm text-slate-400 mt-1">支持 JPG、PNG 等图片格式</p>
          </div>
        </div>
      )}

      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
        <div className="flex items-center space-x-2">
          <MessageSquare className="h-5 w-5 text-slate-400" />
          <span className="text-sm font-medium text-slate-300">
            {messages.length > 0 ? `对话中 (${messages.length} 条消息)` : '准备开始对话'}
          </span>
        </div>
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setShowHistory(true)}
            className="flex items-center space-x-1.5 px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
            title="查看历史对话"
          >
            <History className="h-4 w-4" />
            <span>历史</span>
          </button>
          <button
            onClick={handleNewConversation}
            disabled={messages.length === 0}
            className="flex items-center space-x-1.5 px-3 py-1.5 text-sm bg-primary-600 hover:bg-primary-500 disabled:bg-slate-700 disabled:text-slate-500 disabled:cursor-not-allowed rounded-lg transition-colors"
            title="开始新对话"
          >
            <PlusCircle className="h-4 w-4" />
            <span>新对话</span>
          </button>
        </div>
      </div>

      {/* 消息列表 */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center text-slate-400">
            <div className="text-center">
              <p className="text-lg">开始与交易助手对话</p>
              <p className="mt-2 text-sm">
                我可以帮你分析市场、管理持仓、提供交易建议
              </p>
              <p className="mt-4 text-xs text-slate-500">
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
                <div className="max-w-[80%] rounded-lg px-4 py-3 bg-slate-700 text-slate-100">
                  <div className="prose prose-invert max-w-none prose-td:border prose-td:border-slate-600 prose-th:border prose-th:border-slate-600 prose-th:bg-slate-800 prose-table:w-auto overflow-x-auto">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{currentResponse}</ReactMarkdown>
                  </div>
                </div>
              </div>
            )}
            {isLoading && !currentResponse && (
              <div className="flex justify-start mb-4">
                <div className="rounded-lg px-4 py-3 bg-slate-700">
                  <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                </div>
              </div>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 图片预览 - 支持多张图片 */}
      {selectedImages.length > 0 && (
        <div className="px-4 py-2 border-t border-slate-700 overflow-x-auto">
          <div className="flex items-start gap-4">
            {selectedImages.map((imgObj, i) => (
              <div key={i} className="flex relative flex-col items-center flex-shrink-0">
                <div className="relative">
                  <img
                    src={imgObj.preview}
                    alt={`预览 ${i}`}
                    className="h-20 w-auto rounded-lg object-contain bg-slate-800 border border-slate-600"
                  />
                  <button
                    type="button"
                    onClick={() => clearImage(i)}
                    className="absolute -top-2 -right-2 p-1 bg-red-500 rounded-full hover:bg-red-400 transition-colors z-10"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
                <div className="w-full text-center mt-1 truncate text-xs text-slate-400 max-w-[100px]">
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
      <form onSubmit={handleSubmit} className="border-t border-slate-700 p-4">
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
            className="p-2 text-slate-400 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
            title="上传图片（也可以直接拖拽或粘贴）"
          >
            <Image className="h-5 w-5" />
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={handlePaste}
            placeholder="输入消息...（可拖拽或 Ctrl+V 粘贴图片）"
            className="flex-1 input-field"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={isLoading || (!input.trim() && selectedImages.length === 0)}
            className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Send className="h-5 w-5" />
          </button>
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
