import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import * as React from 'react';
import { SubmitPage } from '../pages/Submit/SubmitPage';
import * as client from '../api/client';

let ctxUppy: any = null;

/**
 * The SPECIFIC Uppy react layer is replaced with a thin reactive fake backed by
 * the REAL Uppy core instance (file selection state, restrictions, remove all
 * live). The browser-level picker path is covered by Playwright, not here.
 */
vi.mock('@uppy/react', () => ({
  Dashboard: () => null,
  UppyContextProvider: ({ uppy, children }: any) => {
    ctxUppy = uppy;
    return children;
  },
  useFileInput: () => ({
    getInputProps: () => ({
      type: 'file',
      multiple: true,
      onChange: (event: any) => {
        const selected: File[] = Array.from(event.target.files ?? []);
        for (const file of selected) {
          ctxUppy.addFile({ data: file, name: file.name, type: file.type });
        }
      },
    }),
    getButtonProps: () => ({ type: 'button', onClick: () => {} }),
  }),
  useUppyState: (uppy: any, selector: any) => {
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
    fireEvent.change(screen.getByPlaceholderText('标签（必填，如 #示例 #壁纸）'), {
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
    fireEvent.change(screen.getByPlaceholderText('标签（必填，如 #示例 #壁纸）'), {
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