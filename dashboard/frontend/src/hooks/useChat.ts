import { useCallback, useState } from 'react'
import { useAppStore } from '@/store'
import { chatService } from '@/services'

export function useChat() {
  const [isStreaming, setIsStreaming] = useState(false)

  const {
    messages,
    isLoading,
    currentResponse,
    suggestions,
    risks,
    addMessage,
    setLoading,
    setCurrentResponse,
    appendToCurrentResponse,
    setSuggestions,
    setRisks,
    clearMessages,
  } = useAppStore()

  const sendMessage = useCallback(
    async (message: string, useStream = false) => {
      if (!message.trim() || isLoading) return

      addMessage({ role: 'user', content: message })
      setLoading(true)
      setCurrentResponse('')

      try {
        if (useStream) {
          setIsStreaming(true)
          await chatService.streamMessage(
            message,
            (chunk) => {
              appendToCurrentResponse(chunk)
            },
            () => {
              setIsStreaming(false)
              // 完成后将流式内容添加到消息
              const finalResponse = useAppStore.getState().currentResponse
              addMessage({ role: 'assistant', content: finalResponse })
              setCurrentResponse('')
            }
          )
        } else {
          const response = await chatService.sendMessage(message, true)
          addMessage({ role: 'assistant', content: response.response })

          if (response.suggestions) {
            setSuggestions(response.suggestions)
          }
          if (response.risks) {
            setRisks(response.risks)
          }
        }
      } catch (error) {
        console.error('Chat error:', error)
        addMessage({
          role: 'assistant',
          content: '抱歉，发生了错误，请稍后重试。',
        })
      } finally {
        setLoading(false)
      }
    },
    [
      isLoading,
      addMessage,
      setLoading,
      setCurrentResponse,
      appendToCurrentResponse,
      setSuggestions,
      setRisks,
    ]
  )

  const sendMessageWithImage = useCallback(
    async (message: string, images: File[]) => {
      if (isLoading) return

      const imgDesc = images.map(img => img.name).join(', ')
      addMessage({ role: 'user', content: `${message}\n[上传了图片: ${imgDesc}]` })
      setLoading(true)

      try {
        const response = await chatService.sendMessageWithImage(message, images, true)
        addMessage({ role: 'assistant', content: response.response })

        if (response.suggestions) {
          setSuggestions(response.suggestions)
        }
        if (response.risks) {
          setRisks(response.risks)
        }
      } catch (error) {
        console.error('Chat error:', error)
        addMessage({
          role: 'assistant',
          content: '抱歉，发生了错误，请稍后重试。',
        })
      } finally {
        setLoading(false)
      }
    },
    [isLoading, addMessage, setLoading, setSuggestions, setRisks]
  )

  return {
    messages,
    isLoading,
    isStreaming,
    currentResponse,
    suggestions,
    risks,
    sendMessage,
    sendMessageWithImage,
    clearMessages,
  }
}
