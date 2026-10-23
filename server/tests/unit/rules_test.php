<?php
declare(strict_types=1);

/** 純規則單元測試：php server/tests/unit/rules_test.php */

require __DIR__ . '/../../src/core.php';
require __DIR__ . '/../../src/rules.php';
require __DIR__ . '/../../src/auth.php';

$pass = 0;
function check(bool $ok, string $name): void
{
    global $pass;
    if (!$ok) {
        fwrite(STDERR, "FAIL $name\n");
        exit(1);
    }
    $pass++;
}

// Tag（沿用 dev.45）
check(update_tag_names(['中文', 'abc'], 'POST', 'abc') === ['abc', '中文'], 'tag no duplicate on add');
check(update_tag_names(['abc', 'xyz'], 'PATCH', 'abc', 'xyz') === ['xyz'], 'tag rename merges duplicate');
check(update_tag_names(['abc', 'xyz'], 'DELETE', 'abc') === ['xyz'], 'tag delete');
foreach (['PATCH', 'DELETE'] as $m) {
    try {
        update_tag_names(['abc'], $m, 'missing', 'n');
        check(false, "$m missing tag");
    } catch (InvalidArgumentException) {
        check(true, "$m missing tag rejected");
    }
}
check(valid_tag_input(" 中 醫　推 拿 ") === '中醫推拿', 'tag removes all spaces incl. full-width');
check(valid_tag_input('   ') === null, 'tag empty');
check(valid_tag_input(str_repeat('字', 61)) === null, 'tag too long');
check(valid_tag_input(['x']) === null, 'tag non-string');

// 帳號
// dev.55 FILE-02：中文、半形英數字（可含 _ . -）；2～80 字（以字數計）
check(valid_username('wang01') === 'wang01' && valid_username('a') === null, 'username ascii');
check(valid_username('王小明') === '王小明' && valid_username('陳明') === '陳明' && valid_username('王小明2') === '王小明2', 'username chinese');
check(valid_username('ｗａｎｇ') === null && valid_username('王 小明') === null && valid_username('王@') === null, 'username rejects full-width, space, symbols');
check(valid_username(str_repeat('王', 80)) !== null && valid_username(str_repeat('王', 81)) === null, 'username length counts characters');
check(valid_username("\xff\xfe") === null && valid_username('ricky.lin_01-a') === 'ricky.lin_01-a', 'username invalid utf8, legacy ascii');
// dev.55 ROLE-03：權限只有 user／admin／super
check(valid_role('user') === 'user' && valid_role('admin') === 'admin' && valid_role('super') === 'super', 'roles');
check(valid_role('mini_admin') === null && valid_role('助教') === null && valid_role('lecturer') === null, 'mini_admin and titles are not roles');
check(ROLE_LEVEL === ['user' => 1, 'admin' => 2, 'super' => 3], 'role levels 1/2/3');
// dev.55 WOL-02：LAN IP 必須是私有網段的單一主機
check(valid_lan_ipv4('10.3.3.21') === '10.3.3.21' && valid_lan_ipv4('192.168.1.5') === '192.168.1.5', 'lan ip ok');
check(valid_lan_ipv4('10.3.3.255') === null && valid_lan_ipv4('10.3.3.0') === null && valid_lan_ipv4('8.8.8.8') === null
    && valid_lan_ipv4('255.255.255.255') === null && valid_lan_ipv4('abc') === null, 'lan ip rejects broadcast/public');
check(valid_password('1234567') === null && valid_password('12345678') === '12345678', 'password min 8');
check(valid_date('2024-02-29') === '2024-02-29' && valid_date('2023-02-29') === null, 'date');

// 自然排序
$rows = natural_sort_by([['n' => '0010'], ['n' => '0002'], ['n' => '0001_上集'], ['n' => 'A2'], ['n' => 'a10']], 'n');
check(array_column($rows, 'n') === ['0001_上集', '0002', '0010', 'A2', 'a10'], 'natural sort');

// 路徑
check(safe_rel_path('基礎班/第一堂課/a b.mp4') !== null, 'path ok');
foreach (['../x', '/etc/passwd', 'a//b', 'a/./b', "a\\b", "a\x00b"] as $bad) check(safe_rel_path($bad) === null, 'path bad ' . json_encode($bad));
// dev.59：/Media 第一層的系統資料夾不能當教材；只看第一層
foreach (['playback/1.mp4', 'Subtitles/1.vtt', 'doc-cache/2.pdf', 'Backups/x.mp4', 'installers/a.pdf', 'lost+found/x', '.zfs/x', '.istudy-media'] as $bad)
    check(!library_rel_ok($bad), 'system dir ' . $bad);
foreach (['課程A/第一堂/影片01.mp4', '課程A/playback.mp4', '課程A/subtitles/1.mp4', 'V06.mp4', '課程 B/a: b?#.mp4'] as $ok)
    check(library_rel_ok($ok), 'library ok ' . $ok);
// dev.61：Outgo 是系統資料夾（不分大小寫）；Outgo 內部路徑另有檢查（不允許隱藏項目、跳出）
foreach (['Outgo/a.mp3', 'outgo/x/y.wav', 'OUTGO', 'Outgo'] as $bad) check(!library_rel_ok($bad), 'outgo is system dir ' . $bad);
check(library_rel_ok('課程A/Outgo/a.mp4'), 'outgo only at top level');
foreach (['a.mp3', '第一期/第3堂/上課錄音.mp3', '資料夾'] as $ok) check(outgo_rel_ok($ok), 'outgo rel ok ' . $ok);
foreach (['', '../a.mp3', '/etc/passwd', '.a.mp3.asr.json', 'x/.hidden/a.mp3', 'a//b', "a\\b", 'x/../y'] as $bad)
    check(!outgo_rel_ok($bad), 'outgo rel bad ' . json_encode($bad));
check(accel_path('/_protected/original', '基礎班/a b.mp4') === '/_protected/original/' . rawurlencode('基礎班') . '/a%20b.mp4', 'accel path encoding');

// MAC、IP
check(normalize_mac('AA-BB-CC-00-11-22') === 'aa:bb:cc:00:11:22', 'mac normalize');
check(normalize_mac('00:00:00:00:00:00') === null && normalize_mac('zz') === null, 'mac invalid');
check(is_lan_ip('10.3.3.21') && is_lan_ip('192.168.1.5') && is_lan_ip('172.20.0.3') && !is_lan_ip('8.8.8.8') && !is_lan_ip('172.32.0.1'), 'lan ip');

// 字幕
$srt = "1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>第一句</i>\r\n\r\n2\r\n00:01:03,000 --> 00:01:04,000\r\n第二句\r\n第二行\r\n";
$cues = parse_subtitles($srt);
check(count($cues) === 2 && $cues[0] === [1000, 2500, '第一句'] && $cues[1][2] === "第二句\n第二行", 'parse srt');
$vtt = cues_to_vtt($cues);
check(str_starts_with($vtt, "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\n"), 'to vtt');
check(parse_subtitles($vtt) === $cues, 'vtt roundtrip');
check(str_contains(cues_to_srt($cues), "2\n00:01:03,000 --> 00:01:04,000\n"), 'to srt');
check(parse_subtitles("WEBVTT\n\nNOTE x\n\ncue1\n00:05.000 --> 00:06.000 align:start\nhi\n") === [[5000, 6000, 'hi']], 'vtt short timestamps and settings');
check(parse_subtitles('not a subtitle') === [], 'garbage');

// 文字編碼
check(text_to_utf8(mb_convert_encoding('繁體中文', 'BIG-5', 'UTF-8')) === '繁體中文', 'big5 to utf8');
check(text_to_utf8("\xEF\xBB\xBFabc") === 'abc', 'strip bom');

// 版別與版本顯示（第 19 章）
check(channel_for_os('Windows 6.1.7601') === 'win7', 'os win7');
check(channel_for_os('Windows 10.0.19045') === 'win10', 'os win10');
check(channel_for_os('Windows 10.0.22631') === 'win11', 'os win11 by build');
check(channel_for_os('Ubuntu 24.04.1 LTS') === 'ubuntu', 'os ubuntu');
check(display_version('1.3.0') === 'V1.3' && display_version('1.3.2') === 'V1.3.2' && display_version('1.0.0-dev.46') === 'V1.0.0-dev.46', 'display version');

echo "$pass rule checks passed\n";
