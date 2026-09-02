"""
会话状态模型定义（单一数据源 = DB submissions 行；这里只描述状态机阶段）

投稿流程收敛为三个阶段：
  UPLOAD  —— 接收媒体/文档（分类自动，媒体与文档不再分开两种状态）
  PREVIEW —— 发布预览面板（编辑/匿名/剧透/发布/取消）
  EDIT    —— 快速编辑单个字段（用 context.user_data['edit_field'] 区分字段）
"""
STATE = {
    'UPLOAD': 1,
    'PREVIEW': 2,
    'EDIT': 3,
}
