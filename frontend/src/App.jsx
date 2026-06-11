import { useCallback, useRef, useState } from 'react';
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom';
import Login from './pages/Login.jsx';
import Register from './pages/Register.jsx';
import Notes from './pages/Notes.jsx';
import Calendar from './pages/Calendar.jsx';
import Settings from './pages/Settings.jsx';
import SharedNote from './pages/SharedNote.jsx';
import ThemeToggle from './components/ThemeToggle.jsx';
import LanguageToggle from './components/LanguageToggle.jsx';
import HelpOverlay from './components/HelpOverlay.jsx';
import { clearToken, isAuthenticated } from './auth.js';
import { useLang } from './i18n.jsx';
import { useShortcuts } from './hooks/useShortcuts.js';

function RequireAuth({ children }) {
  return isAuthenticated() ? children : <Navigate to="/login" replace />;
}

function Header({ onShowHelp }) {
  const navigate = useNavigate();
  const { t } = useLang();
  const linkClass = ({ isActive }) => `nav-link${isActive ? ' active' : ''}`;

  const brand = (
    <div className="brand">
      <span className="brand-mark">N</span>
      <span>{t('brand')}</span>
    </div>
  );

  if (!isAuthenticated()) {
    return (
      <nav className="nav">
        {brand}
        <div className="nav-spacer" />
        <LanguageToggle />
        <ThemeToggle />
      </nav>
    );
  }

  const logout = () => {
    clearToken();
    navigate('/login', { replace: true });
  };

  return (
    <nav className="nav">
      {brand}
      <NavLink to="/notes" className={linkClass}>{t('nav.notes')}</NavLink>
      <NavLink to="/calendar" className={linkClass}>{t('nav.calendar')}</NavLink>
      <NavLink to="/settings" className={linkClass}>{t('nav.settings')}</NavLink>
      <div className="nav-spacer" />
      <button className="link-button help-btn" onClick={onShowHelp} title="?" aria-label="Help">?</button>
      <LanguageToggle />
      <ThemeToggle />
      <button className="link-button" onClick={logout}>{t('nav.logout')}</button>
    </nav>
  );
}

export default function App() {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const pendingActionRef = useRef({});

  const setPendingAction = useCallback((name, handler) => {
    pendingActionRef.current[name] = handler;
  }, []);

  const handlers = {
    onNewNote: () => {
      navigate('/notes');
      setTimeout(() => pendingActionRef.current.newNote?.(), 30);
    },
    onFocusSearch: () => {
      navigate('/notes');
      setTimeout(() => pendingActionRef.current.focusSearch?.(), 30);
    },
    onSave: () => pendingActionRef.current.save?.(),
    onShowHelp: () => setHelpOpen(true),
    onEscape: () => setHelpOpen(false),
  };

  useShortcuts(handlers);

  return (
    <div className="app">
      <Header onShowHelp={() => setHelpOpen(true)} />
      <main>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/share/:token" element={<SharedNote />} />
          <Route path="/notes" element={<RequireAuth><Notes registerAction={setPendingAction} /></RequireAuth>} />
          <Route path="/calendar" element={<RequireAuth><Calendar /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
          <Route path="*" element={<Navigate to={isAuthenticated() ? '/notes' : '/login'} replace />} />
        </Routes>
      </main>
      <HelpOverlay open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
