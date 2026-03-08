import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Brain, User, Target, BookOpen, AlertTriangle, Plus, Edit2, X, Save, Trash2, RefreshCw } from 'lucide-react'
import { memoryService } from '@/services'

// 编辑用户档案对话框
interface EditProfileDialogProps {
  profile: {
    experience_level?: string
    risk_tolerance?: string
    trading_style?: string
    notes?: string
  }
  onClose: () => void
  onSave: (data: {
    experience_level?: string
    risk_tolerance?: string
    trading_style?: string
    notes?: string
  }) => void
}

function EditProfileDialog({ profile, onClose, onSave }: EditProfileDialogProps) {
  const [experienceLevel, setExperienceLevel] = useState(profile.experience_level || '')
  const [riskTolerance, setRiskTolerance] = useState(profile.risk_tolerance || '')
  const [tradingStyle, setTradingStyle] = useState(profile.trading_style || '')
  const [notes, setNotes] = useState(profile.notes || '')

  const handleSave = () => {
    onSave({
      experience_level: experienceLevel || undefined,
      risk_tolerance: riskTolerance || undefined,
      trading_style: tradingStyle || undefined,
      notes: notes
    })
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-slate-800 rounded-lg p-6 w-full max-w-md">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-lg font-semibold">编辑用户档案</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-slate-400 mb-2">交易经验</label>
            <select
              value={experienceLevel}
              onChange={(e) => setExperienceLevel(e.target.value)}
              className="w-full bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="">请选择</option>
              <option value="beginner">新手（0-1年）</option>
              <option value="intermediate">中级（1-3年）</option>
              <option value="expert">专家（3年以上）</option>
            </select>
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">风险偏好</label>
            <select
              value={riskTolerance}
              onChange={(e) => setRiskTolerance(e.target.value)}
              className="w-full bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="">请选择</option>
              <option value="conservative">保守型（追求稳定收益）</option>
              <option value="moderate">稳健型（平衡风险与收益）</option>
              <option value="aggressive">激进型（追求高收益）</option>
            </select>
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">交易风格</label>
            <select
              value={tradingStyle}
              onChange={(e) => setTradingStyle(e.target.value)}
              className="w-full bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
            >
              <option value="">请选择</option>
              <option value="day">日内交易（当天买卖）</option>
              <option value="swing">波段交易（持有数天到数周）</option>
              <option value="position">中线持仓（持有数周到数月）</option>
              <option value="value">价值投资（长期持有）</option>
            </select>
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">备注</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="其他个人交易偏好或注意事项..."
              rows={3}
              className="w-full bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500 resize-none"
            />
          </div>

          <div className="flex space-x-3 pt-4">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors flex items-center justify-center"
            >
              <Save className="h-4 w-4 mr-2" />
              保存
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// 确认对话框
interface ConfirmDialogProps {
  title: string
  message: string
  onConfirm: () => void
  onCancel: () => void
}

function ConfirmDialog({ title, message, onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-slate-800 rounded-lg p-6 w-full max-w-sm">
        <h3 className="text-lg font-semibold mb-2">{title}</h3>
        <p className="text-slate-400 mb-6">{message}</p>
        <div className="flex space-x-3">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            className="flex-1 px-4 py-2 bg-red-600 hover:bg-red-500 rounded-lg transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Memory() {
  const queryClient = useQueryClient()
  const [isEditingProfile, setIsEditingProfile] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)

  const { data: memory, isLoading, error } = useQuery({
    queryKey: ['memory'],
    queryFn: memoryService.getMemory,
  })

  const [newLesson, setNewLesson] = useState('')
  const [newTrigger, setNewTrigger] = useState('')
  const [newSector, setNewSector] = useState('')

  const updateProfileMutation = useMutation({
    mutationFn: (data: {
      experience_level?: string
      risk_tolerance?: string
      trading_style?: string
      notes?: string
    }) => memoryService.updateProfile(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
    }
  })

  const addLessonMutation = useMutation({
    mutationFn: (lesson: string) => memoryService.addLesson({
      description: lesson,
      lesson_type: 'insight',
      lesson: lesson
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
      setNewLesson('')
    }
  })

  const removeLessonMutation = useMutation({
    mutationFn: (index: number) => memoryService.removeLesson(index),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
    }
  })

  const addTriggerMutation = useMutation({
    mutationFn: (trigger: string) => memoryService.addEmotionalTrigger(trigger),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
      setNewTrigger('')
    }
  })

  const removeTriggerMutation = useMutation({
    mutationFn: (trigger: string) => memoryService.removeEmotionalTrigger(trigger),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
    }
  })

  const addSectorMutation = useMutation({
    mutationFn: (sector: string) => memoryService.addPreferredSector(sector),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
      setNewSector('')
    }
  })

  const removeSectorMutation = useMutation({
    mutationFn: (sector: string) => memoryService.removePreferredSector(sector),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
    }
  })

  const resetMemoryMutation = useMutation({
    mutationFn: () => memoryService.reset(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory'] })
      setShowResetConfirm(false)
    }
  })

  const experienceLevelLabels: Record<string, string> = {
    beginner: '新手',
    intermediate: '中级',
    expert: '专家'
  }

  const riskToleranceLabels: Record<string, string> = {
    conservative: '保守型',
    moderate: '稳健型',
    aggressive: '激进型'
  }

  const tradingStyleLabels: Record<string, string> = {
    day: '日内交易',
    swing: '波段交易',
    position: '中线持仓',
    value: '价值投资'
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Brain className="h-8 w-8 animate-pulse text-primary-500" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <AlertTriangle className="h-12 w-12 text-red-500 mx-auto mb-4" />
          <p className="text-lg text-slate-400">加载记忆数据失败</p>
          <p className="text-sm text-slate-500 mt-2">{String(error)}</p>
        </div>
      </div>
    )
  }

  const profile = memory?.profile || {}
  const preferences = memory?.preferences || {}

  return (
    <div className="p-6 space-y-6">
      {/* 页面标题和一键清空按钮 */}
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold flex items-center">
          <Brain className="h-6 w-6 mr-2" />
          用户记忆
        </h1>
        <button
          onClick={() => setShowResetConfirm(true)}
          className="flex items-center px-4 py-2 bg-red-600/20 text-red-400 hover:bg-red-600/30 rounded-lg transition-colors"
        >
          <RefreshCw className="h-4 w-4 mr-2" />
          一键清空记忆
        </button>
      </div>

      {/* 用户档案 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-semibold flex items-center">
            <User className="h-5 w-5 mr-2" />
            用户档案
          </h2>
          <button
            onClick={() => setIsEditingProfile(true)}
            className="flex items-center px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors text-sm"
          >
            <Edit2 className="h-4 w-4 mr-1" />
            编辑
          </button>
        </div>

        <div className="grid grid-cols-3 gap-6">
          <div>
            <div className="text-sm text-slate-400">交易经验</div>
            <div className="text-lg font-medium mt-1">
              {experienceLevelLabels[profile.experience_level] || '未设置'}
            </div>
          </div>
          <div>
            <div className="text-sm text-slate-400">风险偏好</div>
            <div className="text-lg font-medium mt-1">
              {riskToleranceLabels[profile.risk_tolerance] || '未设置'}
            </div>
          </div>
          <div>
            <div className="text-sm text-slate-400">交易风格</div>
            <div className="text-lg font-medium mt-1">
              {tradingStyleLabels[profile.trading_style] || '未设置'}
            </div>
          </div>
        </div>

        {profile.notes && (
          <div className="mt-4 p-4 bg-slate-700/50 rounded-lg">
            <div className="text-sm text-slate-400 mb-2">备注</div>
            <div className="text-sm text-slate-300 whitespace-pre-wrap">{profile.notes}</div>
          </div>
        )}
      </div>

      {/* 偏好板块 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold flex items-center mb-4">
          <Target className="h-5 w-5 mr-2" />
          偏好板块
        </h2>
        <div className="flex flex-wrap gap-2 mb-4">
          {profile.preferred_sectors?.map((sector: string, index: number) => (
            <span
              key={index}
              className="group px-3 py-1.5 bg-primary-600/20 text-primary-400 rounded-full text-sm flex items-center"
            >
              {sector}
              <button
                onClick={() => removeSectorMutation.mutate(sector)}
                className="ml-2 opacity-0 group-hover:opacity-100 hover:text-red-400 transition-opacity"
                title="删除"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          {(!profile.preferred_sectors || profile.preferred_sectors.length === 0) && (
            <span className="text-slate-400">暂无偏好板块</span>
          )}
        </div>
        <div className="flex space-x-2">
          <input
            type="text"
            placeholder="添加偏好板块，如：新能源"
            value={newSector}
            onChange={(e) => setNewSector(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter' && newSector) {
                addSectorMutation.mutate(newSector)
              }
            }}
            className="flex-1 bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <button
            onClick={() => newSector && addSectorMutation.mutate(newSector)}
            disabled={!newSector || addSectorMutation.isPending}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors disabled:opacity-50"
          >
            <Plus className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* 交易教训 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold flex items-center mb-4">
          <BookOpen className="h-5 w-5 mr-2" />
          交易教训
        </h2>
        <div className="space-y-3 mb-4">
          {memory?.history?.lessons?.map((lesson: any, index: number) => (
            <div key={index} className="group flex items-start space-x-3 p-3 bg-slate-700/50 rounded-lg">
              <BookOpen className="h-5 w-5 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm">{lesson.description || lesson.lesson || lesson}</p>
                {lesson.date && (
                  <p className="text-xs text-slate-400 mt-1">{lesson.date}</p>
                )}
              </div>
              <button
                onClick={() => removeLessonMutation.mutate(index)}
                className="opacity-0 group-hover:opacity-100 p-1 text-slate-400 hover:text-red-400 transition-opacity"
                title="删除"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          {(!memory?.history?.lessons || memory.history.lessons.length === 0) && (
            <p className="text-slate-400">暂无交易教训记录</p>
          )}
        </div>
        <div className="flex space-x-2">
          <input
            type="text"
            placeholder="记录一条交易教训..."
            value={newLesson}
            onChange={(e) => setNewLesson(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter' && newLesson) {
                addLessonMutation.mutate(newLesson)
              }
            }}
            className="flex-1 bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <button
            onClick={() => newLesson && addLessonMutation.mutate(newLesson)}
            disabled={!newLesson || addLessonMutation.isPending}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors disabled:opacity-50"
          >
            <Plus className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* 情绪触发点 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold flex items-center mb-4">
          <AlertTriangle className="h-5 w-5 mr-2" />
          情绪触发点
        </h2>
        <p className="text-sm text-slate-400 mb-4">记录容易让你冲动交易的情况，助手会在对话中提醒你注意</p>
        <div className="flex flex-wrap gap-2 mb-4">
          {preferences.emotional_triggers?.map((trigger: string, index: number) => (
            <span
              key={index}
              className="group px-3 py-1.5 bg-red-600/20 text-red-400 rounded-full text-sm flex items-center"
            >
              {trigger}
              <button
                onClick={() => removeTriggerMutation.mutate(trigger)}
                className="ml-2 opacity-0 group-hover:opacity-100 hover:text-white transition-opacity"
                title="删除"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          {(!preferences.emotional_triggers || preferences.emotional_triggers.length === 0) && (
            <span className="text-slate-400">暂无记录</span>
          )}
        </div>
        <div className="flex space-x-2">
          <input
            type="text"
            placeholder="添加情绪触发点，如：看到涨停就想追"
            value={newTrigger}
            onChange={(e) => setNewTrigger(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter' && newTrigger) {
                addTriggerMutation.mutate(newTrigger)
              }
            }}
            className="flex-1 bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <button
            onClick={() => newTrigger && addTriggerMutation.mutate(newTrigger)}
            disabled={!newTrigger || addTriggerMutation.isPending}
            className="px-4 py-2 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors disabled:opacity-50"
          >
            <Plus className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* 编辑对话框 */}
      {isEditingProfile && (
        <EditProfileDialog
          profile={profile}
          onClose={() => setIsEditingProfile(false)}
          onSave={(data) => updateProfileMutation.mutate(data)}
        />
      )}

      {/* 清空确认对话框 */}
      {showResetConfirm && (
        <ConfirmDialog
          title="确认清空记忆"
          message="此操作将清空所有用户记忆数据，包括用户档案、偏好板块、交易教训和情绪触发点。此操作不可恢复！"
          onConfirm={() => resetMemoryMutation.mutate()}
          onCancel={() => setShowResetConfirm(false)}
        />
      )}
    </div>
  )
}
