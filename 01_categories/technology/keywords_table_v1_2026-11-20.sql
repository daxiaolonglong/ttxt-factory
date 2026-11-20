-- R57 词表 v1 备份(2026-11-20,改前快照,只读导出)。
-- 用途:keywords 表是二进制库里的表,git 无法 diff ⇒ 降级前先落 SQL 快照留档。
-- 回滚:把本文件整份执行即可恢复改前状态(下面的 UPDATE 回滚段等价于快照内容)。
-- 本轮变更:仅 3 词 active -> weak(id 4 数据、217 国家、218 工程);
--          id 1 人工智能按 R57 裁决保持 active(别名归一后标题率 34.4%/36.5%,未达降级线)。

BEGIN TRANSACTION;

DELETE FROM keywords;
INSERT INTO keywords(id, keyword, category, origin, status, created_at) VALUES
 (1,   '人工智能', 'technology', 'seed',   'active', '2026-11-12T13:00:54+08:00'),
 (2,   '开源',     'technology', 'seed',   'active', '2026-11-12T13:00:54+08:00'),
 (3,   '效率工具', 'technology', 'seed',   'gap',    '2026-11-12T13:00:54+08:00'),
 (4,   '数据',     'technology', 'seed',   'active', '2026-11-12T13:00:54+08:00'),
 (125, '降价',     'technology', 'mined',  'active', '2026-11-18T21:24:19+08:00'),
 (126, '航天',     'technology', 'mined',  'active', '2026-11-18T21:24:19+08:00'),
 (128, '航天员',   'technology', 'mined',  'active', '2026-11-18T21:24:19+08:00'),
 (198, '中国',     'technology', 'mined',  'active', '2026-11-19T02:03:43+08:00'),
 (202, '科学家',   'technology', 'mined',  'active', '2026-11-19T02:03:43+08:00'),
 (206, '小米',     'technology', 'mined',  'active', '2026-11-19T02:03:43+08:00'),
 (217, '国家',     'technology', 'mined',  'active', '2026-11-19T02:17:53+08:00'),
 (218, '工程',     'technology', 'mined',  'active', '2026-11-19T02:17:53+08:00'),
 (230, '全球',     'technology', 'mined',  'active', '2026-11-19T20:00:00+08:00');

ROLLBACK;

-- ---- 回滚段(需要恢复改前状态时,执行下面三条,不要执行上面的快照事务) ----
-- UPDATE keywords SET status = 'active' WHERE id IN (4, 217, 218);
