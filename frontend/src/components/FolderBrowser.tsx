import { useState, useEffect, useCallback } from 'react';
import { ArrowUp, Folder, HardDrive } from 'lucide-react';
import { browseFolders, type BrowseEntry } from '../lib/api';

/** In-app folder browser: no OS native picker returns a real filesystem path
 *  from a plain browser tab (the File System Access API hides it by design),
 *  so this walks the filesystem through the server instead. Shared between
 *  Settings > Folder Access and the chat input's "+" folder-grant popover. */
export function FolderBrowser({
  onSelect,
  onCancel,
}: {
  onSelect: (path: string) => void;
  onCancel: () => void;
}) {
  const [path, setPath] = useState('');
  const [parent, setParent] = useState<string | null>(null);
  const [folders, setFolders] = useState<BrowseEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const go = useCallback((target: string) => {
    setLoading(true);
    setError(null);
    browseFolders(target)
      .then((r) => {
        setPath(r.path);
        setParent(r.parent);
        setFolders(r.folders);
      })
      .catch((e) => setError(e?.message || 'Failed to browse'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { go(''); }, [go]);

  return (
    <div
      className="rounded-lg p-3"
      style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}
    >
      <div className="flex items-center gap-2 mb-2">
        <button
          type="button"
          onClick={() => go(parent ?? '')}
          disabled={parent === null}
          className="p-1 rounded cursor-pointer disabled:opacity-30"
          style={{ color: 'var(--color-text-secondary)', background: 'transparent', border: 'none' }}
          title="Up one level"
        >
          <ArrowUp size={14} />
        </button>
        <span
          className="text-xs truncate flex-1"
          style={{ color: 'var(--color-text-secondary)' }}
          title={path || 'Drives'}
        >
          {path || 'Drives'}
        </span>
      </div>

      <div className="max-h-48 overflow-y-auto space-y-0.5 mb-2">
        {loading && (
          <p className="text-xs px-2 py-1" style={{ color: 'var(--color-text-tertiary)' }}>Loading…</p>
        )}
        {!loading && error && (
          <p className="text-xs px-2 py-1" style={{ color: 'var(--color-error, #ef4444)' }}>{error}</p>
        )}
        {!loading && !error && folders.length === 0 && (
          <p className="text-xs px-2 py-1" style={{ color: 'var(--color-text-tertiary)' }}>No subfolders</p>
        )}
        {!loading && !error && folders.map((f) => (
          <button
            key={f.path}
            type="button"
            onClick={() => go(f.path)}
            className="w-full flex items-center gap-2 text-sm px-2 py-1.5 rounded cursor-pointer text-left"
            style={{ color: 'var(--color-text)', background: 'transparent', border: 'none' }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-bg-secondary)')}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            {path ? <Folder size={14} style={{ color: 'var(--color-accent)', flexShrink: 0 }} /> : <HardDrive size={14} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />}
            <span className="truncate">{f.name}</span>
          </button>
        ))}
      </div>

      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={onCancel}
          className="text-xs px-3 py-1.5 rounded-lg cursor-pointer"
          style={{ color: 'var(--color-text-secondary)', background: 'transparent', border: '1px solid var(--color-border)' }}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => path && onSelect(path)}
          disabled={!path}
          className="text-xs px-3 py-1.5 rounded-lg cursor-pointer disabled:opacity-50"
          style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)', border: 'none' }}
        >
          Use this folder
        </button>
      </div>
    </div>
  );
}
