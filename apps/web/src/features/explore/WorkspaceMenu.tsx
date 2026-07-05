/**
 * Header dropdown to save / update / load / delete workspaces — named,
 * restorable snapshots of the whole Explore view. Capture and restore go
 * through `lib/workspace`, so this component only orchestrates UI + mutations.
 */
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import {
  useDeleteWorkspace,
  useSaveWorkspace,
  useUpdateWorkspace,
  useWorkspaces,
} from "../../api/queries";
import type { Workspace } from "../../api/types";
import { applyWorkspace, captureWorkspace } from "../../lib/workspace";
import { useWorkspaceStore } from "../../stores/workspaceStore";

function alertError(err: unknown, fallback: string): void {
  window.alert(err instanceof ApiError ? err.detail : fallback);
}

export function WorkspaceMenu() {
  const { data: workspaces } = useWorkspaces();
  const saveWorkspace = useSaveWorkspace();
  const updateWorkspace = useUpdateWorkspace();
  const deleteWorkspace = useDeleteWorkspace();
  const currentId = useWorkspaceStore((s) => s.currentId);
  const currentName = useWorkspaceStore((s) => s.currentName);

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Close on an outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleSaveAs = () => {
    const name = window.prompt("Save workspace as")?.trim();
    if (!name) return;
    saveWorkspace.mutate(
      { name, state: captureWorkspace() },
      {
        onSuccess: (ws) => {
          useWorkspaceStore.getState().setCurrent(ws.id, ws.name);
          setOpen(false);
        },
        onError: (err) => alertError(err, "Could not save the workspace."),
      },
    );
  };

  const handleUpdate = () => {
    if (currentId == null || currentName == null) return;
    updateWorkspace.mutate(
      { id: currentId, body: { name: currentName, state: captureWorkspace() } },
      {
        onSuccess: () => setOpen(false),
        onError: (err) => alertError(err, "Could not update the workspace."),
      },
    );
  };

  const handleLoad = (ws: Workspace) => {
    applyWorkspace(ws.state);
    useWorkspaceStore.getState().setCurrent(ws.id, ws.name);
    setOpen(false);
  };

  const handleDelete = (ws: Workspace) => {
    deleteWorkspace.mutate(ws.id, {
      onSuccess: () => {
        if (useWorkspaceStore.getState().currentId === ws.id) {
          useWorkspaceStore.getState().clear();
        }
      },
      onError: (err) => alertError(err, "Could not delete the workspace."),
    });
  };

  const list = workspaces ?? [];

  return (
    <div className="workspace-menu" ref={rootRef}>
      <button
        className={open ? "active" : ""}
        title="Save, load, and manage workspaces"
        onClick={() => setOpen((v) => !v)}
      >
        ▤ Workspaces{currentName ? ` · ${currentName}` : ""} ▾
      </button>
      {open ? (
        <div className="workspace-menu-panel">
          <button className="workspace-action" onClick={handleSaveAs}>
            Save as…
          </button>
          <button
            className="workspace-action"
            disabled={currentId == null || updateWorkspace.isPending}
            title={currentId == null ? "Load or save a workspace first" : undefined}
            onClick={handleUpdate}
          >
            Update{currentName ? ` “${currentName}”` : ""}
          </button>
          <div className="workspace-menu-sep" />
          {list.length === 0 ? (
            <p className="muted workspace-empty">No saved workspaces yet.</p>
          ) : (
            <ul className="workspace-list">
              {list.map((ws) => (
                <li
                  key={ws.id}
                  className={ws.id === currentId ? "workspace-item current" : "workspace-item"}
                >
                  <button
                    className="workspace-load"
                    title="Load this workspace"
                    onClick={() => handleLoad(ws)}
                  >
                    {ws.name}
                  </button>
                  <button
                    className="icon danger"
                    title={`Delete “${ws.name}”`}
                    onClick={() => handleDelete(ws)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
