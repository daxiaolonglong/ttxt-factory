# P32 逐跳 SSRF 安全边界(2026-11-16)

## 一句话结论

外站页面会把抓取程序 302 到 `127.0.0.1`(东财防机器人就是这么干的),现在**每一跳都重新校验**,本机/私网目标在静态抓取、浏览器渲染、iframe、robots 四条路上全部走不通;公网采集、robots 语义、限速都没被弄坏(实测未误杀)。内网保护一点没放宽。

## 改了什么(只在这几个文件里)

| 文件 | 改动 |
| --- | --- |
| `ttxt/crawler/url_safety.py`(**新增**) | 唯一判定入口。`check_url`(scheme 白名单、字面 IP 判定、域名解析后逐 IP 判定)、`check_cached`(浏览器每个请求用)、`walk`(逐跳跟随:每跳重新解析、重新判定、跳数预算 `DEFAULT_MAX_HOPS=5`、环路检测)、`UnsafeURLError`。拒绝范围:环回、私网、链路本地、组播、保留段、IPv6 本地/ULA/6to4,外加 `0.0.0.0/8`、`198.18.0.0/15`、三个文档段;`localhost` 与 `*.localhost` **按主机名直接拒,不等 DNS** |
| `ttxt/crawler/fetcher.py` | ①`Fetcher.get(..., follow_redirects=False)` 显式关自动跳转(拿不到该关键字就终止报错,不静默用默认值);②`fetch_page()` 初始 URL 先过 `check_url`;③静态路径改成 `url_safety.walk` 逐跳(记 `crawl.fetch.redirect_chain`);④`_browser_kwargs()` 注入 `page_setup=_install_request_guard` + `--host-resolver-rules`(两层),拦截器对主文档/子资源/iframe/弹窗统一 `abort("blockedbyclient")` 并记 `crawl.browser.blocked_by_ssrf_guard`;⑤Service Worker 等效隔离:`context.on("serviceworker")` 一旦出现立刻 `stop()`;⑥跳转链被拒时终止不重试(DNS 抖动才重试) |
| `ttxt/crawler/robots.py` | `urlopen` 自动跳转换成「禁跳转 opener + `url_safety.walk`」;404/410 放行、其他非 200 记 unknown 的既有语义保持不变;`fetch_rules()`/`gate()` 不暴露任何绕过参数 |
| `10_tests/test_p32_ssrf.py`(**新增**) | 85 项:IP 黑名单、公网域名解析出私网 IP、DNS 失败不等于安全、逐跳语义(环路/预算/303/3xx 无 Location)、初始 URL 拦截、静态路径 `follow_redirects=False`、浏览器参数与拦截器、SW 停用、robots 逐跳、**源码扫描:生产模块不得出现把 `allow_private_ips` 置真的写法**、本机模型网关探活**故意不套**安全边界(有测试钉住) |
| `10_tests/test_p10_reliability.py` | 3 处对 `127.0.0.1` 的 robots 用例显式传 `allow_private=True`(只有测试能传,生产不传) |
| `10_tests/test_p12_crawler_hardening.py` | 加 autouse 夹具统一喂公网 IP:该模块只测状态码与重试语义,P32 后假域名会被边界判成解析失败,与它要测的东西无关 |
| `10_tests/test_crawler_stackcheck.py` | 「关掉磁盘缓存」的断言从「没有 extra_flags」改成「没有 `--disk-cache-size=` 旗标,但 SSRF 规则旗标必须还在」 |
| `10_tests/conftest.py` | `00_control/scheduler_state.json` 移出前后指纹比对:P31 无人值守后调度每周期重写心跳,字节/mtime 必然变(实测 sha256 一样只差 mtime),按规则 51 同化处理,守卫在 doctor 的 `ops.scheduler_heartbeat` |
| `07_rules/CORE_RULES.md` | 新增规则 52(把下面所有实测坑写进规矩) |

## 实测证据(硬指标 = 本机 HTTP 服务被命中次数)

脚本 `07_tmp/p32/smoke_browser.py`,输出 `07_tmp/p32/smoke_browser.txt`;三组 Scrapling 会话串行跑,变体基线取未改写的原始实现(脚本自己踩过坑:第二组继承了第一组被改写后的 `_browser_kwargs`,那轮结果作废重跑)。

| 变体 | `127.0.0.1` | `127.0.0.2` | `localhost` | `[::1]` | iframe 子资源 | 本机服务命中 |
| --- | --- | --- | --- | --- | --- | --- |
| **A 生产(拦截器+规则)** | `ERR_BLOCKED_BY_CLIENT` | `ERR_BLOCKED_BY_CLIENT` | `ERR_BLOCKED_BY_CLIENT` | `ERR_BLOCKED_BY_CLIENT` | 拦住 | **0** |
| **B 只有拦截器** | 拦住 | 拦住 | 拦住 | 拦住 | 拦住 | **0** |
| **C 只有规则** | 200(**已记录 GAP**) | `ERR_NAME_NOT_RESOLVED` | `ERR_NAME_NOT_RESOLVED` | `ERR_NAME_NOT_RESOLVED` | 命中 1(GAP) | 3 |

没有误杀采集(同一会话内):

- `fetch_page("https://example.com/", render=True)` → 200、正文 1989 字符、1.3 秒(浏览器渲染 + 代理出网正常)。
- 静态路径 `http://example.com/`(真实 301 跳转链)与 `https://example.com/` → 都 200、正文 577 字符 ⇒ 逐跳 walk 没把正常跳转弄断。
- `robots`:`https://www.bing.com` 拿到 98 条规则(逐跳校验后 robots 仍工作);`http://127.0.0.1:PORT/` → unknown、原因「字面 IP 属于禁止地址:127.0.0.1」(robots 也不能绕过)。
- Service Worker:会话里出现过 0 个;停用工有单测钉住(出现即 `stop()`)。
- 生产日志留痕:本轮 `blocked_by_ssrf_guard` 事件 26 条,带 url/reason/ips。

## 第二层(host-resolver-rules)的边界与代价 —— 必须如实记

1. **本机 Windows 系统代理本身就是回环地址** `127.0.0.1:57777`(`ProxyEnable=1`)。`MAP 127.* ~NOTFOUND` 会把代理主机一起打死,浏览器公网采集全断(实测 `net::ERR_PROXY_CONNECTION_FAILED`)。所以规则最前面放恒等映射 `MAP 127.0.0.1 127.0.0.1`(Chromium 先命中先生效)。实测之后:`127.0.0.2` 仍 `ERR_NAME_NOT_RESOLVED`、公网页照常 200。
   **代价:第二层对代理主机这一条(127.0.0.1)不设防**,变体 C 实测它进得来。这一条完全靠第一层拦截器 + SW 停用兜住(变体 A/B 实测命中 0)。又因为 `ProxyOverride` 里有 `localhost;127.*;192.168.*;10.*;172.16.*…`,内网目标根本不经代理直连,所以**第一层才是主控**,第二层是兜底。
2. **规则串里带方括号是非法语法**:实测 `MAP [::1] ~NOTFOUND` 会让 Chromium 判定语法非法并**丢掉整串规则**,`localhost`、`127.*` 全部静默失效(这就是第一轮实测「第二层没用」的真相)。IPv6 必须写无括号 `MAP ::1 ~NOTFOUND`。`ssrf_host_resolver_rules()` 加了启动期自检:发现方括号直接抛错,不许带着废规则启动浏览器。
3. **有系统代理时,规则对经代理转发的公网主机不起作用**(解析交给代理)。它只管直连主机 —— 恰好就是内网那批。不能拿「公网主机没被规则拦」当漏洞证据。
4. SW 记账用弱引用:实测 `id()` 会在对象回收后复用同一数值,导致监听漏挂(第一版单测就是这么红的)。

## 已知残余与未验证

- **DNS 重绑定残余(未验证,未完全关闭)**:「解析一次」与「真正连接」之间有窗口。本机 `curl_cffi 0.17` 没有 `CURLOPT_RESOLVE` 之类把域名钉死到已校验 IP 的能力,所以静态路径无法把连接目标锁到已验证 IP。缓解:跳转链每一跳重新解析重新判定、浏览器每个请求重新判定(`check_cached`)。要彻底关闭需要换 HTTP 客户端或自己接管 socket,本轮不做。
- 变体 C 的 `127.0.0.1` GAP 如上,是代理配置造成的结构性代价,不是可配置开关。
- `robots` 走 urllib 的 TLS 信任链读不到 `https://example.com/robots.txt`(`unable to get local issuer certificate`)。**原生 urllib 基线同样失败**、git HEAD 的旧 robots 也是同一个 `urllib.request.urlopen`(第 85 行)⇒ 改动前就存在,不是本轮引入;curl_cffi 通道能取到 example.com。robots unknown 时既有语义是不采该站,保持不变。
- 生产进程已换代码:`schedule`/`worker` 子进程已于 21:29:05 重启(见「生产生效与真实站点核查」)。生效性是按「进程启动时刻晚于改动落盘」推断的,**没有逐进程读内存验证**;下一次 `crawl_enqueue_4h`(约 22:54)与 Worker 实际抓取的运行结果属于尚未观测的后续事项。

## 测试

- `10_tests/test_p32_ssrf.py` + `10_tests/test_p10_reliability.py`:**103 passed**。
- 涉及抓取的五组(`test_crawler_stackcheck`、`test_p12_crawler_hardening`、`test_p9_toutiao`、`test_p32_ssrf`、`test_p10_reliability`):**146 passed**。
- 全量 `10_tests`:**578 passed / 0 failed**(152 秒)。本轮第一次全量跑出的 3 失败 1 错误已按上表逐项修掉:`test_p12` 两处是假域名被 SSRF 边界判成解析失败(与状态码语义无关)、`test_crawler_stackcheck` 一处断言过时、`test_p9_toutiao` 的 teardown 误报来自无人值守调度重写心跳文件。

## 生产生效与真实站点核查(实测)

- 子进程换代码:`ttxt supervisor --stop` → 两个子进程优雅收尾(`terminated=true, killed=false`,退出码 1)→ `--clear-stop` → `--ensure` 拉起新托管(pid 36464/状态 35268),`scheduler` 与 `worker` 于 **21:29:05** 重启,启动时刻晚于全部 P32 改动落盘 ⇒ 它们加载的是新代码(按进程启动时间推断,不是逐进程读内存验证)。重启后 `ttxt doctor`:**36 ok / 0 failed**,`ops.supervisor`、`ops.scheduler_heartbeat` 均 PASS。
- 真实站点(脚本 `07_tmp/p32/p32_prod_check.py`、`07_tmp/p32/p32_article_check.py`,只读库、不写任何数据库):
  - 首页:`https://www.eastmoney.com/` 200/327923 字符、`https://www.tmtpost.com/` 200/357125、`https://www.chinanews.com.cn/` 200/190318、`https://example.com/` 200/577 ⇒ 逐跳校验没有误杀真实大站;robots 也照常拿到(东财 1 条、tmtpost 5 条、中新网 39 条,全部 unknown=False)。
  - 文章页(从财经库取真实 URL):`finance.eastmoney.com/news/1354,202611163931759035.html` 与 `.../1345,...html` 都是 302(同站 http→https)后 200,正文 58894 / 55281 字符;`www.tmtpost.com/8206814.html`、`/8206996.html` 直接 200,正文 136157 / 130724 字符。
  - **诚实记录:今天这四个真实文章页没有出现「跳转到 127.0.0.1」**（规则 50 记的是 curl 抓取会撞上,今天的跳转是同站 http→https)。所以「真实外站跳内网被拦」这条**没有**用真实站点验证,只有本机 HTTP 服务(变体 A/B 命中 0)与单测(逐跳 + 字面 IP + 域名解析出私网)证明。规则 50 的 `--render` 结论不变。
  - `example.com` 的 robots 仍是 `unknown=True`,原因「网络失败:SSL CERTIFICATE_VERIFY_FAILED」⇒ 既有环境问题(原生 urllib 同样失败),robots unknown 时既有语义是不采该站,本轮未改。
- 队列现状(只读):`crawl_jobs` 83 done / 7 aborted / 0 queued;`model_jobs` 70 done;测试期间生产库无写入(受保护清单指纹一致)。调度最近一次 `crawl_enqueue_4h` 是 18:54:25(入队 11 条),四小时周期到 22:54 左右才会再跑。

## 回滚点

- 备份:`D:\ttxt\11_backups\p32-before-ssrf-20261116-200941.zip`(13486.5 KB / 221 个文件,`testzip()` 通过;跳过被占用的 `00_control/supervisor.lock`、`00_control/worker.lock`)。
- 回滚方式:`git revert` 本轮提交,或从上面的 zip 还原 `ttxt/crawler/`、`10_tests/`。

## 红线重申

不许为了提高采集成功率放宽私网保护。`eastmoney.com → 127.0.0.1` 必须继续被拒,该站文章页只能 `--render` 用本机浏览器渲染(规则 50/52)。本机模型网关探活(`doctor.py`、`model_gate/client.py` 的 `urlopen`)故意不套 url_safety,别顺手加安全把探活弄不通。
