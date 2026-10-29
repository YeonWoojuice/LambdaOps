import { StrictMode } from 'react';
import type { ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  applyDensity,
  applyMode,
  Density,
  Mode,
} from '@cloudscape-design/global-styles';
import '@cloudscape-design/global-styles/index.css';
import App from './App';
import { IncidentApiClient } from './data/client';
import { readFrontendConfig } from './auth/config';
import { createOperatorManager } from './auth/operator';
import './styles.css';

applyMode(Mode.Dark);
applyDensity(Density.Compact);

const root = document.getElementById('root');

if (!root) {
  throw new Error('LambdaOps root element is missing');
}

const appRoot = createRoot(root);
function render(content: ReactNode) {
  appRoot.render(<StrictMode>{content}</StrictMode>);
}

async function start() {
  const config = readFrontendConfig(import.meta.env, window.location.origin);
  if (config.mode === 'demo') {
    if (window.location.pathname === '/callback') {
      window.history.replaceState(null, '', '/#/overview');
      throw new Error('OAuth callback requires live mode');
    }
    render(<App />);
    return;
  }

  render(<main role="status">로그인 상태를 확인하는 중입니다.</main>);
  const manager = createOperatorManager(config, window.sessionStorage);
  let user;
  if (window.location.pathname === '/callback') {
    try {
      user = await manager.signinRedirectCallback();
    } finally {
      // The authorization code and state must not remain in browser history.
      window.history.replaceState(null, '', '/#/overview');
    }
  } else {
    user = await manager.getUser();
  }
  if (!user || user.expired || !user.access_token) {
    await manager.removeUser();
    await manager.signinRedirect();
    return;
  }

  const incidentSource = new IncidentApiClient({
    baseUrl: config.apiBaseUrl,
    tokenProvider: async () => {
      const current = await manager.getUser();
      return current && !current.expired ? current.access_token : null;
    },
    fetchImpl: async (input, init) => {
      const response = await fetch(input, init);
      if (response.status === 401) {
        // A rejected token must not remain as an apparently usable browser session.
        await manager.removeUser().catch(() => undefined);
      }
      return response;
    },
  });
  render(<App incidentSource={incidentSource} />);
}

void start().catch(() => {
  // Do not print OAuth errors or URL parameters: they may contain one-time codes.
  if (window.location.pathname === '/callback') {
    window.history.replaceState(null, '', '/#/overview');
  }
  render(<main role="alert">실데이터 로그인 또는 연결 설정을 확인해 주세요.</main>);
});
