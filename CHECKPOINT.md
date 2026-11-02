# 2026-11-02 第二轨：非公开逐读法交付

基线 bb85408f20c78dbda0301315dfd3de0bbfc7d955（qwen 的 fix/supply-delivery 候选）；本轮独立 feat/reading-receipts，不移动发布候选。用户授权在 qwen 发版期间继续独立开发。

修复：不再由 has_readings=True 推断媒体应有读法全齐；消费逐读法回执，只补本人所需且缺失的部分。旧提供方缺字段时只确认 original，经各自授权补加工，不乐观认定齐全。拒绝错受益人/非法读法回执，每份私有副本独立领用与交还授权；不能拿 bob 的加工结果标 alice ready。按读法逐项累积完成结果，后续写入拒绝不丢掉此前成功部分。

knowbase 提供方新增兼容 readings/renditions 响应字段及版本引用；父仓锁与实际 HTTP 请求/回执 schema 测试同步。原有契约定义不变，原请求路径不变。新服务与旧服务混布时允许保守补加工；不把布尔字段改成另一种语义。

验证：完整回归 1043 passed / 13 skipped（/tmp/luna-readings-feed-full-final.txt）；完整回归启动后补充的“逐读法累积、写入途中撤权”最终改动，再跑所有受影响场景 47 passed（/tmp/luna-readings-feed-revocation.txt，collected_readings/review3/web/user_data_contract 四个测试文件）。最终 ruff 全仓通过、pyright 0 errors。最初全量有一条旧测试只期望交还一次授权，现按两个独立私有版本改为两次，旧失败输出 /tmp/luna-readings-feed-full.txt 保留，没有删用例或放松权限。

工程边界：真实 PostgreSQL 作业/HTTP-provider 契约，金融原值及外部渠道均未新增调用。不是供应商/Wind/线上财务验收。跨进程首次交付幂等、版本升级后的完整恢复仍需下一单元；已有部分成功读法的补加工与授权隔离此次已覆盖。

继续 WP-10 作者与运行契约；孵化时作者冻结方法单元，运行时只选用/排序/允许的参数。动态运行能力尚未实现。Wind 仍 3/5 次，W0 待核，不接 W1。不触发 Jenkins、不改集群或部署源。
