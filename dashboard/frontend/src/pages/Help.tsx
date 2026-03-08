import { HelpCircle, MessageSquare, Wallet, TrendingUp, Brain, Keyboard } from 'lucide-react'

export default function Help() {
  const features = [
    {
      icon: MessageSquare,
      title: '智能对话',
      desc: '与 AI 交易助手对话，获取交易建议、风险分析和情绪支持。支持上传持仓截图进行分析。'
    },
    {
      icon: Wallet,
      title: '持仓管理',
      desc: '记录和管理你的股票持仓，自动计算盈亏，实时刷新行情数据。'
    },
    {
      icon: TrendingUp,
      title: '行情查询',
      desc: '查询 A 股、港股、美股的实时行情数据，快速了解市场动态。'
    },
    {
      icon: Brain,
      title: '记忆系统',
      desc: 'AI 会记住你的交易风格、偏好和历史教训，提供个性化的建议。'
    },
  ]

  const shortcuts = [
    { key: 'Enter', desc: '发送消息' },
    { key: 'Shift + Enter', desc: '换行' },
    { key: 'Ctrl + V', desc: '粘贴图片' },
    { key: '拖拽', desc: '拖拽图片到对话框上传' },
  ]

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-bold flex items-center">
        <HelpCircle className="h-6 w-6 mr-2" />
        帮助中心
      </h1>

      {/* 功能介绍 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold mb-4">功能介绍</h2>
        <div className="grid grid-cols-2 gap-4">
          {features.map((feature) => {
            const Icon = feature.icon
            return (
              <div key={feature.title} className="p-4 bg-slate-700/50 rounded-lg">
                <div className="flex items-center mb-2">
                  <Icon className="h-5 w-5 text-primary-400 mr-2" />
                  <h3 className="font-medium">{feature.title}</h3>
                </div>
                <p className="text-sm text-slate-400">{feature.desc}</p>
              </div>
            )
          })}
        </div>
      </div>

      {/* 快捷键 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold flex items-center mb-4">
          <Keyboard className="h-5 w-5 mr-2" />
          快捷操作
        </h2>
        <div className="space-y-3">
          {shortcuts.map((shortcut) => (
            <div key={shortcut.key} className="flex items-center justify-between p-3 bg-slate-700/50 rounded-lg">
              <span className="text-slate-300">{shortcut.desc}</span>
              <kbd className="px-3 py-1 bg-slate-600 rounded text-sm font-mono">{shortcut.key}</kbd>
            </div>
          ))}
        </div>
      </div>

      {/* 使用提示 */}
      <div className="bg-slate-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold mb-4">使用提示</h2>
        <ul className="space-y-2 text-slate-300">
          <li className="flex items-start">
            <span className="text-primary-400 mr-2">*</span>
            上传持仓截图时，支持同花顺、东方财富等主流软件的截图格式
          </li>
          <li className="flex items-start">
            <span className="text-primary-400 mr-2">*</span>
            AI 建议仅供参考，不构成投资建议，请根据自身情况谨慎决策
          </li>
          <li className="flex items-start">
            <span className="text-primary-400 mr-2">*</span>
            记录交易教训和情绪触发点，帮助 AI 更好地理解你的交易习惯
          </li>
          <li className="flex items-start">
            <span className="text-primary-400 mr-2">*</span>
            行情数据可能存在延迟，以券商实时行情为准
          </li>
        </ul>
      </div>

      {/* 版本信息 */}
      <div className="text-center text-slate-500 text-sm">
        交易助手 v3.0 | 基于大语言模型的智能交易辅助系统
      </div>
    </div>
  )
}
