import React from 'react';
import ReactDOM from 'react-dom/client';
import 'bootstrap/dist/css/bootstrap.min.css';
import './shared/styles/tokens.css';
import './shared/styles/base.css';
import { App } from './app/App';
const root = document.getElementById('root');
if (!root) throw new Error('Root element is missing');
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
