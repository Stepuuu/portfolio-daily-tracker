import { useState, useEffect } from 'react'
import { X, Calendar, MessageSquare, Clock, ChevronRight, PlayCircle, Image as ImageIcon } from 'lucide-react'
import { chatService } from '@/services/chat'
import SafeMarkdown from './SafeMarkdown'
import { useAppStore } from '@/store'
import { busyHint } from '@/lib/conversationOps'
import { buttonClass, cx, focusRing, numeric } from '@/components/ui'
import { useAppMode } from '@/components/shell/useAppMode'
import type { ChatMessage } from '@/types'

interface ConversationFile {
  date: string
  file: string
  size: number
}

interface ConversationTurn {
  timestamp: string
  user: string
  assistant: string
  has_image: boolean
  images_count?: number
  image_paths?: string[]
  suggestions?: any[]
  risks?: any[]
}

interface ConversationHistoryProps {
  isOpen: boolean
  onClose: () => void
}

export default function ConversationHistory({ isOpen, onClose }: ConversationHistoryProps) {
  const [conversations, setConversations] = useState<ConversationFile[]>([])
  const [selectedConversation, setSelectedConversation] = useState<ConversationTurn[] | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const setMessages = useAppStore(state => state.setMessages)
  // A reply, a resume or a new-conversation reset is already running (shared state, so it
  // also holds after this modal was closed). Resuming now would replace the message list under it.
  const conversationOp = useAppStore(state => state.conversationOp)
  const chatBusy = conversationOp.active !== null
  const beginConversationOp = useAppStore(state => state.beginConversationOp)
  const finishConversationOp = useAppStore(state => state.finishConversationOp)
  // Resuming loads the conversation into the model's context: not in the demo or an unknown mode.
  const { readOnlyReason } = useAppMode()

  useEffect(() => {
    if (isOpen) {
      loadConversations()
    }
  }, [isOpen])

  const loadConversations = async () => {
    try {
      const data = await chatService.getConversations()
      setConversations(data)
    } catch (error) {
      console.error('Failed to load conversations:', error)
    }
  }

  const loadConversationDetail = async (date: string) => {
    setIsLoading(true)
    try {
      const data = await chatService.getConversationByDate(date)
      setSelectedConversation(data.turns)
      setSelectedDate(date)
    } catch (error) {
      console.error('Failed to load conversation detail:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleResumeConversation = async () => {
    if (readOnlyReason) return
    if (!selectedDate) return
    const token = beginConversationOp('resume')
    if (token === null) return
    setIsLoading(true)
    try {
      // 告诉后端加载这个会话为当前上下文
      const result = await chatService.loadConversation(selectedDate)
      // Superseded while waiting: the answer no longer belongs to the current conversation.
      if (!finishConversationOp(token)) return
      const { turns } = result

      // 更新前端状态
      const newMessages: ChatMessage[] = []
      turns.forEach((turn: any) => {
        // 重建用户消息，还原图片路径
        const userMsg: ChatMessage = {
          role: 'user',
          content: turn.user,
          timestamp: turn.timestamp,
        }
        if (turn.image_paths && turn.image_paths.length > 0) {
          userMsg.images = turn.image_paths.map((p: string) => `/api/chat/conversation-images/${p}`)
        }
        newMessages.push(userMsg)
        newMessages.push({
          role: 'assistant',
          content: turn.assistant,
          timestamp: turn.timestamp,
        })
      })
      
      setMessages(newMessages)

      onClose()
    } catch (error) {
      console.error('Failed to resume conversation:', error)
      if (finishConversationOp(token)) alert("加载历史对话失败")
    } finally {
      setIsLoading(false)
    }
  }

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      weekday: 'long'
    })
  }

  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp)
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--backdrop)] p-4">
      <div role="dialog" aria-modal="true" aria-label="对话历史" className="flex h-[80vh] w-full max-w-6xl flex-col rounded-lg border border-line bg-surface text-fg shadow-overlay">
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="flex items-center text-title">
            <MessageSquare className="mr-2 h-5 w-5 text-fg-3" aria-hidden />
            对话历史
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className={cx(buttonClass({ variant: 'ghost' }), 'w-8 px-0')}
          >
            <X className="h-5 w-5" aria-hidden />
          </button>
        </div>

        {/* 内容区域 */}
        <div className="flex-1 flex overflow-hidden">
          {/* 左侧：对话列表 */}
          <div className="w-80 overflow-y-auto border-r border-line">
            {conversations.length === 0 ? (
              <div className="p-8 text-center text-body text-fg-3">
                <MessageSquare className="mx-auto mb-3 h-10 w-10 opacity-50" aria-hidden />
                <p>暂无历史对话</p>
              </div>
            ) : (
              <div className="p-2 space-y-1">
                {conversations.map((conv) => (
                  <button
                    key={conv.date}
                    onClick={() => loadConversationDetail(conv.date)}
                    type="button"
                    aria-current={selectedDate === conv.date ? 'true' : undefined}
                    className={cx(
                      'w-full rounded-md p-3 text-left transition-colors',
                      selectedDate === conv.date ? 'bg-accent/15 text-fg' : 'text-fg-2 hover:bg-surface-2 hover:text-fg',
                      focusRing,
                    )}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="mb-1 flex items-center text-body">
                          <Calendar className="mr-1.5 h-4 w-4" aria-hidden />
                          {formatDate(conv.date)}
                        </div>
                        <div className={cx('text-caption text-fg-3', numeric)}>
                          {formatSize(conv.size)}
                        </div>
                      </div>
                      <ChevronRight className="h-5 w-5 text-fg-3" aria-hidden />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 右侧：对话详情 */}
          <div className="flex-1 overflow-y-auto">
            {isLoading ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center">
                  <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-b-2 border-accent motion-reduce:animate-none" aria-hidden></div>
                  <p className="text-body text-fg-3">加载中...</p>
                </div>
              </div>
            ) : selectedConversation ? (
              <div className="p-6 space-y-6">
                <div className="mb-4 flex items-center justify-between gap-3 border-b border-line pb-4">
                  <h3 className="text-title text-fg-2">
                    {formatDate(selectedDate!)}
                  </h3>
                  <button
                    type="button"
                    onClick={handleResumeConversation}
                    disabled={chatBusy || !!readOnlyReason}
                    className={buttonClass({ variant: 'primary' })}
                    title={readOnlyReason ?? busyHint(conversationOp) ?? '将此对话加载到当前面板继续交流'}
                  >
                    <PlayCircle className="h-4 w-4" aria-hidden />
                    继续此对话
                  </button>
                </div>
                {selectedConversation.map((turn, index) => (
                  <div key={index} className="space-y-4">
                    {/* 用户消息 */}
                    <div className="flex justify-end">
                      <div className="max-w-[70%]">
                        <div className={cx('mb-1 flex items-center justify-end text-caption text-fg-3', numeric)}>
                          <Clock className="mr-1 h-3 w-3" aria-hidden />
                          {formatTime(turn.timestamp)}
                        </div>
                        <div className="rounded-lg bg-accent p-3 text-body text-accent-fg">
                          <div className="whitespace-pre-wrap">{turn.user}</div>
                          {/* 显示保存的图片缩略图 */}
                          {turn.image_paths && turn.image_paths.length > 0 ? (
                            <div className={`mt-2 grid gap-2 ${turn.image_paths.length > 1 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                              {turn.image_paths.map((imgPath: string, imgIdx: number) => (
                                <img 
                                  key={imgIdx}
                                  src={`/api/chat/conversation-images/${imgPath}`}
                                  alt={`图片 ${imgIdx + 1}`}
                                  className="max-w-full rounded-lg max-h-40 object-contain cursor-pointer hover:opacity-80 transition-opacity"
                                  onClick={() => window.open(`/api/chat/conversation-images/${imgPath}`, '_blank')}
                                />
                              ))}
                            </div>
                          ) : turn.images_count && turn.images_count > 0 ? (
                            <div className="mt-2 flex items-center text-caption">
                              <ImageIcon className="mr-1 h-3 w-3" aria-hidden />
                              {turn.images_count} 张图片（早期对话未保存缩略图）
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </div>

                    {/* AI 回复 */}
                    <div className="flex justify-start">
                      <div className="max-w-[70%]">
                        <div className={cx('mb-1 flex items-center text-caption text-fg-3', numeric)}>
                          <Clock className="mr-1 h-3 w-3" aria-hidden />
                          {formatTime(turn.timestamp)}
                        </div>
                        <div className="rounded-lg bg-surface-2 p-3 text-body text-fg">
                          <div className="min-w-0 max-w-none overflow-x-auto break-words">
                            <SafeMarkdown>{turn.assistant}</SafeMarkdown>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-center justify-center h-full">
                <div className="text-center text-body text-fg-3">
                  <MessageSquare className="mx-auto mb-4 h-12 w-12 opacity-30" aria-hidden />
                  <p>选择一个对话查看详情</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
