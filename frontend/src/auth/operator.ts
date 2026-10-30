import { UserManager, WebStorageStateStore } from 'oidc-client-ts';
import type { LiveConfig } from './config';

export function createOperatorManager(config: LiveConfig, storage: Storage): UserManager {
  const sessionStore = new WebStorageStateStore({ store: storage });
  return new UserManager({
    authority: config.issuer,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    response_type: 'code',
    disablePKCE: false,
    scope: 'openid lambdaops/incidents.read',
    userStore: sessionStore,
    stateStore: sessionStore,
    automaticSilentRenew: false,
    monitorSession: false,
    loadUserInfo: false,
    requestTimeoutInSeconds: 10,
  });
}
