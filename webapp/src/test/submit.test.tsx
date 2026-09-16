import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import * as React from 'react';
import { SubmitPage } from '../pages/Submit/SubmitPage';
import * as client from '../api/client';

interface UppyLike {
  addFile: (file: { data: unknown; name: string; type: string }) => void;
  removeFile: (id: string) => void;
  removeFiles: () => void;
  getState: () => { files: Record<string, unknown> };
  on: (event: string, handler: () => void) => void;
  off: (event: string, handler: () => void) => void;
  destroy: () => void;
}

let ctxUppy: UppyLike | null = null;

/**
 * The SPECIFIC Uppy react layer is replaced with a thin reactive fake backed by
 * the REAL Uppy core instance (file selection state, restrictions, remove all
 * live). The browser-level picker path is covered by Playwright, not here.
 */
vi.mock('@uppy/react', () => ({
  Dashboard: () => null,
  UppyContextProvider: ({ uppy, children }: { uppy: UppyLike; children: React.ReactNode }) => {
    ctxUppy = uppy;
    return children;
  },
  useFileInput: () => ({
    getInputProps: () => ({
      type: 'file',
      multiple: true,
      onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
        const selected: File[] = Array.from(event.target.files ?? []);
        for (const file of selected) {
          ctxUppy?.addFile({ data: file, name: file.name, type: file.type });
        }
      },
    }),
    getButtonProps: () => ({ type: 'button', onClick: () => {} }),
  }),
  useUppyState: (uppy: UppyLike, selector: (state: unknown) => unknown) => {
    const [state, setState] = React.useState(() => selector(uppy.getState()));
    React.useEffect(() => {
      const update = () => setState(selector(uppy.getState()));
      update();
      const events = ['file-added', 'file-removed', 'state-update'];
      events.forEach((event) => uppy.on(event, update));
      return () => {
        events.forEach((event) => uppy.off(event, update));
      };
    }, [uppy, selector]);
    return state;
  },
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppRoot>
        <MemoryRouter initialEntries={['/submit']}>
          <SubmitPage />
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

async function addFiles(names: string[]) {
  const input = screen.getByTestId('file-input');
  const files = names.map(
    (name, index) => new File(['x'.repeat(index + 1)], name, { type: 'image/png' }),
  );
  fireEvent.change(input, { target: { files } });
  await waitFor(() => {
    expect(screen.getByTestId('selected-files').textContent).toContain(
      `已选择 ${names.length} 个文件`,
    );
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  ctxUppy = null;
  // jsdom has no createObjectURL; the Submit preview depends on it.
  URL.createObjectURL = vi.fn(() => 'blob:mock');
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  ctxUppy?.destroy?.();
});

describe('SubmitPage (Uppy React integration)', () => {
  it('adds and lists files through real Uppy core state', async () => {
    renderPage();
    await addFiles(['photo.png', 'clip.mp4']);
    expect(screen.getByTestId('selected-files').textContent).toContain('photo.png');
    expect(screen.getByTestId('selected-files').textContent).toContain('clip.mp4');
  });

  it('submits one multipart request with a STABLE idempotency key', async () => {
    vi.spyOn(client, 'hasSession').mockReturnValue(true);
    const call = vi
      .spyOn(client, 'apiFetch')
      .mockResolvedValue({ status: 'pending_review', review_id: 42 });
    renderPage();
    await addFiles(['a.png']);
    fireEvent.change(screen.getByPlaceholderText('标签（必填，可用空格或逗号分隔）'), {
      target: { value: '#test' },
    });
    fireEvent.click(screen.getByTestId('submit'));
    await waitFor(() => expect(call).toHaveBeenCalledTimes(1));
    const [url, init] = call.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/submissions');
    const form = init.body as FormData;
    expect(form.getAll('files')).toHaveLength(1);
    expect(form.get('tags')).toBe('#test');
    const keyFirst = form.get('idempotency_key') as string;
    expect(keyFirst.length).toBeGreaterThan(8);
    // A replay of the same submission session keeps the SAME key.
    fireEvent.click(screen.getByTestId('submit'));
    await waitFor(() => expect(call).toHaveBeenCalledTimes(2));
    const [, init2] = call.mock.calls[1] as [string, RequestInit];
    expect((init2.body as FormData).get('idempotency_key')).toBe(keyFirst);
  });

  it('keeps files and form on failure so the user can retry', async () => {
    vi.spyOn(client, 'hasSession').mockReturnValue(true);
    const call = vi
      .spyOn(client, 'apiFetch')
      .mockRejectedValueOnce(new Error('网络错误'))
      .mockResolvedValueOnce({ status: 'pending_review', review_id: 43 });
    renderPage();
    await addFiles(['b.png']);
    fireEvent.change(screen.getByPlaceholderText('标签（必填，可用空格或逗号分隔）'), {
      target: { value: '#retry' },
    });
    fireEvent.click(screen.getByTestId('submit'));
    await waitFor(() =>
      expect(screen.getByText(/提交失败/)).toBeTruthy(),
    );
    // Files are still selected after the failed attempt.
    expect(screen.getByTestId('selected-files').textContent).toContain('b.png');
    // Retry succeeds with the same idempotency key.
    fireEvent.click(screen.getByTestId('submit'));
    await waitFor(() => expect(call).toHaveBeenCalledTimes(2));
  });

  it('refuses submit without files or tags', async () => {
    const call = vi.spyOn(client, 'apiFetch');
    renderPage();
    fireEvent.click(screen.getByTestId('submit'));
    expect(await screen.findByText('请先添加至少一个文件')).toBeTruthy();
    expect(call).not.toHaveBeenCalled();
  });
});
describe('SubmitPage tag UX + preview (tag hint, real media, submitter)', () => {
  it('shows the separator hint under the tag input', () => {
    renderPage();
    const hint = screen.getByTestId('tag-hint');
    expect(hint.textContent).toContain('空格');
    expect(hint.textContent).toContain('英文逗号');
    expect(hint.textContent).toContain('中文逗号');
    expect(hint.textContent).toContain('ボテ腹, R18 pregnancy');
  });

  it('preview renders the real selected image (blob URL), submitter and caption', async () => {
    client.setSession('tok', 3600, { telegram_user_id: 42, username: 'devuser' });
    vi.spyOn(client, 'apiFetch').mockResolvedValue({ caption: '🏷 #e2e\n投稿人：devuser' });
    renderPage();
      await addFiles(['photo.png']);
      fireEvent.change(screen.getByPlaceholderText('标签（必填，可用空格或逗号分隔）'), {
        target: { value: 'ボテ腹, R18' },
      });
      fireEvent.click(screen.getByRole('button', { name: '预览投稿' }));
      await waitFor(() => expect(screen.getByTestId('preview-panel')).toBeTruthy());
      // Real image preview: an <img> backed by a blob object URL.
      const img = screen.getByTestId('preview-media-0').querySelector('img') as HTMLImageElement;
      expect(img).toBeTruthy();
      expect(img.getAttribute('src')).toBe('blob:mock');
      // Friendly submitter: @username, never the raw ID.
      expect(screen.getByTestId('preview-submitter').textContent).toContain('@devuser');
      expect(screen.getByTestId('preview-panel').textContent).toContain('🏷 ボテ腹, R18');
      // Server caption is rendered from the shared formatter.
      await waitFor(() => expect(screen.getByTestId('preview-caption').textContent).toContain('投稿人：devuser'));
  });

  it('back returns to the form and removed files vanish from the preview', async () => {
    client.setSession('tok', 3600, { telegram_user_id: 42 });
    vi.spyOn(client, 'apiFetch').mockResolvedValue({ caption: '' });
    renderPage();
      await addFiles(['a.png', 'b.png']);
      fireEvent.change(screen.getByPlaceholderText('标签（必填，可用空格或逗号分隔）'), {
        target: { value: '#x' },
      });
      fireEvent.click(screen.getByRole('button', { name: '预览投稿' }));
      await waitFor(() => expect(screen.getByTestId('preview-panel')).toBeTruthy());
      expect(screen.getByTestId('preview-media-0')).toBeTruthy();
      expect(screen.getByTestId('preview-media-1')).toBeTruthy();
      // Back returns to the form (side-effect free).
      fireEvent.click(screen.getByTestId('preview-back'));
      await waitFor(() => expect(screen.queryByTestId('preview-panel')).toBeNull());
      // Remove one file through Uppy's own state: preview then shows 1 media.
      const ids = Object.keys(ctxUppy!.getState().files);
      ctxUppy!.removeFile(ids[1]);
      await waitFor(() =>
        expect(screen.getByTestId('selected-files').textContent).toContain('已选择 1 个文件'),
      );
      fireEvent.click(screen.getByRole('button', { name: '预览投稿' }));
      await waitFor(() => expect(screen.getByTestId('preview-media-0')).toBeTruthy());
      expect(screen.queryByTestId('preview-media-1')).toBeNull();
  });
});
