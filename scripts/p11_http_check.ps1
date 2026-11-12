# P11 dashboard task-dispatch HTTP acceptance (GET + guarded POST; POST only crawl-enqueue/analyze, never write/publish)
$ErrorActionPreference = 'Continue'
$results = @()
$tmp = Join-Path $env:TEMP 'ttxt_p11_http'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Set-Content -Path (Join-Path $tmp 'b_crawl.json')   -Value '{"source_id":"__SRC__"}'               -Encoding ascii -NoNewline
Set-Content -Path (Join-Path $tmp 'b_analyze.json') -Value '{"category":"demo"}'                   -Encoding ascii -NoNewline
Set-Content -Path (Join-Path $tmp 'b_bad.json')     -Value '{"category":'                          -Encoding ascii -NoNewline
Set-Content -Path (Join-Path $tmp 'b_empty.json')   -Value '{}'                                    -Encoding ascii -NoNewline

function Invoke-Curl {
    param([string]$Method, [string]$Url, [string[]]$Headers, [string]$BodyFile)
    $cargs = @('-s', '-S', '-X', $Method, $Url, '-w', '\n%{http_code}')
    foreach ($h in $Headers) { $cargs += @('-H', $h) }
    if ($BodyFile) { $cargs += @('--data-binary', ('@' + $BodyFile)) }
    $out = & curl.exe @cargs 2>$null
    $text = ($out -join "`n")
    $idx = $text.LastIndexOf("`n")
    if ($idx -lt 0) { return @{ code = '000'; body = $text } }
    return @{ code = $text.Substring($idx + 1); body = $text.Substring(0, $idx) }
}

$base = 'http://127.0.0.1:8765'
$okH = @('Content-Type: application/json', 'X-TTXT-Task: 1', 'Origin: http://127.0.0.1:8765')

function Check($name, $cond, $detail) {
    $script:results += [pscustomobject]@{ name = $name; pass = [bool]$cond; detail = ($detail -replace "`r?`n", ' ') }
}

# 1. GET /api/sources
$r = Invoke-Curl GET "$base/api/sources" @() $null
$srcs = $null
try { $srcs = $r.body | ConvertFrom-Json } catch {}
Check 'GET /api/sources 200' ($r.code -eq '200' -and $srcs) "code=$($r.code)"

# 2. GET /api/tasks
$r = Invoke-Curl GET "$base/api/tasks?limit=20" @() $null
$tasks = $null
try { $tasks = $r.body | ConvertFrom-Json } catch {}
Check 'GET /api/tasks 200+shape' ($r.code -eq '200' -and $null -ne $tasks.pending -and $null -ne $tasks.schedule) "code=$($r.code)"

# 3. POST crawl (formal source, valid headers) -> 202
$srcId = $null
foreach ($s in $srcs) { if ($s.enabled -and -not $s.demo) { $srcId = $s.id; break } }
if (-not $srcId) { $srcId = 'ghost_source' }
Set-Content -Path (Join-Path $tmp 'b_crawl.json') -Value ('{"source_id":"' + $srcId + '"}') -Encoding ascii -NoNewline
$r = Invoke-Curl POST "$base/api/tasks/crawl" $okH (Join-Path $tmp 'b_crawl.json')
$expect = if ($srcId -eq 'ghost_source') { '400' } else { '202' }
Check "POST crawl $srcId -> $expect" ($r.code -eq $expect) "code=$($r.code) body=$($r.body)"

# 4. POST analyze demo -> 200 (real run_analysis, idempotent)
$r = Invoke-Curl POST "$base/api/tasks/analyze" $okH (Join-Path $tmp 'b_analyze.json')
Check 'POST analyze(demo) 200' ($r.code -eq '200') "code=$($r.code)"

# 5. missing X-TTXT-Task -> 403
$r = Invoke-Curl POST "$base/api/tasks/analyze" @('Content-Type: application/json', 'Origin: http://127.0.0.1:8765') (Join-Path $tmp 'b_analyze.json')
Check 'POST no task header 403' ($r.code -eq '403') "code=$($r.code)"

# 6. evil Origin -> 403
$r = Invoke-Curl POST "$base/api/tasks/analyze" @('Content-Type: application/json', 'X-TTXT-Task: 1', 'Origin: http://evil.example') (Join-Path $tmp 'b_analyze.json')
Check 'POST evil Origin 403' ($r.code -eq '403') "code=$($r.code)"

# 7. non-loopback Host -> 403
$r = Invoke-Curl POST "$base/api/tasks/analyze" @('Content-Type: application/json', 'X-TTXT-Task: 1', 'Host: 10.0.0.1:8765') (Join-Path $tmp 'b_analyze.json')
Check 'POST non-loopback Host 403' ($r.code -eq '403') "code=$($r.code)"

# 8. malformed JSON -> 400
$r = Invoke-Curl POST "$base/api/tasks/analyze" $okH (Join-Path $tmp 'b_bad.json')
Check 'POST malformed JSON 400' ($r.code -eq '400') "code=$($r.code)"

# 9. write/publish endpoint -> 404 and states not dispatchable
$r = Invoke-Curl POST "$base/api/tasks/write" $okH (Join-Path $tmp 'b_empty.json')
Check 'POST write 404 refused' ($r.code -eq '404') "code=$($r.code) body=$($r.body)"

# 10. non-task path -> 405
$r = Invoke-Curl POST "$base/api/other" $okH (Join-Path $tmp 'b_empty.json')
Check 'POST non-task path 405' ($r.code -eq '405') "code=$($r.code)"

# 11. IPv6 loopback Host/Origin must be accepted (P11 fix regression guard).
# Server binds IPv4 127.0.0.1 only (by design), so validate the [::1] parsing fix via header override.
$r = Invoke-Curl POST "$base/api/tasks/analyze" @('Content-Type: application/json', 'X-TTXT-Task: 1', 'Host: [::1]:8765', 'Origin: http://[::1]:8765') (Join-Path $tmp 'b_analyze.json')
Check 'POST [::1] Host/Origin 200' ($r.code -eq '200') "code=$($r.code)"

$results | ForEach-Object { '{0}  {1}  {2}' -f ($(if ($_.pass) {'PASS'} else {'FAIL'})), $_.name, $_.detail }
$failed = @($results | Where-Object { -not $_.pass })
'---- {0}/{1} passed ----' -f ($results.Count - $failed.Count), $results.Count
if ($failed.Count) { exit 1 }
