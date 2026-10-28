import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  applyDensity,
  applyMode,
  Density,
  Mode,
} from '@cloudscape-design/global-styles';
import '@cloudscape-design/global-styles/index.css';
import App from './App';
import './styles.css';

applyMode(Mode.Dark);
applyDensity(Density.Compact);

const root = document.getElementById('root');

if (!root) {
  throw new Error('LambdaOps root element is missing');
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
