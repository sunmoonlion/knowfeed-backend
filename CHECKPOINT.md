# 2026-11-02 Wind 通用查询与年报证据（开发候选，未发布）

基线 `8f4ee39a297ccd47d2cecb74a968d0291380cc57`。本轮本人要求第二轨作为主演示、巨潮保底，财务与年报都纳入、不限制为财务专线。

Wind 七服务域通用 MCP 适配器，按实时 tools/list 校验参数，复用审核、试采、确认、作业、留存和私有交付；固定 HTTPS 主机，SecretStr 服务器凭据，所有者限定。源工具清单与官方 skill scripts/tool-manifest.json 解析后完全一致（原文件 SHA b19f6c3ebb812ea3e48396dcc2ec266c6bb955bbff7fc8925f33e263eeaad06c；仓内仅排序排版不同）。source_query 参数、每次提案身份进入作业键。原响应/text 可用，不伪造 PDF/pages/facts；保留巨潮原件路径。
渠道 daily_cap=0 表示取消每日总量上限，限速/串行仍有效。quota/auth 持久暂停；未知结果不自动重放。现有 job.payload 记请求意图，原件存储复用下游交付重试；进程丢失响应则报结果未知，防重复计费。试采允许显式 pending，正常发现/执行必须 approved；更新、审批、迟到跨账号需求均不能扩大私有范围。
验证：首轮全量 1078 passed / 13 skipped（/tmp/luna-wind-feed-full.log）；之后新增发现/重试保护的定向回归 73 passed / 1 failed（新增测试 SQL 字面量被 SQLAlchemy 当绑定参数，未掩盖）；修正该测试后最终查询/传输 36 passed（/tmp/luna-wind-feed-final-query.log）。此前失败含测试 dict 属性误用，原日志 /tmp/luna-wind-feed-regression.log 保留。全仓 ruff/pyright 通过，uv lock --check 通过。最终目标用例证明下游重试不再次取数，丢失结果不重发。
真实新适配器年报与财务各 1 次成功；年报返回文本/链接（不是 PDF），财务 4 行/7 列。用户已授权服务器、取消旧 5 次上限，本会话累计 7 次真实数据调用（含之前3次与本轮CLI2次），没有发生额度停止。原值仅私有 /tmp，不进 Git。
待 qwen 配置正式 Secret/owner、目录试采审核和服务间链路；当前未发版、未登记生产渠道。无新增表/迁移。

发布由 qwen/Jenkins；不代点用户采单、不修改线上任务。以下为此前历史：

# 2026-11-02 第二轨：非公开逐读法交付

基线 bb85408f20c78dbda0301315dfd3de0bbfc7d955（qwen 的 fix/supply-delivery 候选）；本轮独立 feat/reading-receipts，不移动发布候选。用户授权在 qwen 发版期间继续独立开发。

修复：不再由 has_readings=True 推断媒体应有读法全齐；消费逐读法回执，只补本人所需且缺失的部分。旧提供方缺字段时只确认 original，经各自授权补加工，不乐观认定齐全。拒绝错受益人/非法读法回执，每份私有副本独立领用与交还授权；不能拿 bob 的加工结果标 alice ready。按读法逐项累积完成结果，后续写入拒绝不丢掉此前成功部分。

knowbase 提供方新增兼容 readings/renditions 响应字段及版本引用；父仓锁与实际 HTTP 请求/回执 schema 测试同步。原有契约定义不变，原请求路径不变。新服务与旧服务混布时允许保守补加工；不把布尔字段改成另一种语义。

验证：完整回归 1043 passed / 13 skipped（/tmp/luna-readings-feed-full-final.txt）；完整回归启动后补充的“逐读法累积、写入途中撤权”最终改动，再跑所有受影响场景 47 passed（/tmp/luna-readings-feed-revocation.txt，collected_readings/review3/web/user_data_contract 四个测试文件）。最终 ruff 全仓通过、pyright 0 errors。最初全量有一条旧测试只期望交还一次授权，现按两个独立私有版本改为两次，旧失败输出 /tmp/luna-readings-feed-full.txt 保留，没有删用例或放松权限。

工程边界：真实 PostgreSQL 作业/HTTP-provider 契约，金融原值及外部渠道均未新增调用。不是供应商/Wind/线上财务验收。跨进程首次交付幂等、版本升级后的完整恢复仍需下一单元；已有部分成功读法的补加工与授权隔离此次已覆盖。

继续 WP-10 作者与运行契约；孵化时作者冻结方法单元，运行时只选用/排序/允许的参数。动态运行能力尚未实现。Wind 仍 3/5 次，W0 待核，不接 W1。不触发 Jenkins、不改集群或部署源。
