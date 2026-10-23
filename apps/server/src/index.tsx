/**
 * Server App 畫面入口：共用 Client App 全部畫面（packages/ui），另加「Server 管理」分頁、初始部署與首次設定精靈。
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { api } from '../../../packages/ui/src/api';
import { App, ServerExtension } from '../../../packages/ui/src/App';
import { appInfo } from '../../../packages/ui/src/bridge';
import { setSetupCache, SrvLayout } from './srv';
import { BootstrapGate } from './pages/Bootstrap';
import { OverviewPage } from './pages/Overview';
import { FoldersPage } from './pages/Folders';
import { MediaListPage } from './pages/MediaList';
import { ConversionsPage } from './pages/Conversions';
import { ImportExportPage, LoginConfigPage, SchedulePage } from './pages/Settings';
import { OriginalsPage } from './pages/Originals';
import { OutgoPage } from './pages/Outgo';
import { ReportsPage } from './pages/Reports';
import { ClientsPage } from './pages/Clients';
import { DocumentsPage, SubtitlesPage } from './pages/Queues';
import { SystemCheckPage } from './pages/SystemCheck';
import { BackupsPage, LogsPage } from './pages/BackupsLogs';
import { HostPage } from './pages/Host';
import { UemPage } from './pages/Uem';
import { SetupWizardPage } from './pages/SetupWizard';

const RAW: Record<string, () => any> = {
  'srv-overview': OverviewPage,
  'srv-folders': FoldersPage,
  'srv-media': MediaListPage,
  'srv-convert': ConversionsPage,
  'srv-schedule': SchedulePage,
  'srv-originals': OriginalsPage,
  'srv-subtitles': SubtitlesPage,
  'srv-docs': DocumentsPage,
  'srv-outgo': OutgoPage,
  'srv-reports': ReportsPage,
  'srv-clients': ClientsPage,
  'srv-ie': ImportExportPage,
  'srv-login': LoginConfigPage,
  'srv-check': SystemCheckPage,
  'srv-backup': BackupsPage,
  'srv-logs': LogsPage,
  'srv-uem': UemPage,
  'srv-host': HostPage,
  'srv-setup': SetupWizardPage,
};

const pages: ServerExtension['pages'] = {};
for (const [id, P] of Object.entries(RAW)) {
  pages[id] = () => <SrvLayout page={id}><P /></SrvLayout>;
}

const server: ServerExtension = {
  pages,
  defaultPage: 'srv-overview',
  PreLogin: BootstrapGate,
  afterLogin: async (account, go) => {
    if (account.role !== 'super') return;
    setSetupCache(null);
    // Super 每次登入觸發一次 Server 診斷；失敗不影響登入（第 10 章）
    api.post('/api/admin/diagnostics', {}).catch(() => undefined);
    try {
      const s = await api.get<{ setup: { completed: boolean; step: number } }>('/api/admin/setup');
      setSetupCache({ completed: !!s.setup.completed, step: s.setup.step || 1 });
      if (!s.setup.completed) go('srv-setup');
    } catch {
      /* 精靈狀態讀不到時留在總覽 */
    }
  },
};

void appInfo().then((info) => {
  createRoot(document.getElementById('root')!).render(<App info={info} server={server} />);
});
