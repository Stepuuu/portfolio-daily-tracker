import { useState, useEffect } from 'react'
import { X, Calendar, MessageSquare, Clock, ChevronRight, PlayCircle, Image as ImageIcon } from 'lucide-react'
import { chatService } from '@/services'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useAppStore } from '@/store'
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
  const setSuggestions = useAppStore(state => state.setSuggestions)
  const setRisks = useAppStore(state => state.setRisks)

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
    if (!selectedDate) return
    setIsLoading(true)
    try {
      // 告诉后端加载这个会话为当前上下文
      const result = await chatService.loadConversation(selectedDate)
      const { turns, suggestions: savedSuggestions, risks: savedRisks } = result
      
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

      // 恢复建议与风险
      if (savedSuggestions && savedSuggestions.length > 0) {
        setSuggestions(savedSuggestions)
      }
      if (savedRisks && savedRisks.length > 0) {
        setRisks(savedRisks)
      }

      onClose()
    } catch (error) {
      console.error('Failed to resume conversation:', error)
      alert("加载历史对话失败")
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
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 rounded-lg w-full max-w-6xl h-[80vh] flex flex-col">
        {/* 头部 */}
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <h2 className="text-xl font-semibold flex items-center">
            <MessageSquare className="h-6 w-6 mr-2 text-primary-500" />
            对话历史
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* 内容区域 */}
        <div className="flex-1 flex overflow-hidden">
          {/* 左侧：对话列表 */}
          <div className="w-80 border-r border-slate-700 overflow-y-auto">
            {conversations.length === 0 ? (
              <div className="p-8 text-center text-slate-400">
                <MessageSquare className="h-12 w-12 mx-auto mb-3 opacity-50" />
                <p>暂无历史对话</p>
              </div>
            ) : (
              <div className="p-2 space-y-1">
                {conversations.map((conv) => (
                  <button
                    key={conv.date}
                    onClick={() => loadConversationDetail(conv.date)}
                    className={`w-full p-3 rounded-lg text-left transition-colors ${
                      selectedDate === conv.date
                        ? 'bg-primary-600'
                        : 'hover:bg-slate-700'
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center text-sm mb-1">
                          <Calendar className="h-4 w-4 mr-1.5" />
                          {formatDate(conv.date)}
                        </div>
                        <div className="text-xs text-slate-400">
                          {formatSize(conv.size)}
                        </div>
                      </div>
                      <ChevronRight className="h-5 w-5 text-slate-500" />
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
                  <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-500 mx-auto mb-4"></div>
                  <p className="text-slate-400">加载中...</p>
                </div>
              </div>
            ) : selectedConversation ? (
              <div className="p-6 space-y-6">
                <div className="flex items-center justify-between mb-4 border-b border-slate-700 pb-4">
                  <h3 className="text-lg font-medium text-slate-300">
                    {formatDate(selectedDate!)}
                  </h3>
                  <button
                    onClick={handleResumeConversation}
                    className="flex items-center px-4 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors text-sm font-medium"
                    title="将此对话加载到当前面板继续交流"
                  >
                    <PlayCircle className="h-4 w-4 mr-2" />
                    继续此对话
                  </button>
                </div>
                {selectedConversation.map((turn, index) => (
                  <div key={index} className="space-y-4">
                    {/* 用户消息 */}
                    <div className="flex justify-end">
                      <div className="max-w-[70%]">
                        <div className="flex items-center justify-end mb-1 text-xs text-slate-400">
                          <Clock className="h-3 w-3 mr-1" />
                          {formatTime(turn.timestamp)}
                        </div>
                        <div className="bg-primary-600 rounded-lg p-3">
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
                            <div className="mt-2 text-xs text-primary-200 flex items-center">
                              <ImageIcon className="h-3 w-3 mr-1" />
                              {turn.images_count} 张图片（早期对话未保存缩略图）
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </div>

                    {/* AI 回复 */}
                    <div className="flex justify-start">
                      <div className="max-w-[70%]">
                        <div className="flex items-center mb-1 text-xs text-slate-400">
                          <Clock className="h-3 w-3 mr-1" />
                          {formatTime(turn.timestamp)}
                        </div>
                        <div className="bg-slate-700 rounded-lg p-3">
                          <div className="prose prose-invert prose-sm max-w-none prose-td:border prose-td:border-slate-600 prose-th:border prose-th:border-slate-600 prose-th:bg-slate-800 prose-table:w-auto overflow-x-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.assistant}</ReactMarkdown>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-center justify-center h-full">
                <div className="text-center text-slate-400">
                  <MessageSquare className="h-16 w-16 mx-auto mb-4 opacity-30" />
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
