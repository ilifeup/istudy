/** Client App 畫面入口（四個版別同一份畫面；版別由主程序的 build-info.json 決定）。 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from '../../../packages/ui/src/App';
import { appInfo } from '../../../packages/ui/src/bridge';

void appInfo().then((info) => {
  createRoot(document.getElementById('root')!).render(<App info={info} />);
});
