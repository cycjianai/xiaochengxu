## ADDED Requirements

### Requirement: 极简顶栏
wx-sniffer 主页顶栏 SHALL 只有「开始抓取」「停止抓取」两个按钮（外加左侧的健康徽章）。SHALL NOT 有「同步主系统」「补齐 UPC」「刷新列表」「下载日志」「清空日志」按钮。「新增商品」手动录入按钮 SHALL 保留（在商品区头部）。

#### Scenario: 顶栏按钮集
- **WHEN** 打开 wx-sniffer 主页
- **THEN** 顶栏 SHALL 只显示「开始抓取」「停止抓取」+ 健康徽章；那 5 个旧按钮 SHALL 都不存在

### Requirement: 商品表格显示「月销」而非「库存」
商品表格 SHALL 有「月销」列（展示 `monthly_sales`），SHALL NOT 把「库存」作为抓取商品的展示列。月销为空/未知时 SHALL 显示「-」。

#### Scenario: 月销列
- **WHEN** 查看商品表格
- **THEN** 价格列后面 SHALL 是「月销」列；某商品 `monthly_sales` 为 `100` → 显示 `100`，为 `None` → 显示 `-`

### Requirement: 自动刷新，无需手动操作
商品表格 SHALL 每 3 秒自动刷新（用户在搜索框打字时跳过这次刷新），日志面板 SHALL 每 2 秒自动刷新。用户 SHALL NOT 需要手动点刷新。

#### Scenario: 抓到新商品自动出现
- **WHEN** addon 抓到一个新商品并入库
- **THEN** 商品表格 SHALL 在 3 秒内自动出现该条目，无需用户点任何按钮

#### Scenario: 日志面板实时更新
- **WHEN** addon 产生新日志（如「[请求] …」「已自动上传后台商品库…」）
- **THEN** 日志面板 SHALL 在 2 秒内显示出来

### Requirement: 登录已移除
wx-sniffer SHALL 不要求登录 —— 打开页面直接进，所有页面/API 无鉴权（单机本地工具）。

#### Scenario: 直接访问
- **WHEN** 浏览器打开 `http://127.0.0.1:5188/`
- **THEN** 直接显示主页（不跳登录页）；所有 `/api/*` 直接可调用
