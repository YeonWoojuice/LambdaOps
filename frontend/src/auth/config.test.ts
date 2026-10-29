import { describe, expect, it } from 'vitest';
import { readFrontendConfig } from './config';

const origin = 'http://localhost:8765';
const liveEnv = {
  VITE_DATA_MODE: 'live',
  VITE_INCIDENT_API_BASE_URL: 'https://example.execute-api.ap-northeast-2.amazonaws.com',
  VITE_COGNITO_ISSUER: 'https://cognito-idp.ap-northeast-2.amazonaws.com/ap-northeast-2_example',
  VITE_COGNITO_CLIENT_ID: 'exampleclient123',
};

describe('frontend data mode', () => {
  it('keeps the existing local demo explicit and independent of AWS configuration', () => {
    expect(readFrontendConfig({}, origin)).toEqual({ mode: 'demo' });
    expect(readFrontendConfig({ VITE_DATA_MODE: 'demo' }, origin)).toEqual({ mode: 'demo' });
  });

  it('requires the registered operator callback and public live configuration', () => {
    expect(readFrontendConfig(liveEnv, origin)).toMatchObject({
      mode: 'live',
      redirectUri: 'http://localhost:8765/callback',
      clientId: 'exampleclient123',
    });
    expect(() => readFrontendConfig(liveEnv, 'http://localhost:5173')).toThrow();
    expect(() => readFrontendConfig({ ...liveEnv, VITE_COGNITO_CLIENT_ID: '' }, origin)).toThrow();
    expect(() => readFrontendConfig({ ...liveEnv, VITE_INCIDENT_API_BASE_URL: 'http://example.com' }, origin)).toThrow();
    expect(() => readFrontendConfig({ ...liveEnv, VITE_DATA_MODE: 'lvie' }, origin)).toThrow();
  });
});
