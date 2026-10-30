/** Stable page IDs. Never renumber or recycle IDs after publication. */
export type PreviewEntry = { id: string; route: string; label: string; module: string; source: string; minRole?: 'admin' | 'super'; serverOnly?: boolean; detail?: boolean };
export const PREVIEW_PAGES: readonly PreviewEntry[] = [
  { id:'C-COURSE-01',route:'courses',label:'課程目錄',module:'COURSE',source:'packages/ui/src/pages/Courses.tsx' },
  { id:'C-COURSE-02',route:'player',label:'影片播放',module:'COURSE',source:'packages/ui/src/pages/Player.tsx',detail:true },
  { id:'C-COURSE-03',route:'doc',label:'文件檢視',module:'COURSE',source:'packages/ui/src/pages/DocViewer.tsx',detail:true },
  { id:'C-HISTORY-01',route:'history',label:'瀏覽記錄',module:'HISTORY',source:'packages/ui/src/pages/History.tsx' },
  { id:'C-BOOKMARK-01',route:'bookmarks',label:'書籤',module:'BOOKMARK',source:'packages/ui/src/pages/Bookmarks.tsx' },
  { id:'C-PROFILE-01',route:'profile',label:'個人資料',module:'PROFILE',source:'packages/ui/src/pages/Profile.tsx' },
  { id:'C-SEARCH-01',route:'search',label:'搜尋',module:'SEARCH',source:'packages/ui/src/pages/Search.tsx' },
  { id:'C-FEEDBACK-01',route:'feedback',label:'問題回報',module:'FEEDBACK',source:'packages/ui/src/pages/Feedback.tsx' },
  { id:'C-ACCOUNT-01',route:'accounts',label:'帳號管理',module:'ACCOUNT',source:'packages/ui/src/admin/Accounts.tsx',minRole:'admin' },
  { id:'C-GROUP-01',route:'groups',label:'課程群組',module:'GROUP',source:'packages/ui/src/admin/Groups.tsx',minRole:'admin' },
  { id:'C-AUDIT-01',route:'audit',label:'使用記錄',module:'AUDIT',source:'packages/ui/src/admin/Audit.tsx',minRole:'admin' },
  { id:'C-WOL-01',route:'wol',label:'WOL 管理',module:'WOL',source:'packages/ui/src/admin/Wol.tsx',minRole:'admin' },
  { id:'C-TAG-01',route:'tagpolicy',label:'Tag 設定',module:'TAG',source:'packages/ui/src/admin/TagPolicy.tsx',minRole:'super' },
  { id:'C-PC-01',route:'pcinfo',label:'PC 資訊',module:'PC',source:'packages/ui/src/admin/PcInfo.tsx',minRole:'super' },
  { id:'C-EMAIL-01',route:'email',label:'Email 報告',module:'EMAIL',source:'packages/ui/src/admin/EmailReport.tsx',minRole:'super' },
  { id:'S-SYSTEM-01',route:'srv-overview',label:'Server 總覽',module:'SYSTEM',source:'apps/server/src/pages/Overview.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SYSTEM-02',route:'srv-setup',label:'首次設定精靈',module:'SYSTEM',source:'apps/server/src/pages/SetupWizard.tsx',serverOnly:true,minRole:'super',detail:true },
  { id:'S-FOLDER-01',route:'srv-folders',label:'教材資料夾',module:'FOLDER',source:'apps/server/src/pages/Folders.tsx',serverOnly:true,minRole:'super' },
  { id:'S-MEDIA-01',route:'srv-media',label:'教材列表',module:'MEDIA',source:'apps/server/src/pages/MediaList.tsx',serverOnly:true,minRole:'super' },
  { id:'S-MEDIA-02',route:'srv-convert',label:'轉檔佇列',module:'MEDIA',source:'apps/server/src/pages/Conversions.tsx',serverOnly:true,minRole:'super' },
  { id:'S-MEDIA-03',route:'srv-originals',label:'原始檔案',module:'MEDIA',source:'apps/server/src/pages/Originals.tsx',serverOnly:true,minRole:'super' },
  { id:'S-MEDIA-04',route:'srv-subtitles',label:'字幕',module:'MEDIA',source:'apps/server/src/pages/Queues.tsx',serverOnly:true,minRole:'super' },
  { id:'S-MEDIA-05',route:'srv-docs',label:'文件',module:'MEDIA',source:'apps/server/src/pages/Queues.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SCHEDULE-01',route:'srv-schedule',label:'排程',module:'SCHEDULE',source:'apps/server/src/pages/Settings.tsx',serverOnly:true,minRole:'super' },
  { id:'S-OUTGO-01',route:'srv-outgo',label:'Outgo',module:'OUTGO',source:'apps/server/src/pages/Outgo.tsx',serverOnly:true,minRole:'super' },
  { id:'S-REPORT-01',route:'srv-reports',label:'報表',module:'REPORT',source:'apps/server/src/pages/Reports.tsx',serverOnly:true,minRole:'super' },
  { id:'S-CLIENT-01',route:'srv-clients',label:'Client 管理',module:'CLIENT',source:'apps/server/src/pages/Clients.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SYSTEM-03',route:'srv-ie',label:'匯入匯出',module:'SYSTEM',source:'apps/server/src/pages/Settings.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SYSTEM-04',route:'srv-login',label:'登入設定',module:'SYSTEM',source:'apps/server/src/pages/Settings.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SYSTEM-05',route:'srv-check',label:'系統檢查',module:'SYSTEM',source:'apps/server/src/pages/SystemCheck.tsx',serverOnly:true,minRole:'super' },
  { id:'S-BACKUP-01',route:'srv-backup',label:'備份',module:'BACKUP',source:'apps/server/src/pages/BackupsLogs.tsx',serverOnly:true,minRole:'super' },
  { id:'S-BACKUP-02',route:'srv-logs',label:'Log',module:'BACKUP',source:'apps/server/src/pages/BackupsLogs.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SYSTEM-06',route:'srv-uem',label:'UEM',module:'SYSTEM',source:'apps/server/src/pages/Uem.tsx',serverOnly:true,minRole:'super' },
  { id:'S-SYSTEM-07',route:'srv-host',label:'主機',module:'SYSTEM',source:'apps/server/src/pages/Host.tsx',serverOnly:true,minRole:'super' }
];
export function previewByRoute(route:string, server:boolean):PreviewEntry|undefined {
  return PREVIEW_PAGES.find(x=>x.route===route && (server ? x.serverOnly : !x.serverOnly))
    ?? (server ? PREVIEW_PAGES.find(x=>x.route===route && !x.serverOnly) : undefined);
}
export function previewAllowed(p:PreviewEntry,role:string,server:boolean):boolean {
  if(p.serverOnly && !server) return false;
  if(server && role !== 'super') return false; // Server App is Super-only today
  if(p.minRole==='super' && role!=='super') return false;
  if(p.minRole==='admin' && role!=='admin' && role!=='super') return false;
  return true;
}
