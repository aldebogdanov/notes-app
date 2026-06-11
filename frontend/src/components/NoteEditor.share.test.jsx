import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import NoteEditor from './NoteEditor.jsx';
import { LangProvider } from '../i18n.jsx';

const NOTE = {
  id: 7,
  title: 'T',
  content: 'c',
  tags: [],
  note_date: null,
  pinned_at: null,
  archived_at: null,
  share_token: null,
};

function renderEditor(note, handlers = {}) {
  return render(
    <LangProvider>
      <NoteEditor note={note} onSave={() => {}} {...handlers} />
    </LangProvider>
  );
}

describe('NoteEditor sharing & export', () => {
  it('share button calls onShare to publish', async () => {
    const onShare = vi.fn();
    renderEditor(NOTE, { onShare });

    await userEvent.click(screen.getByText('Share'));
    expect(onShare).toHaveBeenCalledWith(NOTE, true);
  });

  it('shared note shows the public URL and the unshare button', async () => {
    const shared = { ...NOTE, share_token: 'tok123' };
    const onShare = vi.fn();
    renderEditor(shared, { onShare });

    expect(screen.getByDisplayValue(/\/share\/tok123$/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('Unshare'));
    expect(onShare).toHaveBeenCalledWith(shared, false);
  });

  it('export button calls onExport with the note', async () => {
    const onExport = vi.fn();
    renderEditor(NOTE, { onExport });

    await userEvent.click(screen.getByText('Export .md'));
    expect(onExport).toHaveBeenCalledWith(NOTE);
  });
});
