// 设置 › 备份与导出: what each backup file holds and what neither does, as stated to the person: the ledger's
// tables (core/ledger.py, the journal in backend/api/ledger.py), the desk store's schemas (every
// `desk_store.register_schema`; tests/test_settings_memory_choices.py fails when a new one appears, so it gets
// a line here) and the files outside both.

export const BACKUP_CONTENTS = {
  ledger: {
    file: 'ledger.sqlite3',
    items: [
      '全部已确认的流水：期初、买卖、分红、入金出金、换汇、拆股和冲销',
      '还没确认的账本方案（记一笔、导入、助手提出的）',
      '研究笔记（计划与复盘 › 笔记）',
    ],
  },
  desk: {
    file: 'desk.sqlite3',
    items: [
      '价格提醒规则和触发记录',
      '交易计划及其与成交的关联',
      '自选列表',
      '助手提出的方案，包括你放弃过的教训和画像修改',
      '盘前、盘后简报和每日例行任务的运行记录',
      '导入记录、列对应和代码别名',
      '各账户的费率',
      '站内通知和设置向导的完成状态',
    ],
  },
} as const

export const NOT_IN_BACKUPS: string[] = [
  '助手的记忆：交易画像、情绪触发点和教训（后端目录下的 data/memory.json）',
  '模型设置和 API 密钥（config.json、.env）',
  '研究工作台的实验、回测历史和日线缓存（各自单独的数据库）',
  '旧版持仓文件和收盘快照（数据目录里的 JSON 和 CSV 文件）',
]
