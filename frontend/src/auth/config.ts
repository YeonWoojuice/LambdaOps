export type FrontendConfig = { mode: 'demo' } | LiveConfig;

export type LiveConfig = {
  mode: 'live';
  apiBaseUrl: string;
  issuer: string;
  clientId: string;
  redirectUri: string;
};

type PublicEnv = Record<string, string | undefined>;

function httpsUrl(value: string | undefined, name: string): string {
  if (!value?.trim()) throw new Error(`${name} is required in live mode`);
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error(`${name} must be an HTTPS URL`);
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must be an HTTPS URL without credentials or query values`);
  }
  return url.toString();
}

export function readFrontendConfig(env: PublicEnv, origin: string): FrontendConfig {
  const mode = env.VITE_DATA_MODE || 'demo';
  if (mode === 'demo') return { mode };
  if (mode !== 'live') throw new Error('VITE_DATA_MODE must be demo or live');

  // The existing operator client has only this exact callback. The Slash web client uses 5173.
  if (origin !== 'http://localhost:8765') {
    throw new Error('Live mode must run at http://localhost:8765');
  }
  const clientId = env.VITE_COGNITO_CLIENT_ID?.trim();
  if (!clientId || !/^[A-Za-z0-9]+$/.test(clientId)) {
    throw new Error('VITE_COGNITO_CLIENT_ID is required in live mode');
  }
  return {
    mode,
    apiBaseUrl: httpsUrl(env.VITE_INCIDENT_API_BASE_URL, 'VITE_INCIDENT_API_BASE_URL'),
    issuer: httpsUrl(env.VITE_COGNITO_ISSUER, 'VITE_COGNITO_ISSUER'),
    clientId,
    redirectUri: `${origin}/callback`,
  };
}
