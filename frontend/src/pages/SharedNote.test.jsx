import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import SharedNote from './SharedNote.jsx';
import { LangProvider } from '../i18n.jsx';
import { api } from '../api.js';

vi.mock('../api.js', () => ({
  api: { getPublicNote: vi.fn() },
}));

function renderAt(token) {
  return render(
    <LangProvider>
      <MemoryRouter initialEntries={[`/share/${token}`]}>
        <Routes>
          <Route path="/share/:token" element={<SharedNote />} />
        </Routes>
      </MemoryRouter>
    </LangProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

describe('SharedNote', () => {
  it('renders the fetched note read-only', async () => {
    api.getPublicNote.mockResolvedValue({
      title: 'Public title',
      content: '**bold** body',
      tags: [],
      note_date: '2026-06-11',
      updated_at: '2026-06-11T10:00:00Z',
    });
    renderAt('tok123');

    expect(await screen.findByText('Public title')).toBeInTheDocument();
    expect(screen.getByText('bold')).toBeInTheDocument();
    expect(screen.getByText('Read-only shared note')).toBeInTheDocument();
    expect(api.getPublicNote).toHaveBeenCalledWith('tok123');
  });

  it('shows the invalid-link message on 404', async () => {
    api.getPublicNote.mockRejectedValue(new Error('Not found'));
    renderAt('dead');

    expect(
      await screen.findByText('This link is invalid or has been revoked.')
    ).toBeInTheDocument();
  });
});
