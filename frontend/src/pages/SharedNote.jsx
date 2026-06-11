import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { api } from '../api.js';
import { useLang } from '../i18n.jsx';

export default function SharedNote() {
  const { token } = useParams();
  const { t } = useLang();
  const [note, setNote] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    api
      .getPublicNote(token)
      .then(setNote)
      .catch(() => setError(true));
  }, [token]);

  if (error) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">🔗</div>
        <h3>{t('share.invalid')}</h3>
      </div>
    );
  }
  if (!note) return null;

  return (
    <div className="shared-note content-pane">
      <p className="settings-hint">{t('share.readonly')}</p>
      <h1>{note.title}</h1>
      {note.note_date && <p>📅 {note.note_date}</p>}
      <div className="preview">
        <ReactMarkdown>{note.content}</ReactMarkdown>
      </div>
    </div>
  );
}
