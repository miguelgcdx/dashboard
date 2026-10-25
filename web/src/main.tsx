import { createRoot } from 'react-dom/client';
import { App } from './App';
import { parseSnapshot } from './data';
import '@radix-ui/themes/styles.css';
import './style.css';

const root = document.getElementById('root');
if (root) {
  try {
    const value: unknown = JSON.parse(document.getElementById('usage-data')?.textContent ?? 'null');
    const snapshot = parseSnapshot(value);
    createRoot(root).render(<App data={snapshot} />);
  } catch {
    // Never include raw input or parser errors in the visible failure state.
    root.textContent = 'This snapshot could not be read safely. Regenerate the preview; the legacy dashboard is unchanged.';
    root.setAttribute('role', 'alert');
  }
}
