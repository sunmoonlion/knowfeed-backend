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

## 2026-11-03 PDF 正文读法及公开旧版本补加工

明确请求 text 时，现有 PDF 加工器生成保留页界的 UTF-8 正文，仍使用版本绑定的
加工授权。空白扫描文档不报正文就绪，失败保留原件/其它成功读法；不声称提供 OCR
或数值 facts。已有公开交付不再仅凭 readings 非空跳过新需求所缺的读法；非定期公开
原件快速复用也先核所需种类。默认 PDF pages/table 加工不变。
定向 26 passed；包含调度/网页/重复交付/撤权的扩展回归 62 passed，无跳过，42.57s；
ruff/pyright 通过。旧回归期望“不重做缺表”的断言改为实测补加工失败仍 partial，
成功后才 ready。日志 /tmp/luna-supply-feed-regression.log。无线上作业重放/DB 写入。

## 2026-11-04 一季报权益跨行误识别修复

基线 df981b2，分支 fix/statement-label-continuations。现场 733b21b5、73081dc1 的查询均缺 2023/2024/2026 一季报权益；只读取得三份已入库原件并核 SHA-256，用旧/新解析器离线回放。旧解析器将“归属于母公司所有者权益（或 / 数字 / 股东权益）合计”的后半截先匹配成 total_equity；与真正总权益重复而同时丢弃。修复优先完整三行名称，再尝试相邻片段，保留重复科目拒绝和旧版式规则。三份报告各恢复归母权益/总权益本期与比较期4值，共12值；来自原件，不反推、不换公式，raw 行号/页码/标准层 derived_from 可追溯。

加工版本升 periodic-statements-3，规则版本同步。只加读法版本，不覆盖旧内容/读法或既有委托结果。补了三份原始PDF裁出的词/坐标夹具与逐数、逐页、两式独立校验；合成行测试覆盖同一歧义。旧版回放三份均0条相关标准值，新版均4条。原件还存在其他空标签诊断，未把全部抽取宣称完美。

验证：250 passed（抽取、历史版式、跨年、供给契约、季报、readings、HTTP client）；Ruff/Pyright无错。最初12项契约测试因相邻knowbase开发树schema比发布钉版新而失败，保留事实。测试新增 KNOWBASE_USER_DATA_CONTRACT_PATH 支持钉版artifact，仍强制校验消费者锁SHA；最终用qwen发布工位原契约7fcba39f…复跑全过，不升级生产契约。

发布父仓从实际发布 b9464a9 起；不回退已发布前端/admin。需要Qwen发版后以knowfeed正式processing_grant/content/write_facts/release接口补加工三份公共原件，并核新读法。仅部署不会自动修旧表；旧委托钉版不能被偷偷替换。未重开付费任务、未写生产DB。后续：补加工后核 list_inputs/query_facts 新读法，再按原作者方法验证真实计算。


## 2026-11-04 Wind 管理端HTTP接通

修复正式入口两处断点：Wind注册使用collected私有区；试采HTTP传入source_query且允许无主体供应商查询。共享目录服务拒绝错渠道、错资料类型、期间伪装及非供应商渠道查询；参数沿用query_json校验。真实HTTP→排队→worker→审核数据库回归（模拟供应商）和既有Wind/供给测试48 passed；ruff、pyright通过。没有修改线上渠道记录或伪造试采/批准。
